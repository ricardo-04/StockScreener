"""Ponto de entrada do StockScreener (CLI).

Exemplos:
  python main.py --universe sp500 --top 20
  python main.py --tickers AAPL,MSFT,ASML --no-ai
  python main.py --refresh-cache
  python main.py --skip-stage momentum
  python main.py --universe sp500 --limit 30      # validação rápida com 30 tickers

Ferramenta de triagem quantitativa; não constitui recomendação de investimento.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import textwrap
import time
from datetime import datetime

import pandas as pd
from dotenv import load_dotenv

from src import __version__
from src.ai.gemini_analyst import REVIEW_FLAG, AiResult, GeminiAnalyst, ai_columns
from src.config import PRESET_NAMES, STAGE_NAMES, ConfigError, apply_preset, load_config, resolve_path
from src.data.cache import DiskCache
from src.data.fetcher import FetchSettings, fetch_many
from src.explain import explain_company, explain_metric_names
from src.funnel.engine import FunnelEngine, diagnose_tickers, format_diagnostics
from src.funnel.stages import Elimination, build_stages
from src.logging_setup import setup_logging
from src.metrics.builder import build_metrics_table
from src.metrics.sector import add_sector_stats
from src.news import NewsItem, fetch_news_for_tickers
from src.report.excel import export_screener_workbook
from src.report.markdown import export_markdown_report
from src.scoring import pillar_metric_names, rank_survivors
from src.universe import UniverseError, get_universe, parse_inline_tickers

log = logging.getLogger("main")

# Métricas com percentil setorial além das usadas nas explicações (regras da Etapa 5)
EXTRA_PERCENTILE_COLUMNS = ("fcf_yield", "ev_to_ebit", "price_to_book", "p_ffo", "dividend_yield", "forward_pe")
TOP_TABLE_COLUMNS = {
    "rank": "#", "ticker": "ticker", "company": "empresa", "sector": "setor", "business_type": "tipo",
    "composite_score": "score", "score_quality": "qual", "score_valuation": "val",
    "score_financial_health": "saúde", "score_momentum": "mom", "news_count": "not.",
    "ai_sentiment": "IA", "ai_review": "revisão",
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Screener de ações em funil: universo → filtros sequenciais → Top N com explicação.",
        epilog="Ferramenta de triagem quantitativa; não constitui recomendação de investimento.",
    )
    parser.add_argument("--universe", help="sp500 | sp400 | sp600 | sp1500 | ficheiro.csv (default: config universe.default)")
    parser.add_argument("--tickers", help="lista inline separada por vírgulas (ex.: AAPL,MSFT); ignora --universe")
    parser.add_argument("--limit", type=int, help="usa apenas os primeiros N tickers do universo (validação rápida)")
    parser.add_argument("--top", type=int, help="dimensão do Top N (default: config scoring.top_n)")
    parser.add_argument("--no-ai", action="store_true", help="desliga a análise com Gemini")
    parser.add_argument("--no-news", action="store_true", help="desliga a recolha de notícias (e, por consequência, a IA)")
    parser.add_argument("--refresh-cache", action="store_true", help="ignora as caches (dados, notícias, IA) e descarrega tudo de novo")
    parser.add_argument("--cache-only", action="store_true", help="modo offline: usa apenas dados já em cache (sem rede)")
    parser.add_argument("--cache-stats", action="store_true", help="mostra estatísticas da cache local e termina")
    parser.add_argument(
        "--clear-cache", nargs="?", const="all", default=None,
        choices=["all", "yf", "news", "ai", "universe"],
        help="limpa entradas da cache local (all, yf, news, ai, universe) e termina",
    )
    parser.add_argument(
        "--preset", choices=list(PRESET_NAMES),
        help=f"aplica um perfil de estratégia pré-definido ({', '.join(PRESET_NAMES)})",
    )
    parser.add_argument(
        "--diagnose", metavar="TICKERS",
        help="diagnostica tickers (separados por vírgulas) em todas as etapas do funil e termina",
    )
    parser.add_argument(
        "--sector", metavar="SETOR",
        help="filtra sobreviventes por setor (case-insensitive; aceita lista separada por vírgulas)",
    )
    parser.add_argument(
        "--min-score", type=float, metavar="SCORE",
        help="filtra o ranking final para composite_score >= SCORE",
    )
    parser.add_argument(
        "--skip-stage", action="append", default=[], metavar="ETAPA",
        help=f"ignora uma etapa do funil ({', '.join(STAGE_NAMES)}); repetível ou separado por vírgulas",
    )
    parser.add_argument("--config", help="caminho para um config.yaml alternativo")
    parser.add_argument("--max-workers", type=int, help="threads de download (default: config data.max_workers)")
    parser.add_argument("--output-dir", help="pasta de output (default: config output.dir)")
    parser.add_argument("--log-level", help="nível de log na consola: DEBUG, INFO, WARNING")
    parser.add_argument("--version", action="version", version=f"StockScreener {__version__}")
    return parser.parse_args(argv)


def parse_skip_stages(values: list[str]) -> list[str]:
    names = [name.strip().lower() for value in values for name in value.split(",") if name.strip()]
    unknown = [name for name in names if name not in STAGE_NAMES]
    if unknown:
        raise ConfigError(
            f"Etapa(s) desconhecida(s) em --skip-stage: {', '.join(unknown)}. Válidas: {', '.join(STAGE_NAMES)}"
        )
    return names


def filter_by_sector(df: pd.DataFrame, sector_spec: str) -> pd.DataFrame:
    """Filtra linhas por setor (case-insensitive, suporta lista separada por vírgulas)."""
    wanted = {part.strip().lower() for part in sector_spec.split(",") if part.strip()}
    if not wanted or df.empty or "sector" not in df.columns:
        return df.copy()
    mask = df["sector"].astype("string").fillna("").str.strip().str.lower().isin(wanted)
    return df.loc[mask].copy()


def filter_by_min_score(ranked: pd.DataFrame, min_score: float) -> pd.DataFrame:
    """Filtra o ranking para empresas com ``composite_score >= min_score``."""
    if ranked.empty or "composite_score" not in ranked.columns:
        return ranked.copy()
    filtered = ranked.loc[ranked["composite_score"] >= float(min_score)].reset_index(drop=True)
    if not filtered.empty and "rank" in filtered.columns:
        filtered["rank"] = range(1, len(filtered) + 1)
    return filtered


def format_cache_stats(stats: dict) -> str:
    """Formata o resumo de estatísticas da cache SQLite para a consola."""
    size_bytes = int(stats.get("size_bytes", 0))
    size_str = f"{size_bytes / (1024 * 1024):.2f} MB" if size_bytes >= 1024 * 1024 else f"{size_bytes / 1024:.1f} KB"
    lines = [
        f"Cache: {stats.get('path', '')}",
        f"Tamanho: {size_str} | Entradas: {int(stats.get('total_entries', 0))}",
    ]
    by_prefix = stats.get("by_prefix") or {}
    if by_prefix:
        breakdown = ", ".join(f"{k}={v}" for k, v in sorted(by_prefix.items()))
        lines.append(f"Por prefixo: {breakdown}")
    return "\n".join(lines)


def _handle_cache_commands(args: argparse.Namespace, cfg: dict, cache: DiskCache) -> int | None:
    """Executa --cache-stats / --clear-cache e devolve 0 se algum foi pedido."""
    if not args.cache_stats and args.clear_cache is None:
        return None
    if args.clear_cache is not None:
        target = args.clear_cache
        deleted = 0
        if target == "all":
            deleted += cache.clear(None)
        elif target == "yf":
            # fetcher.py guarda entradas Yahoo Finance com prefixo "ticker:"
            deleted += cache.clear("ticker") + cache.clear("yf")
        elif target in ("news", "universe"):
            deleted += cache.clear(target)
        if target in ("all", "ai"):
            ai_db = resolve_path(cfg.get("ai", {}).get("cache_dir", "cache/ai")) / "gemini_cache.sqlite"
            if ai_db.exists():
                ai_cache = DiskCache(ai_db, ttl_hours=float(cfg.get("ai", {}).get("cache_ttl_hours", 720)))
                try:
                    deleted += ai_cache.clear(None if target == "all" else "ai")
                finally:
                    ai_cache.close()
        print(f"Cache limpa ({target}): {deleted} entrada(s) removida(s).")
    if args.cache_stats:
        print(format_cache_stats(cache.stats()))
    return 0


def format_top_table(top_df: pd.DataFrame) -> str:
    """Tabela curta do Top N para a consola."""
    if top_df.empty:
        return "(nenhuma empresa sobreviveu ao funil)"
    columns = [col for col in TOP_TABLE_COLUMNS if col in top_df.columns]
    view = top_df[columns].copy()
    if "company" in view.columns:
        view["company"] = view["company"].astype(str).str.slice(0, 28)
    if "sector" in view.columns:
        view["sector"] = view["sector"].astype(str).str.slice(0, 22)
    for col in ("composite_score", "score_quality", "score_valuation", "score_financial_health", "score_momentum"):
        if col in view.columns:
            view[col] = view[col].map(lambda v: "n/d" if pd.isna(v) else f"{v:.0f}")
    if "ai_review" in view.columns:
        view["ai_review"] = view["ai_review"].map(lambda v: "REVER" if v == REVIEW_FLAG else "")
    return view.rename(columns=TOP_TABLE_COLUMNS).to_string(index=False)


def format_ai_summary(row: pd.Series, result: AiResult | None) -> str:
    """Bloco de consola com a análise qualitativa de uma empresa."""
    if result is None:
        return ""
    if not result.ok:
        return f"   IA: falhou ({result.error})"
    analysis = result.analysis or {}
    header = (
        f"   IA ({result.model}{', cache' if result.from_cache else ''}): sentimento {analysis['sentiment']} "
        f"({analysis['sentiment_score']:+.2f})" + (f" | {REVIEW_FLAG}" if result.review_required else "")
    )
    lines = [header]
    if analysis.get("red_flags"):
        lines.append("   Red flags: " + " | ".join(analysis["red_flags"]))
    lines.append(textwrap.fill("Tese: " + analysis.get("thesis_summary", ""), width=110, initial_indent="   ", subsequent_indent="   "))
    return "\n".join(lines)


def enrich_with_sector_stats(metrics_df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Percentis, z-scores e medianas por setor, calculados sobre o universo completo."""
    scoring = cfg.get("scoring", {})
    percentile_columns = sorted(set(explain_metric_names()) | set(EXTRA_PERCENTILE_COLUMNS))
    return add_sector_stats(
        metrics_df,
        percentile_columns=percentile_columns,
        zscore_columns=pillar_metric_names(cfg),
        median_columns=explain_metric_names(),
        winsor=float(scoring.get("winsorize_z", 3.0)),
        robust=bool(scoring.get("robust_z", True)),
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):  # consola Windows: evitar erros com "→" e acentos
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_dotenv()

    try:
        cfg = load_config(args.config)
        if args.preset:
            cfg = apply_preset(cfg, args.preset)
        skip = parse_skip_stages(args.skip_stage)
        if args.cache_only and args.refresh_cache:
            raise ConfigError("--cache-only e --refresh-cache não podem ser usados em simultâneo")
    except ConfigError as exc:
        print(f"ERRO de configuração: {exc}", file=sys.stderr)
        return 2

    log_file = setup_logging(resolve_path(cfg["logging"]["dir"]), args.log_level or cfg["logging"].get("level", "INFO"))
    log.info("StockScreener v%s | config: %s%s", __version__, cfg["_config_path"],
             f" | preset: {args.preset}" if args.preset else "")
    log.info("Log desta execução: %s", log_file)
    started = time.time()

    top_n = args.top or int(cfg["scoring"]["top_n"])
    news_enabled = bool(cfg.get("news", {}).get("enabled", True)) and not args.no_news and not args.cache_only
    ai_enabled = bool(cfg.get("ai", {}).get("enabled", False)) and not args.no_ai and news_enabled

    data_cfg = cfg["data"]
    cache = DiskCache(resolve_path(data_cfg["cache_dir"]) / "yfinance_cache.sqlite", ttl_hours=float(data_cfg["cache_ttl_hours"]))
    settings = FetchSettings.from_config(data_cfg)
    if args.max_workers:
        settings.max_workers = max(1, args.max_workers)

    try:
        cache_rc = _handle_cache_commands(args, cfg, cache)
        if cache_rc is not None:
            return cache_rc

        # ---- Etapa 0: universo -------------------------------------------------
        if args.tickers:
            universe_label = "inline"
            universe = parse_inline_tickers(args.tickers)
        else:
            universe_label = args.universe or cfg["universe"]["default"]
            universe = get_universe(
                universe_label,
                cache,
                ttl_hours=float(cfg["universe"].get("cache_ttl_hours", 168)),
                refresh=args.refresh_cache,
                csv_ticker_column=cfg["universe"].get("csv_ticker_column", "ticker"),
                cache_only=args.cache_only,
            )
        if args.limit:
            universe = universe[: args.limit]
        tickers = [entry.ticker for entry in universe]
        diagnose_list: list[str] = []
        if args.diagnose:
            diagnose_list = [entry.ticker for entry in parse_inline_tickers(args.diagnose)]
            for ticker in diagnose_list:
                if ticker not in tickers:
                    tickers.append(ticker)
        log.info("Universo '%s': %d tickers", universe_label, len(tickers))

        # ---- Download com cache, retry e progresso ---------------------------
        datas, fetch_failures = fetch_many(
            tickers, cache, settings, refresh=args.refresh_cache, cache_only=args.cache_only
        )

        # ---- Métricas (funções puras) + estatísticas setoriais ---------------
        metrics_df, build_failures = build_metrics_table(datas, cfg)
        metrics_df = enrich_with_sector_stats(metrics_df, cfg)
        if not metrics_df.empty and "business_type" in metrics_df.columns:
            counts = metrics_df["business_type"].value_counts().to_dict()
            log.info("Tipos de negócio: %s", ", ".join(f"{k}={v}" for k, v in counts.items()))

        stages = build_stages(cfg, skip=skip)
        if diagnose_list:
            diagnostics = diagnose_tickers(metrics_df, stages, tickers=diagnose_list)
            print()
            print("=" * 78)
            print("DIAGNÓSTICO POR ETAPA")
            print("=" * 78)
            print(format_diagnostics(diagnostics))
            return 0

        pre_eliminations = [Elimination.from_download_failure(f.ticker, f.reason) for f in fetch_failures]
        pre_eliminations += [Elimination.from_download_failure(t, reason, rule="metrics") for t, reason in build_failures]

        # ---- Funil (etapas 1-6) ------------------------------------------------
        result = FunnelEngine(stages).run(metrics_df, pre_eliminations=pre_eliminations)

        # ---- Etapa 7: ranking e explicações ----------------------------------
        survivors = filter_by_sector(result.survivors, args.sector) if args.sector else result.survivors
        ranked = rank_survivors(survivors, cfg)
        if args.min_score is not None:
            ranked = filter_by_min_score(ranked, args.min_score)
        top_df = ranked.head(top_n).copy()
        top_df["explanation"] = [explain_company(row, cfg) for _, row in top_df.iterrows()]
        top_tickers = list(top_df["ticker"]) if "ticker" in top_df.columns else []

        # ---- Notícias (só Top N) ----------------------------------------------
        news_by_ticker: dict[str, list[NewsItem]] = {}
        if news_enabled and top_tickers:
            news_by_ticker = fetch_news_for_tickers(
                top_tickers, cfg, cache, finnhub_key=os.environ.get("FINNHUB_API_KEY") or None, refresh=args.refresh_cache
            )
            top_df["news_count"] = top_df["ticker"].map(lambda t: len(news_by_ticker.get(t, [])))

        # ---- Análise qualitativa com Gemini (só Top N; nunca altera o funil) --
        ai_results: dict[str, AiResult] = {}
        if ai_enabled and top_tickers:
            ai_cache = DiskCache(resolve_path(cfg["ai"].get("cache_dir", "cache/ai")) / "gemini_cache.sqlite",
                                 ttl_hours=float(cfg["ai"].get("cache_ttl_hours", 720)))
            try:
                analyst = GeminiAnalyst(cfg, cache=ai_cache)
                if not analyst.available:
                    log.warning("GEMINI_API_KEY ausente (.env): análise com IA ignorada")
                else:
                    log.info("Análise com IA: modelo %s para %d empresas", analyst.model, len(top_tickers))
                    jobs = [(row.to_dict(), str(row["explanation"]), news_by_ticker.get(row["ticker"], []))
                            for _, row in top_df.iterrows()]
                    ai_results = analyst.analyze_many(jobs, refresh=args.refresh_cache)
            finally:
                ai_cache.close()
        if ai_enabled and top_tickers:
            ai_frame = pd.DataFrame([ai_columns(ai_results.get(t)) for t in top_tickers], index=top_df.index)
            for column in ai_frame.columns:
                top_df[column] = ai_frame[column]

        # ---- Resumo na consola -----------------------------------------------
        print()
        print("=" * 78)
        print("RESUMO DO FUNIL")
        print("=" * 78)
        print(result.summary_text(top_n=top_n))
        if pre_eliminations:
            print("\nEliminadas na Etapa 0 (dados):")
            for elimination in pre_eliminations:
                print(f"  - {elimination.ticker}: {elimination.primary.observed}")
        print()
        print("=" * 78)
        print(f"TOP {len(top_df)} (de {result.n_survivors} sobreviventes)")
        print("=" * 78)
        print(format_top_table(top_df))
        if not top_df.empty:
            print()
            for _, row in top_df.iterrows():
                print(f"{int(row['rank'])}. {row['ticker']} — {row['explanation']}")
                ai_text = format_ai_summary(row, ai_results.get(row["ticker"]))
                if ai_text:
                    print(ai_text)
            flagged = [t for t, r in ai_results.items() if r.review_required]
            if flagged:
                print(f"\n{REVIEW_FLAG} (red flags nas notícias): {', '.join(flagged)}")

        # ---- Exportação --------------------------------------------------------
        out_dir = resolve_path(args.output_dir or cfg["output"]["dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        excel_path = out_dir / f"{cfg['output'].get('excel_prefix', 'screener')}_{datetime.now():%Y-%m-%d}.xlsx"
        saved = export_screener_workbook(
            result, metrics_df, excel_path, top_df=top_df, news_by_ticker=news_by_ticker, ai_results=ai_results
        )
        markdown_path = out_dir / f"{cfg['output'].get('markdown_prefix', 'relatorio')}_{datetime.now():%Y-%m-%d}.md"
        universe_text = universe_label + (f", primeiros {args.limit}" if args.limit else "")
        export_markdown_report(
            markdown_path, result=result, top_df=top_df, cfg=cfg, universe_label=universe_text,
            news_by_ticker=news_by_ticker, ai_results=ai_results,
        )
        print(f"\nExcel gerado: {saved}")
        print(f"Relatório Markdown: {markdown_path}")
        print(f"Log: {log_file}")
        log.info("Concluído em %.1f s", time.time() - started)
        return 0
    except UniverseError as exc:
        log.error("Universo: %s", exc)
        return 2
    finally:
        cache.close()


if __name__ == "__main__":
    sys.exit(main())

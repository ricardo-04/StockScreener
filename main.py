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
from src.config import STAGE_NAMES, ConfigError, load_config, resolve_path
from src.data.cache import DiskCache
from src.data.fetcher import FetchSettings, fetch_many
from src.explain import explain_company, explain_metric_names
from src.funnel.engine import FunnelEngine
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
        skip = parse_skip_stages(args.skip_stage)
    except ConfigError as exc:
        print(f"ERRO de configuração: {exc}", file=sys.stderr)
        return 2

    log_file = setup_logging(resolve_path(cfg["logging"]["dir"]), args.log_level or cfg["logging"].get("level", "INFO"))
    log.info("StockScreener v%s | config: %s", __version__, cfg["_config_path"])
    log.info("Log desta execução: %s", log_file)
    started = time.time()

    top_n = args.top or int(cfg["scoring"]["top_n"])
    news_enabled = bool(cfg.get("news", {}).get("enabled", True)) and not args.no_news
    ai_enabled = bool(cfg.get("ai", {}).get("enabled", False)) and not args.no_ai and news_enabled

    data_cfg = cfg["data"]
    cache = DiskCache(resolve_path(data_cfg["cache_dir"]) / "yfinance_cache.sqlite", ttl_hours=float(data_cfg["cache_ttl_hours"]))
    settings = FetchSettings.from_config(data_cfg)
    if args.max_workers:
        settings.max_workers = max(1, args.max_workers)

    try:
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
            )
        if args.limit:
            universe = universe[: args.limit]
        tickers = [entry.ticker for entry in universe]
        log.info("Universo '%s': %d tickers", universe_label, len(tickers))

        # ---- Download com cache, retry e progresso ---------------------------
        datas, fetch_failures = fetch_many(tickers, cache, settings, refresh=args.refresh_cache)

        # ---- Métricas (funções puras) + estatísticas setoriais ---------------
        metrics_df, build_failures = build_metrics_table(datas, cfg)
        metrics_df = enrich_with_sector_stats(metrics_df, cfg)
        if not metrics_df.empty and "business_type" in metrics_df.columns:
            counts = metrics_df["business_type"].value_counts().to_dict()
            log.info("Tipos de negócio: %s", ", ".join(f"{k}={v}" for k, v in counts.items()))

        pre_eliminations = [Elimination.from_download_failure(f.ticker, f.reason) for f in fetch_failures]
        pre_eliminations += [Elimination.from_download_failure(t, reason, rule="metrics") for t, reason in build_failures]

        # ---- Funil (etapas 1-6) ------------------------------------------------
        stages = build_stages(cfg, skip=skip)
        result = FunnelEngine(stages).run(metrics_df, pre_eliminations=pre_eliminations)

        # ---- Etapa 7: ranking e explicações ----------------------------------
        ranked = rank_survivors(result.survivors, cfg)
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

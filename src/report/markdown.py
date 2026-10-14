"""Relatório Markdown (``output/relatorio_YYYY-MM-DD.md``).

Conteúdo: aviso, resumo do funil (linha, tabela por etapa, regras que mais
eliminaram), tabela do Top N e uma secção por empresa do Top N com score e
pilares, explicação por regras, métricas com posição no setor, análise da IA
(claramente identificada como tal) e notícias recentes.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from src.ai.gemini_analyst import REVIEW_FLAG
from src.explain import EXPLAIN_METRICS, format_metric, position_phrase
from src.funnel.engine import FunnelResult
from src.metrics.statements import to_float

log = logging.getLogger(__name__)

DISCLAIMER = (
    "> **Aviso:** este relatório é o resultado de uma ferramenta de **triagem quantitativa** com dados do "
    "Yahoo Finance, que podem conter erros ou atrasos. **Não constitui recomendação de investimento** nem "
    "substitui a análise dos relatórios e contas. A secção \"Análise qualitativa (IA)\" é gerada por um modelo "
    "de linguagem a partir das notícias e métricas fornecidas e não altera a aprovação no funil."
)
PILLAR_LABELS = (
    ("score_quality", "Qualidade"),
    ("score_valuation", "Valuation"),
    ("score_financial_health", "Saúde financeira"),
    ("score_momentum", "Momentum"),
)


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text if text and text.lower() != "nan" else default


def _score(value: Any) -> str:
    number = to_float(value)
    return "n/d" if math.isnan(number) else f"{number:.0f}"


def _escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    lines += ["| " + " | ".join(_escape(str(cell)) for cell in row) + " |" for row in rows]
    return "\n".join(lines)


def _applies(spec: dict[str, Any], business_type: str) -> bool:
    types = spec.get("types")
    if types and business_type not in types:
        return False
    exclude = spec.get("exclude")
    return not (exclude and business_type in exclude)


def metrics_table(row: pd.Series, small_sector: int = 5) -> str:
    """Tabela das métricas explicáveis com valor e posição no setor."""
    business_type = _text(row.get("business_type"), "standard")
    sector_size = to_float(row.get("sector_size"))
    rows: list[list[str]] = []
    for spec in EXPLAIN_METRICS:
        if not _applies(spec, business_type):
            continue
        value = to_float(row.get(spec["metric"]))
        if math.isnan(value):
            continue
        pct = to_float(row.get(f"{spec['metric']}_sector_pct"))
        if math.isnan(pct):
            position = "n/d"
        else:
            effective = pct if spec["direction"] > 0 else 100.0 - pct
            position = position_phrase(effective, sector_size, small_sector)
        rows.append([spec["label"], format_metric(value, spec["format"]), position])
    if not rows:
        return "_Sem métricas disponíveis._"
    return _table(["Métrica", "Valor", "Posição no setor"], rows)


def funnel_section(result: FunnelResult, top_n: int, universe_label: str) -> str:
    lines = ["## Resumo do funil", ""]
    lines.append(f"- Universo: `{universe_label}` ({result.universe_size} empresas)")
    summary_lines = result.summary_text(top_n=top_n).splitlines()
    lines.append(f"- Funil: {summary_lines[0]}")
    lines.append("")
    stage_rows = [[c.label, str(c.entered), str(c.passed), str(c.eliminated)] for c in result.stage_counts]
    lines.append(_table(["Etapa", "Entraram", "Passaram", "Eliminadas"], stage_rows))
    top_rules = result.top_rules(10)
    if top_rules:
        lines += ["", "**Regras que mais eliminaram:**", ""]
        lines += [f"{i}. {rule} — {count} empresas" for i, (rule, count) in enumerate(top_rules, start=1)]
    return "\n".join(lines)


def top_table_section(top_df: pd.DataFrame, has_ai: bool) -> str:
    lines = [f"## Top {len(top_df)}", ""]
    if top_df.empty:
        lines.append("_Nenhuma empresa sobreviveu a todas as etapas do funil._")
        return "\n".join(lines)
    headers = ["#", "Ticker", "Empresa", "Setor", "Tipo", "Score", "Qual.", "Val.", "Saúde", "Mom."]
    if has_ai:
        headers += ["IA", "Revisão"]
    rows = []
    for _, row in top_df.iterrows():
        cells = [
            _score(row.get("rank")), _text(row.get("ticker")), _text(row.get("company")), _text(row.get("sector")),
            _text(row.get("business_type")), _score(row.get("composite_score")),
            *[_score(row.get(col)) for col, _ in PILLAR_LABELS],
        ]
        if has_ai:
            cells += [_text(row.get("ai_sentiment"), "—"), _text(row.get("ai_review"), "")]
        rows.append(cells)
    lines.append(_table(headers, rows))
    return "\n".join(lines)


def ai_section(result: Any) -> str:
    """Bloco da análise qualitativa (só quando existe resultado da IA)."""
    if result is None:
        return ""
    lines = [f"#### Análise qualitativa (IA: {result.model})", ""]
    if not result.ok:
        lines.append(f"_Análise indisponível: {result.error}_")
        return "\n".join(lines)
    analysis = result.analysis or {}
    flag = f" — **{REVIEW_FLAG}**" if result.review_required else ""
    lines.append(f"- **Sentimento:** {analysis.get('sentiment')} ({to_float(analysis.get('sentiment_score')):+.2f}){flag}")
    lines.append(f"- **Tese:** {analysis.get('thesis_summary', '')}")
    lines.append(f"- **Moat:** {analysis.get('moat_assessment', '')}")
    lines.append("- **Catalisadores:** " + ("; ".join(analysis.get("catalysts") or []) or "—"))
    lines.append("- **Riscos:** " + ("; ".join(analysis.get("risks") or []) or "—"))
    lines.append("- **Red flags:** " + ("; ".join(analysis.get("red_flags") or []) or "nenhuma"))
    lines.append(f"- **Impacto das notícias:** {analysis.get('news_impact', '')}")
    return "\n".join(lines)


def news_section(items: list) -> str:
    lines = [f"#### Notícias recentes ({len(items)})", ""]
    if not items:
        lines.append("_Sem notícias disponíveis._")
        return "\n".join(lines)
    for item in items:
        title = item.title.replace("[", "(").replace("]", ")")
        link = f"[{title}]({item.link})" if item.link else title
        source = f" · {item.source}" if item.source else ""
        lines.append(f"- {item.published_text}{source} · {link}")
    return "\n".join(lines)


def company_section(row: pd.Series, news: list, ai_result: Any, small_sector: int = 5) -> str:
    ticker = _text(row.get("ticker"))
    pillars = " · ".join(f"{label} {_score(row.get(col))}" for col, label in PILLAR_LABELS)
    lines = [
        f"### {_score(row.get('rank'))}. {ticker} — {_text(row.get('company'), ticker)}",
        "",
        f"**Setor:** {_text(row.get('sector'), 'n/d')} · **Indústria:** {_text(row.get('industry'), 'n/d')} · "
        f"**Tipo:** {_text(row.get('business_type'), 'standard')} · **Preço:** {format_metric(row.get('price'), 'num')} "
        f"· **Market cap:** {to_float(row.get('market_cap')) / 1e9:,.1f} mil M" if not math.isnan(to_float(row.get("market_cap")))
        else f"**Setor:** {_text(row.get('sector'), 'n/d')} · **Tipo:** {_text(row.get('business_type'), 'standard')}",
        "",
        f"**Score composto:** {_score(row.get('composite_score'))} ({pillars})",
        "",
        f"**Explicação (regras, sem IA):** {_text(row.get('explanation'), 'n/d')}",
    ]
    warnings = _text(row.get("funnel_warnings"))
    if warnings:
        lines += ["", f"**Alertas do funil:** {warnings}"]
    lines += ["", metrics_table(row, small_sector)]
    ai_block = ai_section(ai_result)
    if ai_block:
        lines += ["", ai_block]
    lines += ["", news_section(news)]
    return "\n".join(lines)


def render_markdown_report(
    result: FunnelResult,
    top_df: pd.DataFrame,
    cfg: dict[str, Any],
    universe_label: str = "",
    news_by_ticker: dict[str, list] | None = None,
    ai_results: dict[str, Any] | None = None,
    generated_at: datetime | None = None,
) -> str:
    generated_at = generated_at or datetime.now()
    top_n = int(cfg.get("scoring", {}).get("top_n", len(top_df)))
    small_sector = int(((cfg.get("stages", {}).get("valuation", {}) or {}).get("min_sector_size", 5)))
    ai_results = ai_results or {}
    news_by_ticker = news_by_ticker or {}
    has_ai = any(r is not None for r in ai_results.values())

    parts = [
        f"# StockScreener — relatório de triagem ({generated_at:%Y-%m-%d})",
        "",
        DISCLAIMER,
        "",
        f"_Gerado em {generated_at:%Y-%m-%d %H:%M}. Dados: Yahoo Finance via yfinance._",
        "",
        funnel_section(result, top_n, universe_label),
        "",
        top_table_section(top_df, has_ai),
    ]
    if not top_df.empty:
        parts += ["", "## Empresas do Top N"]
        for _, row in top_df.iterrows():
            ticker = _text(row.get("ticker"))
            parts += ["", company_section(row, news_by_ticker.get(ticker, []), ai_results.get(ticker), small_sector)]
    flagged = [t for t, r in ai_results.items() if r is not None and r.review_required]
    if flagged:
        parts += ["", f"**{REVIEW_FLAG}** (red flags identificadas pela IA nas notícias): {', '.join(flagged)}"]
    parts += ["", "---", "", DISCLAIMER]
    return "\n".join(parts) + "\n"


def export_markdown_report(path: Path, **kwargs: Any) -> Path:
    """Escreve o relatório Markdown em ``path`` (UTF-8) e devolve o caminho."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown_report(**kwargs), encoding="utf-8")
    log.info("Relatório Markdown gravado: %s", path)
    return path

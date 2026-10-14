"""Relatório Markdown: resumo do funil, tabela do Top N, secção por empresa, IA e notícias."""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from src.ai.gemini_analyst import REVIEW_FLAG, AiResult
from src.funnel.engine import FunnelEngine
from src.funnel.stages import Elimination
from src.news import NewsItem
from src.report.markdown import export_markdown_report, metrics_table, render_markdown_report

NAN = float("nan")


def make_top() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "rank": 1, "ticker": "ACN", "company": "Accenture plc", "sector": "Technology", "industry": "IT Services",
            "business_type": "standard", "price": 216.5, "market_cap": 132.5e9, "composite_score": 59.4,
            "score_quality": 54.0, "score_valuation": 69.0, "score_financial_health": 61.0, "score_momentum": 48.0,
            "explanation": "Passou todas as 6 etapas. Pontos fortes: caixa líquida.", "funnel_warnings": "PEG de 2.0 acima de 2.5",
            "roic_avg_3y": 0.32, "roic_avg_3y_sector_pct": 80.0, "fcf_yield": 0.084, "fcf_yield_sector_pct": 94.0,
            "ev_to_ebit": 11.9, "ev_to_ebit_sector_pct": 10.0, "sector_size": 60, "ai_sentiment": "positivo", "ai_review": "",
        },
        {
            "rank": 2, "ticker": "BK", "company": "Banco", "sector": "Financial Services", "industry": "Banks",
            "business_type": "bank_insurance", "price": 50.0, "market_cap": NAN, "composite_score": 55.0,
            "score_quality": 50.0, "score_valuation": 60.0, "score_financial_health": NAN, "score_momentum": 55.0,
            "explanation": "Passou todas as 6 etapas.", "funnel_warnings": "", "roe_avg_3y": 0.12, "roe_avg_3y_sector_pct": 40.0,
            "roic_avg_3y": 0.2, "roic_avg_3y_sector_pct": 50.0, "sector_size": 3, "ai_sentiment": "", "ai_review": REVIEW_FLAG,
        },
    ])


def make_result(top: pd.DataFrame):
    return FunnelEngine([]).run(top, pre_eliminations=[Elimination.from_download_failure("ZZZ", "info vazio")])


def test_metrics_table_respects_type_and_sector_size():
    top = make_top()
    standard = metrics_table(top.iloc[0])
    assert "| ROIC médio 3 anos | 32.0% | top 20% do setor |" in standard
    assert "| EV/EBIT | 11.9x | top 10% do setor |" in standard
    assert "ROE" not in standard
    bank = metrics_table(top.iloc[1])
    assert "| ROE médio 3 anos | 12.0% | 3.º de 3 no setor |" in bank  # percentil 40 em 3 empresas = último
    assert "ROIC" not in bank  # excluído para bancos


def test_render_report_full(tmp_path):
    top = make_top()
    result = make_result(top)
    news = {"ACN": [NewsItem("Deal [big]", "Reuters", datetime(2026, 10, 10, tzinfo=timezone.utc), "https://x/1", "s"),
                    NewsItem("Sem link", "", None, "", "")]}
    ai = {
        "ACN": AiResult(ticker="ACN", model="gemini-test", analysis={
            "sentiment": "positivo", "sentiment_score": 0.6, "catalysts": ["Contrato [N1]"], "risks": [],
            "red_flags": [], "moat_assessment": "Escala.", "thesis_summary": "Tese.", "news_impact": "Positivo."}),
        "BK": AiResult(ticker="BK", model="gemini-test", analysis={
            "sentiment": "negativo", "sentiment_score": -0.7, "catalysts": [], "risks": ["Risco [N1]"],
            "red_flags": ["Investigação regulatória [N1]"], "moat_assessment": "m", "thesis_summary": "t", "news_impact": "i"},
            review_required=True),
    }
    cfg = {"scoring": {"top_n": 20}, "stages": {"valuation": {"min_sector_size": 5}}}
    text = render_markdown_report(result, top, cfg, universe_label="sp500", news_by_ticker=news, ai_results=ai,
                                  generated_at=datetime(2026, 10, 14, 17, 0))

    assert text.startswith("# StockScreener — relatório de triagem (2026-10-14)")
    assert text.count("Não constitui recomendação de investimento") == 2
    assert "- Universo: `sp500` (3 empresas)" in text
    assert "| Etapa 0 – Dados | 3 | 2 | 1 |" in text
    assert "1. Etapa 0 – Dados · Descarga/cálculo de dados — 1 empresas" in text
    assert "| 1 | ACN | Accenture plc | Technology | standard | 59 | 54 | 69 | 61 | 48 | positivo |  |" in text
    assert f"| 2 | BK | Banco | Financial Services | bank_insurance | 55 | 50 | 60 | n/d | 55 | — | {REVIEW_FLAG} |" in text
    assert "### 1. ACN — Accenture plc" in text
    assert "**Market cap:** 132.5 mil M" in text
    assert "**Explicação (regras, sem IA):** Passou todas as 6 etapas. Pontos fortes: caixa líquida." in text
    assert "**Alertas do funil:** PEG de 2.0 acima de 2.5" in text
    assert "#### Análise qualitativa (IA: gemini-test)" in text
    assert "- **Sentimento:** positivo (+0.60)" in text
    assert f"- **Sentimento:** negativo (-0.70) — **{REVIEW_FLAG}**" in text
    assert "- **Red flags:** Investigação regulatória [N1]" in text
    assert "#### Notícias recentes (2)" in text
    assert "- 2026-10-10 · Reuters · [Deal (big)](https://x/1)" in text
    assert "- s/ data · Sem link" in text
    assert f"**{REVIEW_FLAG}** (red flags identificadas pela IA nas notícias): BK" in text

    path = export_markdown_report(tmp_path / "rel" / "relatorio.md", result=result, top_df=top, cfg=cfg,
                                  universe_label="sp500", news_by_ticker=news, ai_results=ai)
    assert path.exists() and path.read_text(encoding="utf-8").startswith("# StockScreener")


def test_render_report_without_ai_and_empty_top():
    top = make_top()
    result = make_result(top)
    text = render_markdown_report(result, top, {"scoring": {"top_n": 20}}, universe_label="inline")
    assert "| IA | Revisão |" not in text and "#### Análise qualitativa" not in text
    assert "#### Notícias recentes (0)" in text and "_Sem notícias disponíveis._" in text
    empty = render_markdown_report(FunnelEngine([]).run(top.iloc[0:0]), top.iloc[0:0], {"scoring": {"top_n": 20}})
    assert "_Nenhuma empresa sobreviveu" in empty and "## Empresas do Top N" not in empty

"""Configuração (config.yaml) e exportação Excel."""
from __future__ import annotations

import openpyxl
import pandas as pd
import pytest

from src.config import ConfigError, load_config, resolve_path, validate_config
from src.funnel.engine import FunnelEngine
from src.funnel.stages import Elimination
from src.report.excel import excel_value, export_screener_workbook, number_format_for


def test_default_config_loads_and_is_consistent(base_config):
    assert base_config["universe"]["default"] == "sp500"
    assert base_config["data"]["max_workers"] <= 8
    assert base_config["stages"]["eligibility"]["min_market_cap_usd"] == 2_000_000_000  # sem notação científica
    assert base_config["stages"]["eligibility"]["min_avg_dollar_volume_usd"] == 10_000_000
    assert base_config["stages"]["financial_health"]["by_type"]["cyclical_commodity"]["max_net_debt_to_ebitda"] == 2.0
    assert base_config["stages"]["valuation"]["by_type"]["bank_insurance"]["max_forward_pe"] == 18.0
    assert base_config["stages"]["quality"]["default"]["min_roic_avg_3y"] == 0.10
    assert base_config["stages"]["growth"]["by_type"]["cyclical_commodity"]["min_revenue_cagr_3y"] == 0.0
    assert set(base_config["scoring"]["pillars"]) == {"quality", "valuation", "financial_health", "momentum"}
    assert base_config["scoring"]["top_n"] == 20
    assert sum(base_config["scoring"]["weights"].values()) == pytest.approx(1.0)
    assert resolve_path("cache").name == "cache" and resolve_path("cache").is_absolute()


def test_validate_config_rejects_bad_weights_and_missing_sections(base_config):
    bad = {**base_config, "scoring": {**base_config["scoring"], "weights": {"quality": 0.5, "valuation": 0.3}}}
    with pytest.raises(ConfigError, match="somar 1.0"):
        validate_config(bad)
    with pytest.raises(ConfigError, match="Secções em falta"):
        validate_config({"universe": {}})
    with pytest.raises(ConfigError):
        load_config("nao_existe.yaml")


def test_number_formats_by_column_name():
    assert number_format_for("gross_margin") == "0.0%"
    assert number_format_for("dividend_yield") == "0.0%"
    assert number_format_for("price_vs_sma200") == "0.0%"
    assert number_format_for("fcf_yield_sector_pct") == "0.0"
    assert number_format_for("net_debt_to_ebitda") == "0.00"
    assert number_format_for("forward_pe") == "0.00"
    assert number_format_for("price_to_book") == "0.00"
    assert number_format_for("eps_diluted_fy0") == "0.00"
    assert number_format_for("price") == "$#,##0.00"
    assert number_format_for("sma_200") == "$#,##0.00"
    assert number_format_for("price_history_days") == "#,##0"
    assert number_format_for("shares_diluted_fy1") == "#,##0"
    assert number_format_for("market_cap") == '$#,##0,,"M"'
    assert number_format_for("free_cash_flow_fy2") == '$#,##0,,"M"'
    assert number_format_for("avg_dollar_volume_60d") == '$#,##0,,"M"'
    assert number_format_for("ticker") is None


def test_excel_value_conversions():
    assert excel_value(float("nan")) is None
    assert excel_value(float("inf")) is None
    assert excel_value(pd.NA) is None
    assert excel_value(pd.Timestamp("2025-01-01", tz="UTC")).tzinfo is None
    assert excel_value([1, "a"]) == '[1, "a"]'
    assert excel_value(True) is True


def test_export_workbook_has_three_sheets(tmp_path):
    metrics = pd.DataFrame(
        {"ticker": ["A", "B"], "company": ["Alfa", "Beta"], "business_type": ["standard", "reit"],
         "market_cap": [5e9, float("nan")], "gross_margin": [0.5, 0.3], "fetched_at": ["2026-10-14", "2026-10-14"]}
    )
    result = FunnelEngine([]).run(metrics, pre_eliminations=[Elimination.from_download_failure("Z", "info vazio")])
    top = metrics.copy()
    top["rank"] = [1, 2]
    top["composite_score"] = [80.0, 60.0]
    top["explanation"] = ["Passou todas as etapas.", "Passou todas as etapas."]
    path = export_screener_workbook(result, metrics, tmp_path / "out" / "screener.xlsx", top_df=top)
    assert path.exists()

    wb = openpyxl.load_workbook(path)
    assert wb.sheetnames == ["Resumo_Funil", "Top_N", "Eliminadas", "Universo_Completo"]

    from datetime import datetime, timezone
    from src.ai.gemini_analyst import AiResult
    from src.news import NewsItem

    news = {"A": [NewsItem("Título", "Fonte", datetime(2026, 10, 1, tzinfo=timezone.utc), "https://x", "Resumo")]}
    ai = {"A": AiResult(ticker="A", model="m", analysis={"sentiment": "neutro", "sentiment_score": 0.0, "catalysts": [],
                                                         "risks": ["r"], "red_flags": ["flag"], "moat_assessment": "m",
                                                         "thesis_summary": "t", "news_impact": "i"}, review_required=True)}
    path = export_screener_workbook(result, metrics, tmp_path / "out" / "screener2.xlsx", top_df=top, news_by_ticker=news, ai_results=ai)
    wb = openpyxl.load_workbook(path)
    assert wb.sheetnames == ["Resumo_Funil", "Top_N", "Noticias_IA", "Eliminadas", "Universo_Completo"]
    ws = wb["Noticias_IA"]
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    assert rows[0][2] == "ANÁLISE IA" and "REVER MANUALMENTE" in rows[0][5]
    assert rows[1][2] == "NOTÍCIA" and rows[1][5] == "Título" and rows[1][3] == "2026-10-01"
    top_ws = wb["Top_N"]
    assert [c.value for c in top_ws[1]][:3] == ["rank", "ticker", "company"]
    assert top_ws["A2"].value == 1 and top_ws["A2"].number_format == "#,##0"
    universe_ws = wb["Universo_Completo"]
    assert [c.value for c in universe_ws[1]] == list(metrics.columns)
    assert universe_ws["D3"].value is None  # NaN -> célula vazia
    assert universe_ws["E2"].number_format == "0.0%"
    eliminated_ws = wb["Eliminadas"]
    assert eliminated_ws["A2"].value == "Z" and eliminated_ws["E2"].value == "Etapa 0 – Dados"
    summary_ws = wb["Resumo_Funil"]
    assert summary_ws["A1"].value == "etapa" and summary_ws["B2"].value == 3

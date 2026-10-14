"""Motor do funil: eliminação na primeira etapa falhada, registo de todas as regras, resumo."""
from __future__ import annotations

import math

import pandas as pd
import pytest

from src.funnel import base
from src.funnel.engine import FunnelEngine
from src.funnel.stages import Elimination, RuleFailure, Stage, build_stages, check_max, check_min, format_value


class MarketCapStage(Stage):
    name = "eligibility"
    order = 1
    label = "Etapa 1 – Elegibilidade"

    def evaluate(self, row):
        failures = []
        for check in (
            check_min(row["market_cap"], self.config["min_market_cap_usd"], "min_market_cap", "Market cap mínimo"),
            check_min(row["price"], self.config["min_price_usd"], "min_price", "Preço mínimo"),
        ):
            if check:
                failures.append(check)
        return failures


class LeverageStage(Stage):
    name = "financial_health"
    order = 2
    label = "Etapa 2 – Saúde financeira"

    def evaluate(self, row):
        failure = check_max(row["net_debt_to_ebitda"], 3.0, "max_net_debt_to_ebitda", "Net Debt/EBITDA máximo")
        return [failure] if failure else []

    def warnings(self, row):
        return ["alavancagem acima de 2.5x"] if row["net_debt_to_ebitda"] > 2.5 else []


@pytest.fixture
def frame():
    return pd.DataFrame(
        {
            "ticker": ["A", "B", "C", "D", "E"],
            "company": ["Alfa", "Beta", "Gama", "Delta", "Épsilon"],
            "business_type": ["standard"] * 5,
            "market_cap": [5e9, 1e9, 5e9, float("nan"), 5e9],
            "price": [50.0, 3.0, 50.0, 50.0, 50.0],
            "net_debt_to_ebitda": [1.0, 0.5, 4.0, 1.0, 2.8],
        }
    )


def test_engine_eliminates_at_first_failing_stage_and_records_all_rules(frame):
    stages = [LeverageStage(), MarketCapStage({"min_market_cap_usd": 2e9, "min_price_usd": 5.0})]
    pre = [Elimination.from_download_failure("F", "info vazio")]
    result = FunnelEngine(stages).run(frame, pre_eliminations=pre)

    assert result.universe_size == 6
    assert list(result.survivors["ticker"]) == ["A", "E"]
    assert result.survivors.loc[result.survivors["ticker"] == "E", "funnel_warnings"].iloc[0] == "alavancagem acima de 2.5x"
    assert result.survivors.loc[result.survivors["ticker"] == "A", "funnel_warnings"].iloc[0] == ""
    assert (result.survivors["stages_passed"] == 2).all()

    by_ticker = {e.ticker: e for e in result.eliminations}
    assert by_ticker["F"].stage_order == 0
    assert by_ticker["B"].stage == "eligibility" and len(by_ticker["B"].failures) == 2  # ambas as regras
    assert {f.rule for f in by_ticker["B"].failures} == {"min_market_cap", "min_price"}
    assert by_ticker["B"].failures[0].threshold == 2e9 and by_ticker["B"].failures[0].observed == 1e9
    assert by_ticker["D"].stage == "eligibility" and math.isnan(by_ticker["D"].failures[0].observed)
    assert by_ticker["C"].stage == "financial_health"  # passou a etapa 1, caiu na 2
    assert by_ticker["C"].failures[0].observed == 4.0
    assert "C" not in [e.ticker for e in result.eliminations if e.stage == "eligibility"]

    counts = {c.name: (c.entered, c.passed) for c in result.stage_counts}
    assert counts["download"] == (6, 5)
    assert counts["eligibility"] == (5, 3)
    assert counts["financial_health"] == (3, 2)
    assert [c.order for c in result.stage_counts] == [0, 1, 2]  # ordenação por order, não pela lista


def test_summary_text_tables_and_top_rules(frame):
    stages = [MarketCapStage({"min_market_cap_usd": 2e9, "min_price_usd": 5.0}), LeverageStage()]
    result = FunnelEngine(stages).run(frame)
    text = result.summary_text(top_n=20)
    assert text.startswith("5 empresas → Etapa 0 – Dados: 5 → Etapa 1 – Elegibilidade: 3 → Etapa 2 – Saúde financeira: 2 → 2 sobreviventes → Top 2")
    assert "Regras que mais eliminaram" in text

    top = result.top_rules(5)
    assert top[0] == ("Etapa 1 – Elegibilidade · Market cap mínimo", 2)
    assert dict(top)["Etapa 2 – Saúde financeira · Net Debt/EBITDA máximo"] == 1

    table = result.eliminations_table()
    assert len(table) == 4  # B: 2 regras, D: 1, C: 1
    assert list(table.columns)[:6] == ["ticker", "company", "business_type", "stage_order", "stage", "rule"]
    assert table.loc[table["ticker"] == "D", "observed"].iloc[0] == "n/d"
    assert table.loc[table["ticker"] == "C", "threshold"].iloc[0] == "3.00"

    stage_table = result.stage_table()
    assert list(stage_table["eliminadas"]) == [0, 2, 1]


def test_engine_without_stages_keeps_everyone(frame):
    result = FunnelEngine([]).run(frame)
    assert result.n_survivors == 5
    assert "nenhuma etapa" in result.summary_text()
    assert result.top_rules() == []
    assert result.eliminations_table().empty


def test_check_helpers_and_format_value():
    assert check_min(5, 5, "r", "d") is None
    assert check_min(4, 5, "r", "d").observed == 4
    assert check_min(None, 5, "r", "d", nan_fails=False) is None
    assert check_max(6, 5, "r", "d").threshold == 5
    assert math.isnan(check_max("x", 5, "r", "d").observed)
    assert format_value(None) == "n/d"
    assert format_value(2.5e9) == "2.50 mil M"
    assert format_value(12_500_000.0) == "12.5 M"
    assert format_value(0.1234) == "0.12"
    assert format_value(True) == "sim"
    assert format_value("texto") == "texto"
    assert RuleFailure("r", "ROIC mínimo", 0.08, 0.10).text() == "ROIC mínimo: 0.08 (limiar 0.10)"


def test_build_stages_respects_skip_enabled_and_registry(base_config, monkeypatch):
    monkeypatch.setattr(base, "STAGE_REGISTRY", {"eligibility": MarketCapStage, "financial_health": LeverageStage})
    stages = build_stages(base_config)
    assert [s.name for s in stages] == ["eligibility", "financial_health"]
    assert stages[0].config["min_market_cap_usd"] == 2_000_000_000
    assert stages[0].global_config is base_config

    assert [s.name for s in build_stages(base_config, skip=["eligibility"])] == ["financial_health"]

    cfg = {**base_config, "stages": {**base_config["stages"], "financial_health": {"enabled": False}}}
    assert [s.name for s in build_stages(cfg)] == ["eligibility"]

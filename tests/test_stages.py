"""Etapas 1-6 do funil com linhas sintéticas: etapa e motivo de eliminação corretos."""
from __future__ import annotations

import math

import pandas as pd
import pytest

from src.funnel.engine import FunnelEngine
from src.funnel.stages import (
    EligibilityStage, FinancialHealthStage, GrowthStage, MomentumStage, QualityStage, ValuationStage,
    build_stages, merged_type_config,
)

NAN = float("nan")

GOOD = {
    "ticker": "GOOD", "company": "Good Co", "business_type": "standard", "sector": "Technology", "currency": "USD",
    "market_cap": 5e9, "avg_dollar_volume_60d": 5e7, "price": 50.0, "years_of_statements": 4,
    "revenue": 1e9, "ebit": 2e8, "net_income": 1.5e8, "operating_cash_flow": 2.2e8, "capex": -6e7,
    "total_assets": 2e9, "equity": 9e8, "total_debt": 4e8, "cash": 1.5e8,
    "net_debt": 2.5e8, "net_debt_to_ebitda": 1.0, "debt_status": "com_divida", "interest_coverage": 20.0,
    "altman_z": 3.5, "current_ratio": 2.0, "piotroski_f": 7.0, "piotroski_criteria": 9, "beneish_m": -2.5,
    "roic_avg_3y": 0.18, "roic_years_observed": 3, "fcf_positive_years": 4, "fcf_years_observed": 4,
    "cash_conversion": 1.0, "gross_margin_std_pp": 2.0, "share_dilution_cagr": -0.01,
    "revenue_cagr_3y": 0.08, "revenue_cagr_span_years": 3, "eps_consecutive_declines": 0.0,
    "revenue_growth_est_next_fy": 0.05,
    "sector_size": 10, "fcf_yield": 0.05, "fcf_yield_sector_pct": 70.0, "ev_to_ebit": 15.0,
    "ev_to_ebit_sector_pct": 40.0, "forward_pe": 20.0, "peg_ratio": 1.5, "price_to_book": 5.0,
    "price_to_book_sector_pct": 50.0, "dividend_yield": 0.01, "p_ffo": NAN,
    "momentum_12_1": 0.10, "sma_200": 45.0, "price_vs_sma200": 0.11,
    "roe_fy0": 0.15, "roe_fy1": 0.14, "roe_fy2": 0.13, "roe_avg_3y": 0.14,
}


def company(ticker: str, **overrides) -> dict:
    row = dict(GOOD)
    row.update(ticker=ticker, company=f"{ticker} Co", **overrides)
    return row


def bank(ticker: str, **overrides) -> dict:
    row = company(ticker, business_type="bank_insurance", sector="Financial Services", ebit=NAN, capex=NAN,
                  operating_cash_flow=NAN, net_debt_to_ebitda=NAN, interest_coverage=NAN, altman_z=NAN,
                  current_ratio=NAN, piotroski_f=NAN, piotroski_criteria=0, beneish_m=NAN, roic_avg_3y=NAN,
                  fcf_positive_years=0, fcf_years_observed=0, cash_conversion=NAN, gross_margin_std_pp=NAN,
                  price_to_book=1.2, forward_pe=12.0, roe_avg_3y=0.12, fcf_yield=NAN, fcf_yield_sector_pct=NAN,
                  ev_to_ebit=NAN, ev_to_ebit_sector_pct=NAN)
    row.update(overrides)
    return row


def series(**overrides) -> pd.Series:
    return pd.Series(company("X", **overrides))


# ----------------------------------------------------------------------------- unidades por etapa
def test_eligibility_rules_and_missing_fields(base_config):
    stage = EligibilityStage(base_config["stages"]["eligibility"], base_config)
    assert stage.evaluate(series()) == []
    failures = stage.evaluate(series(market_cap=1e9, price=3.0))
    assert {f.rule for f in failures} == {"min_market_cap", "min_price"}
    failures = stage.evaluate(series(ebit=NAN, capex=NAN))
    assert [f.description for f in failures] == ["dados insuficientes: ebit", "dados insuficientes: capex"]
    assert stage.evaluate(series(years_of_statements=2))[0].rule == "min_statement_years"
    assert stage.evaluate(series(avg_dollar_volume_60d=NAN))[0].rule == "min_avg_dollar_volume"
    assert stage.warnings(series(currency="EUR")) == ["moeda de cotação EUR ≠ USD: limiares em USD podem não se aplicar"]
    bank_row = pd.Series(bank("B"))
    assert stage.evaluate(bank_row) == []  # ebit/capex não são críticos para bancos
    assert stage.evaluate(pd.Series(bank("B", price_to_book=NAN)))[0].description == "dados insuficientes: price_to_book"


def test_financial_health_rules(base_config):
    stage = FinancialHealthStage(base_config["stages"]["financial_health"], base_config)
    assert stage.evaluate(series()) == []
    assert stage.evaluate(series(net_debt_to_ebitda=4.0))[0].rule == "max_net_debt_to_ebitda"
    assert stage.evaluate(series(net_debt_to_ebitda=NAN))[0].rule == "max_net_debt_to_ebitda"  # EBITDA negativo
    assert stage.evaluate(series(net_debt=-1e8, net_debt_to_ebitda=NAN, interest_coverage=NAN)) == []  # caixa líquida
    assert stage.evaluate(series(interest_coverage=2.0))[0].rule == "min_interest_coverage"
    assert stage.evaluate(series(interest_coverage=NAN, debt_status="sem_divida")) == []
    assert stage.evaluate(series(interest_coverage=NAN, net_debt_to_ebitda=0.5)) == []  # dívida imaterial
    assert "dívida imaterial" in stage.warnings(series(interest_coverage=NAN, net_debt_to_ebitda=0.5))[0]
    assert stage.evaluate(series(interest_coverage=NAN, net_debt_to_ebitda=2.0))[0].description.startswith("dados insuficientes")
    stale = series(interest_expense_source="Interest Expense (2024, último ano em falta)")
    assert stage.evaluate(stale) == [] and "ano anterior" in stage.warnings(stale)[0]
    assert stage.evaluate(series(altman_z=1.0))[0].rule == "min_altman_z"
    assert stage.evaluate(series(altman_z=NAN)) == [] and "Altman" in stage.warnings(series(altman_z=NAN))[0]
    assert stage.evaluate(series(current_ratio=0.8))[0].rule == "min_current_ratio"
    assert stage.evaluate(series(piotroski_f=3.0))[0].rule == "min_piotroski"
    assert stage.evaluate(series(piotroski_f=3.0, piotroski_criteria=5)) == []  # incompleto: só aviso
    assert "Piotroski incompleto (5/9" in stage.warnings(series(piotroski_f=3.0, piotroski_criteria=5))[0]
    assert stage.evaluate(series(beneish_m=-1.0))[0].rule == "max_beneish_m"
    assert stage.evaluate(series(beneish_m=NAN)) == [] and "Beneish" in stage.warnings(series(beneish_m=NAN))[0]
    # utilities: limiares próprios e sem Altman/current ratio
    utility = series(business_type="utility", net_debt_to_ebitda=5.0, interest_coverage=3.0, altman_z=NAN, current_ratio=NAN)
    assert stage.evaluate(utility) == [] and stage.warnings(utility) == []
    assert stage.evaluate(series(business_type="cyclical_commodity", net_debt_to_ebitda=2.5))[0].threshold == 2.0
    assert stage.evaluate(series(business_type="reit", net_debt_to_ebitda=6.5, altman_z=NAN, current_ratio=NAN)) == []
    # bancos: ROE positivo 3 anos e P/B disponível
    assert stage.evaluate(pd.Series(bank("B"))) == []
    assert stage.evaluate(pd.Series(bank("B", roe_fy1=-0.02)))[0].rule == "roe_positive"
    assert stage.evaluate(pd.Series(bank("B", roe_fy2=NAN)))[0].rule == "roe_history"
    assert stage.evaluate(pd.Series(bank("B", price_to_book=NAN)))[0].rule == "price_to_book_available"


def test_quality_rules(base_config):
    stage = QualityStage(base_config["stages"]["quality"], base_config)
    assert stage.evaluate(series()) == []
    assert stage.evaluate(series(roic_avg_3y=0.05))[0].rule == "min_roic_avg_3y"
    assert stage.evaluate(series(roic_avg_3y=NAN))[0].description.startswith("dados insuficientes: ROIC")
    assert stage.evaluate(series(roic_years_observed=1))[0].description.startswith("dados insuficientes: ROIC")
    assert stage.evaluate(series(fcf_positive_years=2))[0].rule == "min_fcf_positive_years"
    assert stage.evaluate(series(fcf_years_observed=2, fcf_positive_years=2))[0].description.startswith("dados insuficientes: FCF")
    assert stage.evaluate(series(cash_conversion=0.5))[0].rule == "min_cash_conversion"
    assert stage.evaluate(series(cash_conversion=NAN))[0].rule == "min_cash_conversion"
    assert stage.evaluate(series(gross_margin_std_pp=8.0))[0].rule == "max_gross_margin_std"
    cyclical = series(business_type="cyclical_commodity", gross_margin_std_pp=8.0)
    assert stage.evaluate(cyclical) == [] and "volátil" in stage.warnings(cyclical)[0]
    assert stage.evaluate(series(share_dilution_cagr=0.05))[0].rule == "max_share_dilution"
    assert stage.evaluate(series(share_dilution_cagr=NAN)) == [] and "diluição" in stage.warnings(series(share_dilution_cagr=NAN))[0]
    assert stage.evaluate(pd.Series(bank("B"))) == []
    assert stage.evaluate(pd.Series(bank("B", roe_avg_3y=0.08)))[0].rule == "min_roe_avg_3y"
    assert stage.evaluate(pd.Series(bank("B", share_dilution_cagr=0.10)))[0].rule == "max_share_dilution"


def test_growth_rules(base_config):
    stage = GrowthStage(base_config["stages"]["growth"], base_config)
    assert stage.evaluate(series()) == []
    assert stage.evaluate(series(revenue_cagr_3y=0.01))[0].rule == "min_revenue_cagr_3y"
    assert stage.evaluate(series(revenue_cagr_3y=NAN))[0].description.startswith("dados insuficientes")
    assert stage.evaluate(series(business_type="cyclical_commodity", revenue_cagr_3y=0.01)) == []
    assert stage.evaluate(series(business_type="cyclical_commodity", revenue_cagr_3y=-0.01))[0].threshold == 0.0
    assert stage.evaluate(series(eps_consecutive_declines=2.0))[0].rule == "max_eps_declines"
    assert stage.evaluate(series(eps_consecutive_declines=1.0)) == []
    assert stage.evaluate(series(revenue_growth_est_next_fy=-0.05))[0].rule == "expected_revenue_growth"
    assert stage.evaluate(series(revenue_growth_est_next_fy=NAN)) == []
    notes = stage.warnings(series(revenue_cagr_span_years=2, eps_consecutive_declines=NAN, revenue_growth_est_next_fy=NAN))
    assert len(notes) == 3 and "2 anos" in notes[0]


def test_valuation_rules(base_config):
    stage = ValuationStage(base_config["stages"]["valuation"], base_config)
    assert stage.evaluate(series()) == []
    assert stage.evaluate(series(fcf_yield_sector_pct=30.0))[0].rule == "min_fcf_yield_sector_pct"
    assert stage.evaluate(series(fcf_yield_sector_pct=NAN))[0].description.startswith("dados insuficientes: FCF yield")
    assert stage.evaluate(series(ev_to_ebit_sector_pct=80.0))[0].rule == "max_ev_ebit_sector_pct"
    assert stage.evaluate(series(ev_to_ebit_sector_pct=NAN))[0].rule == "max_ev_ebit_sector_pct"
    assert stage.evaluate(series(forward_pe=45.0))[0].rule == "forward_pe_range"
    assert stage.evaluate(series(forward_pe=-5.0))[0].rule == "forward_pe_range"
    assert stage.evaluate(series(forward_pe=NAN)) == [] and "Forward P/E" in stage.warnings(series(forward_pe=NAN))[0]
    small = series(sector_size=3, fcf_yield_sector_pct=10.0)
    assert stage.evaluate(small) == []
    notes = stage.warnings(small)
    assert "setor com 3 empresas" in notes[0] and notes[1].startswith("(aviso) FCF yield")
    assert "PEG de 3.0" in stage.warnings(series(peg_ratio=3.0))[0]
    # bancos e REITs
    assert stage.evaluate(pd.Series(bank("B"))) == []
    assert stage.evaluate(pd.Series(bank("B", price_to_book_sector_pct=85.0)))[0].rule == "max_pb_sector_pct"
    assert stage.evaluate(pd.Series(bank("B", forward_pe=25.0)))[0].rule == "max_forward_pe"
    reit = series(business_type="reit", dividend_yield=0.04, p_ffo=18.0)
    assert stage.evaluate(reit) == []
    assert stage.evaluate(series(business_type="reit", dividend_yield=0.02, p_ffo=18.0))[0].rule == "min_dividend_yield"
    assert stage.evaluate(series(business_type="reit", dividend_yield=0.04, p_ffo=30.0))[0].rule == "max_p_ffo"
    reit_missing = series(business_type="reit", dividend_yield=NAN, p_ffo=NAN)
    assert stage.evaluate(reit_missing) == [] and len(stage.warnings(reit_missing)) == 2


def test_momentum_rules(base_config):
    stage = MomentumStage(base_config["stages"]["momentum"], base_config)
    assert stage.evaluate(series()) == []
    assert stage.evaluate(series(momentum_12_1=-0.25))[0].rule == "min_momentum_12_1"
    assert stage.evaluate(series(momentum_12_1=-0.20))[0].rule == "min_momentum_12_1"  # > -20% exigido
    knife = series(momentum_12_1=-0.15, price=40.0, sma_200=45.0)
    assert stage.evaluate(knife) == [] and stage.warnings(knife)[0].startswith("faca a cair")
    assert stage.warnings(series(momentum_12_1=-0.15, price=50.0, sma_200=45.0)) == []
    assert stage.evaluate(series(momentum_12_1=NAN)) == [] and "histórico" in stage.warnings(series(momentum_12_1=NAN))[0]


def test_merged_type_config():
    cfg = {"default": {"a": 1, "b": 2}, "by_type": {"reit": {"b": 3}, "standard": {"a": 9}}}
    assert merged_type_config(cfg, "reit") == {"a": 1, "b": 3}
    assert merged_type_config(cfg, "utility") == {"a": 9, "b": 2}  # recorre a standard
    assert merged_type_config({"by_type": {"standard": {"x": 1}}}, "standard") == {"x": 1}


# ----------------------------------------------------------------------------- funil completo
def test_full_funnel_eliminates_at_first_failing_stage(base_config):
    rows = [
        company("GOOD"),
        company("SMALL", market_cap=1e9),
        company("NODATA", ebit=NAN, capex=NAN),
        company("LEVERED", net_debt_to_ebitda=4.0),
        company("NOCOVER", interest_coverage=2.0),
        company("NETCASH", net_debt=-1e8, net_debt_to_ebitda=NAN, interest_coverage=NAN),
        company("LOWF", piotroski_f=3.0),
        company("MANIP", beneish_m=-1.0),
        company("LOWROIC", roic_avg_3y=0.05),
        company("DILUTE", share_dilution_cagr=0.05, roic_avg_3y=0.05),  # duas regras... mas ROIC também falha
        company("NOGROWTH", revenue_cagr_3y=0.01),
        company("NOGROWCYC", business_type="cyclical_commodity", revenue_cagr_3y=0.01),
        company("EPSDOWN", eps_consecutive_declines=2.0),
        company("EXPENSIVE", fcf_yield_sector_pct=30.0, ev_to_ebit_sector_pct=80.0),
        company("PEGWARN", peg_ratio=3.0),
        company("MOM", momentum_12_1=-0.25),
        company("KNIFE", momentum_12_1=-0.15, price=40.0, sma_200=45.0),
        bank("BANKOK"),
        bank("BANKPE", forward_pe=25.0),
        company("REITOK", business_type="reit", dividend_yield=0.04, p_ffo=18.0, net_debt_to_ebitda=6.0),
        company("UTIL", business_type="utility", net_debt_to_ebitda=5.0, interest_coverage=3.0, altman_z=NAN, current_ratio=NAN),
    ]
    frame = pd.DataFrame(rows)
    stages = build_stages(base_config)
    assert [s.name for s in stages] == ["eligibility", "financial_health", "quality", "growth", "valuation", "momentum"]
    result = FunnelEngine(stages).run(frame)

    survivors = set(result.survivors["ticker"])
    assert survivors == {"GOOD", "NETCASH", "NOGROWCYC", "PEGWARN", "KNIFE", "BANKOK", "REITOK", "UTIL"}

    by_ticker = {e.ticker: e for e in result.eliminations}
    expected = {
        "SMALL": ("eligibility", "min_market_cap"),
        "NODATA": ("eligibility", "critical_fields"),
        "LEVERED": ("financial_health", "max_net_debt_to_ebitda"),
        "NOCOVER": ("financial_health", "min_interest_coverage"),
        "LOWF": ("financial_health", "min_piotroski"),
        "MANIP": ("financial_health", "max_beneish_m"),
        "LOWROIC": ("quality", "min_roic_avg_3y"),
        "DILUTE": ("quality", "min_roic_avg_3y"),
        "NOGROWTH": ("growth", "min_revenue_cagr_3y"),
        "EPSDOWN": ("growth", "max_eps_declines"),
        "EXPENSIVE": ("valuation", "min_fcf_yield_sector_pct"),
        "MOM": ("momentum", "min_momentum_12_1"),
        "BANKPE": ("valuation", "max_forward_pe"),
    }
    for ticker, (stage, rule) in expected.items():
        assert by_ticker[ticker].stage == stage, ticker
        assert by_ticker[ticker].failures[0].rule == rule, ticker
    assert len(by_ticker["NODATA"].failures) == 2
    assert {f.rule for f in by_ticker["DILUTE"].failures} == {"min_roic_avg_3y", "max_share_dilution"}
    assert {f.rule for f in by_ticker["EXPENSIVE"].failures} == {"min_fcf_yield_sector_pct", "max_ev_ebit_sector_pct"}

    warnings = dict(zip(result.survivors["ticker"], result.survivors["funnel_warnings"]))
    assert warnings["GOOD"] == ""
    assert "faca a cair" in warnings["KNIFE"]
    assert "PEG de 3.0" in warnings["PEGWARN"]
    assert "dívida imaterial" in warnings["NETCASH"]

    counts = {c.name: c.passed for c in result.stage_counts}
    assert counts["download"] == 21
    assert counts["eligibility"] == 19
    assert counts["financial_health"] == 15
    assert counts["quality"] == 13
    assert counts["growth"] == 11
    assert counts["valuation"] == 9
    assert counts["momentum"] == 8
    text = result.summary_text(top_n=20)
    assert text.startswith("21 empresas → Etapa 0 – Dados: 21 → Etapa 1 – Elegibilidade e dados: 19")
    assert "Top 8" in text
    table = result.eliminations_table()
    assert set(table["ticker"]) == set(expected)
    assert math.isnan(float("nan"))  # sanidade do import

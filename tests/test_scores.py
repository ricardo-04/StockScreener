"""Piotroski F-Score, Altman Z-Score e Beneish M-Score com dados sintéticos."""
from __future__ import annotations

import math

import pandas as pd
import pytest

from src.metrics.scores import (
    BENEISH_COEFFICIENTS, BENEISH_INTERCEPT, altman_z_score, beneish_components, beneish_m_score,
    piotroski_f_score,
)


def two_years(latest: dict[str, float], previous: dict[str, float]) -> pd.DataFrame:
    """Frame alinhado por ano fiscal: linha 0 = mais recente, linha 1 = anterior."""
    return pd.DataFrame([latest, previous], index=[2025, 2024]).astype(float)


# ----------------------------------------------------------------------------- Piotroski
def test_piotroski_perfect_company_scores_nine():
    frame = two_years(
        {"net_income": 150, "total_assets": 2000, "operating_cash_flow": 220, "long_term_debt": 300,
         "current_assets": 600, "current_liabilities": 250, "shares": 95, "gross_profit": 520, "revenue": 1000},
        {"net_income": 100, "total_assets": 1900, "operating_cash_flow": 150, "long_term_debt": 320,
         "current_assets": 500, "current_liabilities": 250, "shares": 100, "gross_profit": 450, "revenue": 900},
    )
    assert piotroski_f_score(frame) == (9.0, 9)


def test_piotroski_worst_company_scores_zero():
    frame = two_years(
        {"net_income": -50, "total_assets": 2000, "operating_cash_flow": -60, "long_term_debt": 500,
         "current_assets": 400, "current_liabilities": 300, "shares": 110, "gross_profit": 300, "revenue": 800},
        {"net_income": 10, "total_assets": 1900, "operating_cash_flow": 20, "long_term_debt": 300,
         "current_assets": 500, "current_liabilities": 250, "shares": 100, "gross_profit": 450, "revenue": 900},
    )
    assert piotroski_f_score(frame) == (0.0, 9)


def test_piotroski_counts_only_evaluable_criteria():
    frame = two_years(
        {"net_income": 150, "total_assets": 2000, "operating_cash_flow": 220, "total_debt": 300,
         "shares": 95, "revenue": 1000},
        {"net_income": 100, "total_assets": 1900, "operating_cash_flow": 150, "total_debt": 320,
         "shares": 100, "revenue": 900},
    )
    score, evaluated = piotroski_f_score(frame)
    assert evaluated == 7  # sem current ratio nem margem bruta
    assert score == 7.0
    assert piotroski_f_score(pd.DataFrame()) == (pytest.approx(float("nan"), nan_ok=True), 0)


def test_piotroski_needs_two_years_for_deltas():
    frame = pd.DataFrame([{"net_income": 10, "total_assets": 100, "operating_cash_flow": 20}], index=[2025]).astype(float)
    score, evaluated = piotroski_f_score(frame)
    assert (score, evaluated) == (3.0, 3)


# ----------------------------------------------------------------------------- Altman
def test_altman_z_known_value():
    z = altman_z_score(working_capital=100, retained_earnings=200, ebit=50, total_assets=1000,
                       market_cap=600, total_liabilities=500, revenue=800)
    assert z == pytest.approx(1.2 * 0.1 + 1.4 * 0.2 + 3.3 * 0.05 + 0.6 * 1.2 + 0.8)


def test_altman_z_nan_when_input_missing_or_invalid():
    assert math.isnan(altman_z_score(100, 200, 50, 1000, None, 500, 800))
    assert math.isnan(altman_z_score(100, 200, 50, 0, 600, 500, 800))
    assert math.isnan(altman_z_score(100, 200, 50, 1000, 600, 0, 800))


# ----------------------------------------------------------------------------- Beneish
STEADY = {
    "revenue": 1000, "receivables": 100, "cost_of_revenue": 600, "current_assets": 400, "net_ppe": 300,
    "total_assets": 1000, "depreciation_amortization": 30, "sga_expense": 150, "long_term_debt": 200,
    "current_liabilities": 150, "net_income": 80, "operating_cash_flow": 80,
}


def test_beneish_steady_state_company():
    frame = two_years(dict(STEADY), dict(STEADY))
    components = beneish_components(frame)
    assert all(v == pytest.approx(1.0) for k, v in components.items() if k != "tata")
    assert components["tata"] == 0.0
    expected = BENEISH_INTERCEPT + sum(BENEISH_COEFFICIENTS[k] for k in BENEISH_COEFFICIENTS if k != "tata")
    assert beneish_m_score(frame) == pytest.approx(expected)
    assert beneish_m_score(frame) < -1.78


def test_beneish_flags_receivables_jump_and_accruals():
    latest = dict(STEADY, receivables=200, net_income=180)  # DSRI = 2, TATA = 0.10
    frame = two_years(latest, dict(STEADY))
    steady = beneish_m_score(two_years(dict(STEADY), dict(STEADY)))
    manipulated = beneish_m_score(frame)
    assert manipulated == pytest.approx(steady + 0.920 * 1.0 + 4.679 * 0.10)
    assert manipulated > -1.78


def test_beneish_uses_gross_profit_when_cogs_missing_and_nan_when_sga_missing():
    with_gp = {k: v for k, v in STEADY.items() if k != "cost_of_revenue"}
    with_gp["gross_profit"] = 400
    assert not math.isnan(beneish_m_score(two_years(dict(with_gp), dict(with_gp))))
    without_sga = {k: v for k, v in STEADY.items() if k != "sga_expense"}
    assert math.isnan(beneish_m_score(two_years(dict(without_sga), dict(without_sga))))
    assert math.isnan(beneish_m_score(pd.DataFrame()))

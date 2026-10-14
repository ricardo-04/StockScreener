"""ROIC, ROE, conversão de caixa, margem bruta, diluição, CAGR e quedas consecutivas."""
from __future__ import annotations

import math

import pandas as pd
import pytest

from src.metrics.growth import cagr, consecutive_declines, series_cagr
from src.metrics.quality import (
    average_of_last, cash_conversion, count_positive_of_last, gross_margin_std_pp, invested_capital, nopat,
    resolve_tax_rate, roe, roe_series, roic, roic_series, share_count_cagr,
)

YEARS = [2025, 2024, 2023, 2022]


def s(values):
    return pd.Series(values, index=YEARS[: len(values)], dtype=float)


# ----------------------------------------------------------------------------- ROIC / ROE
def test_roic_formula_and_edge_cases():
    assert invested_capital(900, 400, 150) == 1150
    assert nopat(200, 0.2) == pytest.approx(160)
    assert roic(200, 0.2, 900, 400, 150) == pytest.approx(160 / 1150)
    assert math.isnan(roic(200, 0.2, 100, 0, 200))  # capital investido negativo
    assert math.isnan(roic(None, 0.2, 900, 400, 150))
    assert math.isnan(invested_capital(900, None, 150))


def test_resolve_tax_rate_chain_and_clamp():
    assert resolve_tax_rate(38, 190, None, 0.10, 0.30, 0.21) == pytest.approx(0.20)
    assert resolve_tax_rate(None, 190, 0.25, 0.10, 0.30, 0.21) == pytest.approx(0.25)
    assert resolve_tax_rate(None, None, None, 0.10, 0.30, 0.21) == pytest.approx(0.21)
    assert resolve_tax_rate(5, 190, None, 0.10, 0.30, 0.21) == pytest.approx(0.10)  # 2.6% -> piso
    assert resolve_tax_rate(100, 190, None, 0.10, 0.30, 0.21) == pytest.approx(0.30)  # 53% -> teto


def test_roic_series_and_average():
    frame = pd.DataFrame(
        {"ebit": [200, 180, 160], "tax_provision": [38, 34, 30], "pretax_income": [190, 170, 150],
         "equity": [900, 800, 700], "total_debt": [400, 400, 400], "cash": [150, 130, 110]},
        index=YEARS[:3],
    ).astype(float)
    values = roic_series(frame, 0.10, 0.30, 0.21)
    assert values.iloc[0] == pytest.approx(160 / 1150)
    assert values.iloc[2] == pytest.approx(128 / 990)
    avg, used = average_of_last(values, 3)
    assert used == 3 and avg == pytest.approx(values.mean())
    assert average_of_last(None, 3) == (pytest.approx(float("nan"), nan_ok=True), 0)
    assert roic_series(pd.DataFrame(), 0.1, 0.3, 0.21).empty


def test_roe_series_and_positive_count():
    assert roe(152, 900) == pytest.approx(152 / 900)
    assert math.isnan(roe(152, -10))
    frame = pd.DataFrame({"net_income": [100, -20, 50], "equity": [500, 480, 450]}, index=YEARS[:3]).astype(float)
    values = roe_series(frame)
    assert count_positive_of_last(values, 3) == (2, 3)
    assert count_positive_of_last(values, 1) == (1, 1)


# ----------------------------------------------------------------------------- conversão de caixa
def test_cash_conversion_uses_sums_over_window():
    fcf = s([160, 145, 130, -55])
    net_income = s([152, 136, 120, 104])
    value, years = cash_conversion(fcf, net_income, 3)
    assert years == 3 and value == pytest.approx(435 / 408)
    assert cash_conversion(fcf, s([-10, -20, -30]), 3) == (pytest.approx(float("nan"), nan_ok=True), 3)
    assert cash_conversion(s([10]), s([5]), 3)[1] == 1  # só 1 ano: NaN
    assert math.isnan(cash_conversion(None, net_income, 3)[0])


# ----------------------------------------------------------------------------- margem bruta e diluição
def test_gross_margin_std_in_percentage_points():
    gp = s([500, 450, 400, 350])
    revenue = s([1000, 900, 800, 700])
    assert gross_margin_std_pp(gp, revenue, 4) == pytest.approx(0.0)
    volatile = gross_margin_std_pp(s([600, 360, 400, 210]), revenue, 4)  # margens 60%, 40%, 50%, 30%
    assert volatile == pytest.approx(pd.Series([0.6, 0.4, 0.5, 0.3]).std(ddof=1) * 100)
    assert math.isnan(gross_margin_std_pp(gp.head(2), revenue.head(2), 4))


def test_share_count_cagr_and_span():
    value, span = share_count_cagr(s([110, 105, 100, 95]), 3)
    assert span == 3 and value == pytest.approx((110 / 95) ** (1 / 3) - 1)
    value, span = share_count_cagr(s([100, float("nan"), 90]), 3)
    assert span == 2 and value == pytest.approx((100 / 90) ** 0.5 - 1)
    assert share_count_cagr(s([100]), 3) == (pytest.approx(float("nan"), nan_ok=True), 0)
    assert share_count_cagr(None, 3)[1] == 0


# ----------------------------------------------------------------------------- crescimento
def test_cagr_and_series_cagr():
    assert cagr(700, 1000, 3) == pytest.approx((1000 / 700) ** (1 / 3) - 1)
    assert math.isnan(cagr(-5, 100, 3))
    assert math.isnan(cagr(100, 100, 0))
    value, span = series_cagr(s([1000, 900, 800, 700]), 3)
    assert span == 3 and value == pytest.approx((1000 / 700) ** (1 / 3) - 1)
    value, span = series_cagr(s([1000, 900, 800]), 3)  # só 3 anos: intervalo de 2
    assert span == 2 and value == pytest.approx((1000 / 800) ** 0.5 - 1)
    assert series_cagr(s([float("nan"), 900]), 3) == (pytest.approx(float("nan"), nan_ok=True), 0)


def test_consecutive_declines():
    assert consecutive_declines(s([1.52, 1.36, 1.20])) == 0
    assert consecutive_declines(s([1.00, 1.36, 1.20])) == 1
    assert consecutive_declines(s([1.00, 1.10, 1.20, 1.00])) == 2
    assert consecutive_declines(s([1.00, 1.10, 1.20, 1.30])) == 3
    assert math.isnan(consecutive_declines(s([1.00, float("nan"), 1.20])))
    assert math.isnan(consecutive_declines(s([1.00])))

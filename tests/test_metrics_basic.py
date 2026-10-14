"""Testes das funções puras: FCF, dívida, cobertura de juros, margens, taxa de imposto."""
from __future__ import annotations

import math

import pandas as pd
import pytest

from src.metrics.cashflow import count_positive, free_cash_flow, free_cash_flow_series
from src.metrics.leverage import (
    DEBT_FREE, HAS_DEBT, UNKNOWN, current_ratio, debt_status, interest_coverage,
    is_net_cash, net_debt, net_debt_to_ebitda,
)
from src.metrics.profitability import clamp_tax_rate, effective_tax_rate, margin
from src.metrics.statements import align_series, latest_value, latest_value_with_source, to_float
from tests.conftest import FISCAL_YEARS, make_statement


# ----------------------------------------------------------------------------- FCF
def test_fcf_is_ocf_minus_capex_regardless_of_capex_sign():
    assert free_cash_flow(220.0, -60.0) == 160.0
    assert free_cash_flow(220.0, 60.0) == 160.0


def test_fcf_is_nan_when_capex_or_ocf_missing():
    assert math.isnan(free_cash_flow(220.0, None))
    assert math.isnan(free_cash_flow(float("nan"), -60.0))
    assert math.isnan(free_cash_flow(None, None))


def test_fcf_series_aligns_periods_and_counts_positive_years():
    ocf = pd.Series([220.0, 200.0, 180.0, -10.0], index=FISCAL_YEARS)
    capex = pd.Series([-60.0, -55.0, float("nan"), -45.0], index=FISCAL_YEARS)
    series = free_cash_flow_series(ocf, capex)
    assert list(series.index) == FISCAL_YEARS
    assert series.iloc[0] == 160.0
    assert math.isnan(series.iloc[2])  # sem CapEx nesse ano: não se inventa
    assert series.iloc[3] == -55.0
    assert count_positive(series, last_n=4) == (2, 3)
    assert count_positive(None) == (0, 0)


# ----------------------------------------------------------------------------- dívida
def test_net_debt_and_net_cash():
    assert net_debt(400.0, 150.0) == 250.0
    assert math.isnan(net_debt(None, 150.0))
    assert is_net_cash(-10.0) is True
    assert is_net_cash(float("nan")) is False


def test_net_debt_to_ebitda_requires_positive_ebitda():
    assert net_debt_to_ebitda(250.0, 250.0) == 1.0
    assert math.isnan(net_debt_to_ebitda(250.0, 0.0))
    assert math.isnan(net_debt_to_ebitda(250.0, -50.0))
    assert math.isnan(net_debt_to_ebitda(250.0, None))


def test_interest_coverage_is_nan_without_interest_expense():
    assert interest_coverage(200.0, 10.0) == 20.0
    assert interest_coverage(200.0, -10.0) == 20.0  # sinal irrelevante
    assert math.isnan(interest_coverage(200.0, 0.0))  # nunca o 25x arbitrário antigo
    assert math.isnan(interest_coverage(200.0, None))
    assert math.isnan(interest_coverage(None, 10.0))


def test_debt_status_is_explicit():
    assert debt_status(0.0, None) == DEBT_FREE
    assert debt_status(400.0, None) == HAS_DEBT
    assert debt_status(None, 0.0) == DEBT_FREE
    assert debt_status(None, 12.0) == HAS_DEBT
    assert debt_status(None, None) == UNKNOWN


def test_current_ratio():
    assert current_ratio(600.0, 300.0) == 2.0
    assert math.isnan(current_ratio(600.0, 0.0))


# ----------------------------------------------------------------------------- margens e impostos
def test_margin_and_tax_rate():
    assert margin(500.0, 1000.0) == 0.5
    assert math.isnan(margin(500.0, 0.0))
    assert effective_tax_rate(38.0, 190.0) == pytest.approx(0.2)
    assert math.isnan(effective_tax_rate(38.0, -10.0))
    assert math.isnan(effective_tax_rate(-5.0, 190.0))  # benefício fiscal: não interpretável
    assert clamp_tax_rate(0.05, 0.10, 0.30) == 0.10
    assert clamp_tax_rate(0.45, 0.10, 0.30) == 0.30
    assert clamp_tax_rate(0.21, 0.10, 0.30) == 0.21
    assert math.isnan(clamp_tax_rate(float("nan"), 0.10, 0.30))


# ----------------------------------------------------------------------------- acesso às demonstrações
def test_latest_value_does_not_fall_back_to_older_years():
    frame = make_statement({
        "Total Revenue": [100.0, 90.0, 80.0, 70.0],  # define os períodos da demonstração
        "Interest Expense": [float("nan"), float("nan"), 3.9, 2.9],
    })
    assert math.isnan(latest_value(frame, ["Interest Expense"]))
    assert latest_value(frame, ["Total Revenue"]) == 100.0


def test_latest_value_with_source_uses_first_candidate_with_value():
    frame = make_statement({
        "Interest Expense": [float("nan"), 1.0, 1.0, 1.0],
        "Interest Expense Non Operating": [7.0, 1.0, 1.0, 1.0],
    })
    assert latest_value_with_source(frame, ["Interest Expense", "Interest Expense Non Operating"]) == (7.0, "Interest Expense Non Operating")
    assert latest_value_with_source(pd.DataFrame(), ["X"]) == (pytest.approx(float("nan"), nan_ok=True), None)


def test_align_series_outer_joins_periods():
    a = pd.Series([1.0, 2.0], index=FISCAL_YEARS[:2])
    b = pd.Series([3.0, 4.0, 5.0], index=FISCAL_YEARS[1:])
    frame = align_series(a=a, b=b)
    assert list(frame.index) == FISCAL_YEARS
    assert math.isnan(frame.loc[FISCAL_YEARS[0], "b"])
    assert frame.loc[FISCAL_YEARS[1], "b"] == 3.0


def test_to_float_handles_garbage():
    assert to_float("12.5") == 12.5
    assert math.isnan(to_float("abc"))
    assert math.isnan(to_float(None))
    assert math.isnan(to_float(pd.NA))

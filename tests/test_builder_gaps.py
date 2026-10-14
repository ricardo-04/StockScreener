"""Tratamento de lacunas do Yahoo no builder: CapEx alternativo, ativo derivado, juros do ano anterior, dívida zero."""
from __future__ import annotations

import math

import pandas as pd
import pytest

from src.metrics.builder import build_metrics_row
from src.metrics.statements import get_row_combined
from tests.conftest import FISCAL_YEARS, make_statement, make_ticker_data, standard_balance, standard_cashflow, standard_income

NAN = float("nan")


def test_get_row_combined_merges_candidates_per_period():
    frame = make_statement({
        "Capital Expenditure": [NAN, -55.0, -50.0, NAN],
        "Purchase Of PPE": [-58.0, -99.0, NAN, -45.0],
    })
    combined = get_row_combined(frame, ["Capital Expenditure", "Purchase Of PPE"])
    assert list(combined.index) == FISCAL_YEARS
    assert combined.tolist() == [-58.0, -55.0, -50.0, -45.0]  # a primeira candidata vence onde existe
    assert get_row_combined(frame, ["Inexistente"]) is None
    assert get_row_combined(pd.DataFrame(), ["Capital Expenditure"]) is None


def test_capex_falls_back_to_purchase_of_ppe_per_year(base_config):
    cashflow = standard_cashflow()
    cashflow["Capital Expenditure"] = [NAN, -55.0, -50.0, -45.0]
    cashflow["Purchase Of PPE"] = [-58.0, -55.0, -50.0, -45.0]
    row = build_metrics_row(make_ticker_data(cashflow=cashflow), base_config)
    assert row["capex"] == -58.0
    assert row["capex_source"] == "Purchase Of PPE"
    assert row["free_cash_flow"] == 162.0
    assert row["free_cash_flow_fy1"] == 145.0
    assert (row["fcf_positive_years"], row["fcf_years_observed"]) == (3, 4)


def test_capex_missing_in_both_rows_stays_nan(base_config):
    cashflow = standard_cashflow()
    cashflow["Capital Expenditure"] = [NAN, -55.0, -50.0, -45.0]
    cashflow["Purchase Of PPE"] = [NAN, -55.0, -50.0, -45.0]
    row = build_metrics_row(make_ticker_data(cashflow=cashflow), base_config)
    assert math.isnan(row["free_cash_flow"])
    assert row["fcf_years_observed"] == 3


def test_total_assets_derived_from_liabilities_plus_equity(base_config):
    balance = standard_balance()
    balance["Total Assets"] = [NAN, 1800.0, 1600.0, 1400.0]
    balance["Total Equity Gross Minority Interest"] = [900.0, 800.0, 700.0, 600.0]
    row = build_metrics_row(make_ticker_data(balance=balance), base_config)
    assert row["total_assets"] == 2000.0  # 1100 + 900
    assert row["total_assets_source"].startswith("derivado")
    assert "ativo total" in row["data_notes"]
    assert not math.isnan(row["altman_z"])
    assert (row["piotroski_f"], row["piotroski_criteria"]) == (6.0, 9)  # ROA continua avaliável


def test_total_assets_not_derived_without_both_parts(base_config):
    balance = standard_balance()
    balance["Total Assets"] = [NAN, 1800.0, 1600.0, 1400.0]
    row = build_metrics_row(make_ticker_data(balance=balance), base_config)
    assert math.isnan(row["total_assets"])
    assert row["total_assets_source"] is None


def test_interest_expense_uses_previous_year_only_when_latest_is_missing(base_config):
    income = standard_income()
    income["Interest Expense"] = [NAN, 12.0, 10.0, 10.0]
    row = build_metrics_row(make_ticker_data(income=income), base_config)
    assert row["interest_expense"] == 12.0
    assert "último ano em falta" in row["interest_expense_source"]
    assert row["interest_coverage"] == pytest.approx(200 / 12)
    assert "ano anterior" in row["data_notes"]
    assert row["debt_status"] == "com_divida"


def test_interest_expense_missing_two_years_is_not_backfilled(base_config):
    income = standard_income()
    income["Interest Expense"] = [NAN, NAN, 10.0, 10.0]  # caso Apple: dois anos em falta
    row = build_metrics_row(make_ticker_data(income=income), base_config)
    assert math.isnan(row["interest_expense"])
    assert row["interest_expense_source"] is None


def test_cashflow_interest_paid_is_preferred_over_previous_year(base_config):
    income = standard_income()
    income["Interest Expense"] = [NAN, 12.0, 10.0, 10.0]
    cashflow = standard_cashflow()
    cashflow["Interest Paid Supplemental Data"] = [9.0, 9.0, 9.0, 9.0]
    row = build_metrics_row(make_ticker_data(income=income, cashflow=cashflow), base_config)
    assert row["interest_expense"] == 9.0
    assert row["interest_expense_source"] == "Interest Paid Supplemental Data"


def test_debt_assumed_zero_even_with_lease_interest_in_cashflow(base_config):
    balance = standard_balance()
    balance.pop("Total Debt")
    income = standard_income()
    income.pop("Interest Expense")
    cashflow = standard_cashflow()
    cashflow["Interest Paid Supplemental Data"] = [1.0, 1.0, 1.0, 1.0]  # juros de leasing
    row = build_metrics_row(make_ticker_data(income=income, balance=balance, cashflow=cashflow), base_config)
    assert row["total_debt"] == 0.0
    assert row["total_debt_source"].startswith("assumido 0")
    assert row["debt_status"] == "sem_divida"
    assert row["interest_expense"] == 1.0
    assert row["roic_years_observed"] == 3
    assert row["roic_fy0"] == pytest.approx(160 / (900 - 150))


def test_debt_not_assumed_zero_when_interest_expense_exists(base_config):
    balance = standard_balance()
    balance.pop("Total Debt")
    row = build_metrics_row(make_ticker_data(balance=balance), base_config)  # Interest Expense = 10
    assert math.isnan(row["total_debt"])
    assert row["total_debt_source"] is None
    assert row["debt_status"] == "com_divida"


def test_debt_row_missing_only_in_latest_year_is_zero_when_no_interest(base_config):
    balance = standard_balance()
    balance["Total Debt"] = [NAN, 374.0, NAN, NAN]  # caso Monster: leasing só num ano
    income = standard_income()
    income.pop("Interest Expense")
    row = build_metrics_row(make_ticker_data(income=income, balance=balance), base_config)
    assert row["total_debt"] == 0.0
    assert row["debt_status"] == "sem_divida"
    assert row["roic_years_observed"] == 3  # série de dívida preenchida a zero nos anos em falta

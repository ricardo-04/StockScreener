"""Tratamento de lacunas do Yahoo no builder: CapEx alternativo, ativo derivado, juros do ano anterior, dívida zero."""
from __future__ import annotations

import math

import pandas as pd
import pytest

from src.metrics.builder import build_metrics_row, fiscal_year_index
from src.metrics.statements import get_row_combined
from tests.conftest import (
    FISCAL_YEARS, make_statement, make_ticker_data, standard_balance, standard_cashflow, standard_income,
    standard_info,
)

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


def test_debt_not_assumed_zero_with_persistent_prior_debt_or_material_cf_interest(base_config):
    # Caso 1: dívida persistente nos anos anteriores (FY1..FY3), mesmo sem Interest Expense em FY0/FY1
    balance = standard_balance()
    balance["Total Debt"] = [NAN, 500.0, 480.0, 460.0]
    income = standard_income()
    income.pop("Interest Expense")
    row = build_metrics_row(make_ticker_data(income=income, balance=balance), base_config)
    assert math.isnan(row["total_debt"])
    assert row["total_debt_source"] is None

    # Caso 2: juros pagos materiais no cash flow (>= 1% da receita) sem rubricas de dívida no balanço
    balance_no_debt = standard_balance()
    balance_no_debt.pop("Total Debt")
    cashflow = standard_cashflow()
    cashflow["Interest Paid Supplemental Data"] = [50.0, 50.0, 50.0, 50.0]  # 5% da receita
    row2 = build_metrics_row(make_ticker_data(income=income, balance=balance_no_debt, cashflow=cashflow), base_config)
    assert math.isnan(row2["total_debt"])
    assert row2["total_debt_source"] is None


def test_currency_mismatch_nulls_mixed_ratios_and_adds_note(base_config):
    info = standard_info()
    info["currency"] = "USD"
    info["financialCurrency"] = "JPY"
    row = build_metrics_row(make_ticker_data(info=info), base_config)
    assert math.isnan(row["fcf_yield"])
    assert math.isnan(row["ev_to_ebit"])
    assert math.isnan(row["p_ffo"])
    assert math.isnan(row["altman_z"])
    assert "moeda de mercado (USD) difere da moeda das demonstrações (JPY)" in row["data_notes"]

    # Case-insensitive match não dispara a guarda
    info_same = standard_info()
    info_same["currency"] = "USD"
    info_same["financialCurrency"] = "usd"
    row_same = build_metrics_row(make_ticker_data(info=info_same), base_config)
    assert not math.isnan(row_same["fcf_yield"])
    assert not math.isnan(row_same["ev_to_ebit"])
    assert not math.isnan(row_same["altman_z"])


def test_fiscal_year_index_handles_52_53_week_calendar_and_nan_duplicate():
    # Dois fechos fiscais no mesmo ano civil (52/53 semanas): ambos preservados
    s52 = pd.Series(
        [100.0, 90.0, 80.0, 70.0],
        index=[
            pd.Timestamp("2024-12-28"),
            pd.Timestamp("2024-01-01"),
            pd.Timestamp("2022-12-31"),
            pd.Timestamp("2021-12-31"),
        ],
    )
    indexed = fiscal_year_index(s52)
    assert list(indexed.index) == [2024, 2023, 2022, 2021]
    assert indexed.tolist() == [100.0, 90.0, 80.0, 70.0]

    # Coluna duplicada no mesmo período com NaN não apaga valor válido
    s_dup = pd.Series(
        [NAN, 90.0, 80.0],
        index=[pd.Timestamp("2024-12-31"), pd.Timestamp("2024-12-30"), pd.Timestamp("2023-12-31")],
    )
    indexed_dup = fiscal_year_index(s_dup)
    assert list(indexed_dup.index) == [2024, 2023]
    assert indexed_dup.tolist() == [90.0, 80.0]


def test_partial_fy0_fallback_populates_multiyear_ebit_debt_and_shares(base_config):
    income = standard_income()
    income["Operating Income"] = [NAN, 180.0, 160.0, 140.0]  # FY0 cai para EBIT (205.0)
    income["Diluted Average Shares"] = [NAN, 100.0, 100.0, 100.0]  # FY0 cai para Ordinary Shares Number (100.0)
    balance = standard_balance()
    balance["Total Debt"] = [NAN, 400.0, 400.0, 400.0]  # FY0 cai para Long Term Debt + Current Debt (300 + 100)
    balance["Long Term Debt"] = [300.0, 300.0, 300.0, 300.0]
    balance["Current Debt"] = [100.0, 100.0, 100.0, 100.0]
    row = build_metrics_row(make_ticker_data(income=income, balance=balance), base_config)
    assert row["ebit"] == 205.0
    assert row["total_debt"] == 400.0
    assert row["shares_fy0"] == 100.0
    assert row["roic_fy0"] == pytest.approx((205.0 * 0.8) / (900.0 + 400.0 - 150.0))
    assert row["roic_years_observed"] == 3
    assert row["share_dilution_cagr"] == pytest.approx(0.0)

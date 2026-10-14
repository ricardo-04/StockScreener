"""Testes do construtor da tabela de métricas (correções de dados da Fase 1)."""
from __future__ import annotations

import math

import pandas as pd
import pytest

from src.metrics.builder import build_metrics_row, build_metrics_table, normalize_dividend_yield
from tests.conftest import (
    FISCAL_YEARS, make_ticker_data, standard_balance, standard_cashflow, standard_income, standard_info,
)


def test_standard_company_core_metrics(standard_ticker_data, base_config):
    row = build_metrics_row(standard_ticker_data, base_config)

    assert row["ticker"] == "TEST"
    assert row["company"] == "Empresa Exemplo S.A."
    assert row["business_type"] == "standard"
    assert row["years_of_statements"] == 4
    assert row["latest_fiscal_year_end"] == "2025-12-31"

    # FCF = OCF − |CapEx|, com histórico de 4 anos (3 positivos)
    assert row["free_cash_flow"] == 160.0
    assert (row["fcf_positive_years"], row["fcf_years_observed"]) == (3, 4)
    assert row["free_cash_flow_fy3"] == -55.0

    # EBIT vem de Operating Income (nunca de Operating Revenue nem do "EBIT" do yfinance)
    assert row["ebit"] == 200.0
    assert row["ebit_source"] == "Operating Income"
    assert row["operating_margin"] == 0.2

    # Despesa de juros vem de "Interest Expense", ignorando "Net Interest Income"
    assert row["interest_expense"] == 10.0
    assert row["interest_expense_source"] == "Interest Expense"
    assert row["interest_coverage"] == 20.0
    assert row["debt_status"] == "com_divida"

    assert row["net_debt"] == 250.0
    assert row["ebitda"] == 250.0
    assert row["net_debt_to_ebitda"] == 1.0
    assert row["current_ratio"] == 2.0
    assert row["gross_margin"] == 0.5
    assert row["effective_tax_rate"] == pytest.approx(0.2)
    assert row["effective_tax_rate_clamped"] == pytest.approx(0.2)
    assert row["dividend_yield"] == pytest.approx(0.015)
    assert row["revenue_fy0"] == 1000.0 and row["revenue_fy3"] == 700.0
    assert row["eps_diluted_fy1"] == 1.36
    assert row["market_cap"] == 5_000_000_000
    assert row["price"] == 50.0
    assert row["price_history_days"] == 600
    assert not math.isnan(row["avg_dollar_volume_60d"])
    assert not math.isnan(row["momentum_12_1"])
    assert row["data_warnings"] == ""


def test_net_interest_income_is_never_used_as_interest_expense(base_config):
    income = standard_income()
    income.pop("Interest Expense")  # só sobra "Net Interest Income" (armadilha)
    data = make_ticker_data(income=income)
    row = build_metrics_row(data, base_config)
    assert math.isnan(row["interest_expense"])
    assert row["interest_expense_source"] is None
    assert math.isnan(row["interest_coverage"])
    assert row["debt_status"] == "com_divida"  # há dívida (400) mas sem juros reportados


def test_interest_expense_falls_back_to_interest_paid_in_cashflow(base_config):
    income = standard_income()
    income.pop("Interest Expense")
    cashflow = standard_cashflow()
    cashflow["Interest Paid Supplemental Data"] = [8.0, 8.0, 8.0, 8.0]
    row = build_metrics_row(make_ticker_data(income=income, cashflow=cashflow), base_config)
    assert row["interest_expense"] == 8.0
    assert row["interest_expense_source"] == "Interest Paid Supplemental Data"
    assert row["interest_coverage"] == 25.0


def test_missing_capex_gives_nan_fcf_not_ocf(base_config):
    cashflow = standard_cashflow()
    cashflow.pop("Capital Expenditure")
    row = build_metrics_row(make_ticker_data(cashflow=cashflow), base_config)
    assert math.isnan(row["free_cash_flow"])
    assert (row["fcf_positive_years"], row["fcf_years_observed"]) == (0, 0)
    assert row["operating_cash_flow"] == 220.0  # o OCF continua disponível, mas não substitui o FCF


def test_operating_revenue_is_not_ebit(base_config):
    income = standard_income()
    income.pop("Operating Income")
    income.pop("EBIT")
    row = build_metrics_row(make_ticker_data(income=income), base_config)
    assert math.isnan(row["ebit"])
    assert row["ebit_source"] is None
    assert math.isnan(row["operating_margin"])
    assert math.isnan(row["interest_coverage"])


def test_ebit_falls_back_to_reported_ebit_and_ebitda_is_derived(base_config):
    income = standard_income()
    income.pop("Operating Income")
    income.pop("EBITDA")
    row = build_metrics_row(make_ticker_data(income=income), base_config)
    assert row["ebit"] == 205.0
    assert row["ebit_source"] == "EBIT"
    assert row["ebitda"] == 255.0  # 205 + D&A 50
    assert row["ebitda_source"].startswith("derivado")


def test_debt_free_company(base_config):
    balance = standard_balance()
    balance["Total Debt"] = [0.0, 0.0, 0.0, 0.0]
    income = standard_income()
    income["Interest Expense"] = [0.0, 0.0, 0.0, 0.0]
    row = build_metrics_row(make_ticker_data(income=income, balance=balance), base_config)
    assert row["debt_status"] == "sem_divida"
    assert math.isnan(row["interest_coverage"])  # sem juros: NaN, não 25x
    assert row["net_debt"] == -150.0


def test_latest_year_missing_interest_is_not_backfilled(base_config):
    income = standard_income()
    income["Interest Expense"] = [float("nan"), float("nan"), 10.0, 10.0]  # caso Apple
    row = build_metrics_row(make_ticker_data(income=income), base_config)
    assert math.isnan(row["interest_expense"])
    assert row["debt_status"] == "com_divida"


def test_years_of_statements_is_minimum_across_statements(base_config):
    cashflow = standard_cashflow()
    cashflow["Operating Cash Flow"] = [220.0, 200.0, 180.0, float("nan")]
    row = build_metrics_row(make_ticker_data(cashflow=cashflow), base_config)
    assert row["years_income"] == 4
    assert row["years_cashflow"] == 3
    assert row["years_of_statements"] == 3


def test_empty_statements_and_prices_do_not_crash(base_config):
    data = make_ticker_data()
    data.income = pd.DataFrame()
    data.balance = pd.DataFrame()
    data.cashflow = pd.DataFrame()
    data.prices = pd.DataFrame()
    data.warnings = ["income_stmt vazio"]
    row = build_metrics_row(data, base_config)
    assert row["years_of_statements"] == 0
    assert row["latest_fiscal_year_end"] is None
    assert math.isnan(row["free_cash_flow"])
    assert math.isnan(row["avg_dollar_volume_60d"])
    assert row["price"] == 50.0  # vem do info
    assert row["data_warnings"] == "income_stmt vazio"


def test_estimated_revenue_growth_from_estimates(base_config):
    estimates = {
        "revenue_estimate": pd.DataFrame(
            {"avg": [1.0, 1.0, 1.0, 1.0], "growth": [0.05, 0.06, 0.08, 0.10]},
            index=["0q", "+1q", "0y", "+1y"],
        )
    }
    row = build_metrics_row(make_ticker_data(estimates=estimates), base_config)
    assert row["revenue_growth_est_next_fy"] == 0.10
    assert row["revenue_growth_est_current_fy"] == 0.08


def test_bank_classification_from_info(base_config):
    info = standard_info()
    info["sector"] = "Financial Services"
    info["industry"] = "Banks—Diversified"
    row = build_metrics_row(make_ticker_data(info=info), base_config)
    assert row["business_type"] == "bank_insurance"


def test_normalize_dividend_yield():
    assert normalize_dividend_yield(0.32) == pytest.approx(0.0032)
    assert normalize_dividend_yield(6.1) == pytest.approx(0.061)
    assert math.isnan(normalize_dividend_yield(None))


def test_build_metrics_table_orders_columns_and_reports_failures(base_config):
    good = make_ticker_data(ticker="BBB")
    other = make_ticker_data(ticker="AAA")
    broken = make_ticker_data(ticker="ZZZ")
    broken.prices = "isto não é um DataFrame"  # força uma exceção no cálculo
    table, failures = build_metrics_table([good, other, broken], base_config)
    assert list(table["ticker"]) == ["AAA", "BBB"]
    assert list(table.columns[:5]) == ["ticker", "company", "sector", "industry", "business_type"]
    assert len(failures) == 1 and failures[0][0] == "ZZZ"
    assert "erro ao calcular métricas" in failures[0][1]


def test_build_metrics_table_empty():
    table, failures = build_metrics_table([], {})
    assert table.empty and failures == []
    assert "ticker" in table.columns

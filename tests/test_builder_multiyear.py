"""Métricas multi-ano e scores calculados pelo builder sobre a empresa sintética."""
from __future__ import annotations

import math

import pytest

from src.metrics.builder import build_metrics_row
from tests.conftest import make_ticker_data, standard_balance, standard_cashflow, standard_income, standard_info


def test_multiyear_metrics_on_standard_company(standard_ticker_data, base_config):
    row = build_metrics_row(standard_ticker_data, base_config)

    # ROIC por ano: NOPAT = EBIT × (1 − 20%); IC = equity + dívida − caixa
    expected_roic = [(200 * 0.8) / 1150, (180 * 0.8) / 1070, (160 * 0.8) / 990]
    for i, value in enumerate(expected_roic):
        assert row[f"roic_fy{i}"] == pytest.approx(value)
    assert row["roic_avg_3y"] == pytest.approx(sum(expected_roic) / 3)
    assert row["roic_years_observed"] == 3

    assert row["roe_fy0"] == pytest.approx(152 / 900)
    assert row["roe_avg_3y"] == pytest.approx((152 / 900 + 136 / 800 + 120 / 700) / 3)
    assert row["roe_positive_years"] == 3

    assert row["cash_conversion"] == pytest.approx((160 + 145 + 130) / (152 + 136 + 120))
    assert row["cash_conversion_years"] == 3
    assert row["gross_margin_std_pp"] == pytest.approx(0.0)
    assert row["share_dilution_cagr"] == pytest.approx(0.0) and row["share_dilution_span_years"] == 3
    assert row["revenue_cagr_3y"] == pytest.approx((1000 / 700) ** (1 / 3) - 1)
    assert row["revenue_cagr_span_years"] == 3
    assert row["eps_consecutive_declines"] == 0.0

    # Piotroski: ROA>0, OCF>0, ROA sobe, OCF>NI, alavancagem desce, CR igual (não), sem diluição, GM igual (não), ATO igual (não)
    assert (row["piotroski_f"], row["piotroski_criteria"]) == (6.0, 9)
    assert row["altman_z"] > 0  # WC = 600 − 300; passivo total = 1100
    assert math.isnan(row["beneish_m"])  # sem clientes, PPE e SG&A no sintético: não calculável

    assert row["fcf_yield"] == pytest.approx(160 / 5_250_000_000)
    assert row["ev_to_ebit"] == pytest.approx(5_250_000_000 / 200)
    assert row["equity_to_assets"] == pytest.approx(900 / 2000)
    assert row["ffo"] == pytest.approx(152 + 50)
    assert row["p_ffo"] == pytest.approx(5_000_000_000 / 202)
    assert row["shares_fy0"] == 100.0 and row["equity_fy3"] == 600.0
    assert row["total_debt_source"] == "Total Debt"
    assert row["capex_source"] == "Capital Expenditure"
    assert row["classification_note"] == ""


def test_debt_free_company_without_debt_rows_is_assumed_zero(base_config):
    balance = standard_balance()
    balance.pop("Total Debt")
    income = standard_income()
    income.pop("Interest Expense")
    row = build_metrics_row(make_ticker_data(income=income, balance=balance), base_config)
    assert row["total_debt"] == 0.0
    assert row["total_debt_source"].startswith("assumido 0")
    assert row["debt_status"] == "sem_divida"
    assert row["net_debt"] == -150.0
    assert not math.isnan(row["roic_avg_3y"])  # a série de dívida a zero permite o ROIC


def test_reit_uses_purchase_of_investment_properties_as_capex(base_config):
    info = standard_info()
    info["sector"], info["industry"] = "Real Estate", "REIT—Office"
    cashflow = standard_cashflow()
    cashflow.pop("Capital Expenditure")
    cashflow["Purchase Of Investment Properties"] = [-80.0, -70.0, -60.0, -50.0]
    row = build_metrics_row(make_ticker_data(info=info, cashflow=cashflow), base_config)
    assert row["business_type"] == "reit"
    assert row["capex_source"] == "Purchase Of Investment Properties"
    assert row["free_cash_flow"] == 140.0
    standard_row = build_metrics_row(make_ticker_data(cashflow=cashflow), base_config)
    assert math.isnan(standard_row["free_cash_flow"])  # empresas normais não usam essa rubrica


def test_lender_heuristic_reclassifies_credit_services_without_ebit(base_config):
    info = standard_info()
    info["sector"], info["industry"] = "Financial Services", "Credit Services"
    income = standard_income()
    income.pop("Operating Income")
    income.pop("EBIT")
    income["Net Interest Income"] = [400.0, 380.0, 360.0, 340.0]  # 40% da receita
    row = build_metrics_row(make_ticker_data(info=info, income=income), base_config)
    assert row["business_type"] == "bank_insurance"
    assert row["classification_note"].startswith("reclassificado como bank_insurance")
    visa_like = build_metrics_row(make_ticker_data(info=info), base_config)  # com EBIT fica standard
    assert visa_like["business_type"] == "standard"

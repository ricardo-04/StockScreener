"""Construção da tabela de métricas (uma linha por empresa) a partir dos dados brutos.

Sem I/O: recebe ``TickerData`` já descarregados e devolve dicionários/DataFrame.
Inclui identificação, dados de mercado, último ano fiscal, séries de 4 anos,
rácios base e as métricas multi-ano usadas pelo funil (ROIC/ROE médios,
conversão de caixa, CAGR, diluição, Piotroski, Altman, Beneish, múltiplos).

Regras de dados (correções face ao código antigo):
- FCF = Operating Cash Flow − |CapEx|. CapEx = "Capital Expenditure" ou, em cada
  ano em que esta falte, "Purchase Of PPE" (a linha de compra de ativos fixos do
  cash flow). REITs sem ambas usam "Purchase Of Investment Properties". Sem
  nenhuma, o FCF fica NaN;
- despesa de juros: "Interest Expense" ou "Interest Expense Non Operating"
  (resultados), depois "Interest Paid Supplemental Data" (fluxos de caixa) e, se
  só o último ano faltar, a do ano anterior com a fonte assinalada.
  "Net Interest Income" NUNCA é usado como juros (só na heurística de lenders);
- EBIT: "Operating Income" (preferido) ou "EBIT". "Operating Revenue" nunca é usado;
- ativo total em falta é derivado de passivo + capital próprio (identidade contabilística);
- sem qualquer rubrica de dívida no balanço e sem juros nos resultados, a dívida
  é assumida 0 (o Yahoo omite rubricas a zero), com a fonte registada;
- cobertura de juros sem despesa de juros fica NaN; "sem dívida" é um estado
  próprio (``debt_status``), não um número arbitrário;
- ``dividendYield`` do yfinance vem em percentagem e é convertido para fração.
Todas as decisões de fallback ficam nas colunas ``*_source`` e ``data_notes``.
"""
from __future__ import annotations

import logging
import math
from typing import Any

import pandas as pd

from src.data.fetcher import TickerData
from src.metrics import statements as st
from src.metrics.cashflow import count_positive, free_cash_flow_series
from src.metrics.classification import REIT, apply_lender_heuristic, classify_business_type
from src.metrics.growth import consecutive_declines, series_cagr
from src.metrics.leverage import current_ratio, debt_status, interest_coverage, net_debt, net_debt_to_ebitda
from src.metrics.prices import average_dollar_volume, last_close, momentum_12_1, price_vs_sma, simple_moving_average
from src.metrics.profitability import clamp_tax_rate, effective_tax_rate, margin
from src.metrics.quality import (
    average_of_last, cash_conversion, count_positive_of_last, gross_margin_std_pp, roe_series, roic_series,
    share_count_cagr,
)
from src.metrics.scores import altman_z_score, beneish_m_score, piotroski_f_score
from src.metrics.statements import NAN, to_float
from src.metrics.valuation import ev_to_ebit, fcf_yield, funds_from_operations, price_to_ffo

log = logging.getLogger(__name__)

# Rubricas do yfinance por métrica (ordem = preferência). Nunca "Net Interest Income"
# para juros nem "Operating Revenue" para EBIT.
INCOME_ROWS: dict[str, tuple[str, ...]] = {
    "revenue": ("Total Revenue",),
    "gross_profit": ("Gross Profit",),
    "cost_of_revenue": ("Cost Of Revenue", "Reconciled Cost Of Revenue"),
    "operating_income": ("Operating Income", "Total Operating Income As Reported"),
    "ebit_reported": ("EBIT",),
    "ebitda_reported": ("EBITDA",),
    "interest_expense": ("Interest Expense", "Interest Expense Non Operating"),
    "net_interest_income": ("Net Interest Income",),  # só para a heurística de lenders
    "pretax_income": ("Pretax Income",),
    "tax_provision": ("Tax Provision",),
    "tax_rate_reported": ("Tax Rate For Calcs",),
    "net_income": ("Net Income", "Net Income Common Stockholders", "Net Income Continuous Operations"),
    "eps_diluted": ("Diluted EPS",),
    "shares_diluted": ("Diluted Average Shares", "Basic Average Shares"),
    "depreciation_amortization": ("Reconciled Depreciation",),
    "sga_expense": ("Selling General And Administration",),
}
BALANCE_ROWS: dict[str, tuple[str, ...]] = {
    "total_assets": ("Total Assets",),
    "current_assets": ("Current Assets",),
    "current_liabilities": ("Current Liabilities",),
    "total_liabilities": ("Total Liabilities Net Minority Interest",),
    "total_equity_gross": ("Total Equity Gross Minority Interest",),
    "total_debt_reported": ("Total Debt",),
    "long_term_debt": ("Long Term Debt And Capital Lease Obligation", "Long Term Debt"),
    "current_debt": ("Current Debt And Capital Lease Obligation", "Current Debt"),
    "cash": ("Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"),
    "equity": ("Stockholders Equity", "Common Stock Equity", "Total Equity Gross Minority Interest"),
    "retained_earnings": ("Retained Earnings",),
    "working_capital": ("Working Capital",),
    "receivables": ("Accounts Receivable", "Receivables"),
    "inventory": ("Inventory",),
    "net_ppe": ("Net PPE",),
    "shares_outstanding_bs": ("Ordinary Shares Number", "Share Issued"),
}
CASHFLOW_ROWS: dict[str, tuple[str, ...]] = {
    "operating_cash_flow": ("Operating Cash Flow", "Cash Flow From Continuing Operating Activities"),
    "capex": ("Capital Expenditure", "Purchase Of PPE"),
    "interest_paid": ("Interest Paid Supplemental Data",),
    "depreciation_amortization_cf": ("Depreciation And Amortization", "Depreciation Amortization Depletion"),
    "dividends_paid": ("Cash Dividends Paid", "Common Stock Dividend Paid"),
    "buybacks": ("Repurchase Of Capital Stock",),
}
REIT_CAPEX_ROWS: tuple[str, ...] = ("Capital Expenditure", "Purchase Of PPE", "Purchase Of Investment Properties")

# Métricas cujas séries anuais são expandidas em colunas *_fy0.._fyN
YEAR_SERIES = ("revenue", "operating_income", "net_income", "free_cash_flow", "eps_diluted", "shares", "equity")

# Rubricas âncora para contar anos disponíveis por demonstração
INCOME_ANCHORS = ("Total Revenue", "Net Income")
BALANCE_ANCHORS = ("Total Assets", "Total Liabilities Net Minority Interest")
CASHFLOW_ANCHORS = ("Operating Cash Flow", "Cash Flow From Continuing Operating Activities")

# Ordem das colunas principais na tabela final (as restantes seguem por ordem de criação)
LEADING_COLUMNS = (
    "ticker", "company", "sector", "industry", "business_type", "country", "exchange",
    "price", "market_cap", "enterprise_value", "avg_dollar_volume_60d", "years_of_statements",
    "latest_fiscal_year_end", "revenue", "ebit", "ebitda", "net_income", "operating_cash_flow",
    "capex", "free_cash_flow", "fcf_positive_years", "fcf_years_observed", "gross_margin",
    "operating_margin", "net_margin", "roic_avg_3y", "roe_avg_3y", "cash_conversion",
    "gross_margin_std_pp", "share_dilution_cagr", "revenue_cagr_3y", "eps_consecutive_declines",
    "revenue_growth_est_next_fy", "total_debt", "cash", "net_debt", "net_debt_to_ebitda",
    "debt_status", "interest_expense", "interest_coverage", "current_ratio", "altman_z",
    "piotroski_f", "piotroski_criteria", "beneish_m", "fcf_yield", "ev_to_ebit", "forward_pe",
    "trailing_pe", "peg_ratio", "price_to_book", "dividend_yield", "p_ffo", "momentum_12_1",
    "sma_200", "price_vs_sma200", "data_warnings", "data_notes", "classification_note",
)


# ---------------------------------------------------------------------------
# Auxiliares
# ---------------------------------------------------------------------------
def _info_number(info: dict[str, Any], keys: tuple[str, ...]) -> float:
    for key in keys:
        value = to_float(info.get(key))
        if not math.isnan(value):
            return value
    return NAN


def _info_text(info: dict[str, Any], keys: tuple[str, ...], default: str = "") -> str:
    for key in keys:
        value = info.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def normalize_dividend_yield(value: float) -> float:
    """yfinance (>= 0.2.5x) devolve ``dividendYield`` em percentagem (0.32 = 0.32%)."""
    raw = to_float(value)
    return NAN if math.isnan(raw) else raw / 100.0


def _estimated_revenue_growth(estimates: dict[str, pd.DataFrame]) -> tuple[float, float]:
    """(crescimento esperado próximo ano fiscal, ano fiscal corrente) da tabela revenue_estimate."""
    frame = estimates.get("revenue_estimate")
    if frame is None or frame.empty or "growth" not in frame.columns:
        return NAN, NAN

    def pick(period: str) -> float:
        return to_float(frame.loc[period, "growth"]) if period in frame.index else NAN

    return pick("+1y"), pick("0y")


def _first_available(*values: float) -> float:
    for value in values:
        if not math.isnan(value):
            return value
    return NAN


def _sum_available(*values: float) -> float:
    present = [v for v in values if not math.isnan(v)]
    return sum(present) if present else NAN


def fiscal_year_index(series: pd.Series | None) -> pd.Series | None:
    """Reindexa uma série por ano fiscal (inteiro), do mais recente ao mais antigo.

    Permite alinhar demonstrações cujas datas de fecho diferem por alguns dias.
    """
    if series is None or len(series) == 0:
        return series
    pairs: list[tuple[int, float]] = []
    seen: set[int] = set()
    for label, value in series.items():
        year = getattr(label, "year", None)
        if year is None:
            try:
                year = pd.Timestamp(label).year
            except (TypeError, ValueError):
                continue
        if year in seen:
            continue
        seen.add(year)
        pairs.append((int(year), to_float(value)))
    if not pairs:
        return pd.Series(dtype=float)
    pairs.sort(key=lambda item: item[0], reverse=True)
    return pd.Series([v for _, v in pairs], index=[y for y, _ in pairs], dtype=float)


def _has_values(series: pd.Series | None) -> bool:
    return series is not None and len(series) > 0 and bool(series.notna().any())


def _sum_series(first: pd.Series | None, second: pd.Series | None, min_count: int = 1) -> pd.Series | None:
    """Soma duas séries alinhadas por ano (``min_count`` parcelas necessárias por ano)."""
    if not _has_values(first) and not _has_values(second):
        return None
    frame = st.align_series(a=first, b=second)
    return frame.sum(axis=1, min_count=min_count)


def _fill_missing(primary: pd.Series | None, fallback: pd.Series | None) -> pd.Series | None:
    """Preenche os NaN da série principal com a série alternativa (alinhadas por ano)."""
    if not _has_values(fallback):
        return primary
    if not _has_values(primary):
        return fallback
    return primary.combine_first(fallback).sort_index(ascending=False)


# ---------------------------------------------------------------------------
# Linha de métricas de uma empresa
# ---------------------------------------------------------------------------
def build_metrics_row(data: TickerData, cfg: dict[str, Any]) -> dict[str, Any]:
    metrics_cfg = cfg.get("metrics", {})
    years = int(cfg.get("data", {}).get("statement_years", 4))
    info = data.info or {}
    row: dict[str, Any] = {}
    notes: list[str] = []

    # --- identificação -----------------------------------------------------
    sector = _info_text(info, ("sector",))
    industry = _info_text(info, ("industry",))
    row["ticker"] = data.ticker
    row["company"] = _info_text(info, ("longName", "shortName"), data.ticker)
    row["sector"] = sector
    row["industry"] = industry
    row["business_type"] = classify_business_type(sector, industry, cfg.get("business_types"))
    row["country"] = _info_text(info, ("country",))
    row["exchange"] = _info_text(info, ("exchange",))
    row["currency"] = _info_text(info, ("currency",))
    row["financial_currency"] = _info_text(info, ("financialCurrency",))

    # --- mercado -----------------------------------------------------------
    price = _info_number(info, ("currentPrice", "regularMarketPrice", "previousClose"))
    if math.isnan(price):
        price = last_close(data.prices)
    row["price"] = price
    row["market_cap"] = _info_number(info, ("marketCap",))
    row["enterprise_value"] = _info_number(info, ("enterpriseValue",))
    row["shares_outstanding"] = _info_number(info, ("sharesOutstanding",))
    row["forward_pe"] = _info_number(info, ("forwardPE",))
    row["trailing_pe"] = _info_number(info, ("trailingPE",))
    row["peg_ratio"] = _info_number(info, ("pegRatio", "trailingPegRatio"))
    row["price_to_book"] = _info_number(info, ("priceToBook",))
    row["dividend_yield"] = normalize_dividend_yield(_info_number(info, ("dividendYield",)))
    row["revenue_growth_ttm"] = _info_number(info, ("revenueGrowth",))
    row["earnings_growth_ttm"] = _info_number(info, ("earningsGrowth",))
    row["revenue_growth_est_next_fy"], row["revenue_growth_est_current_fy"] = _estimated_revenue_growth(data.estimates)

    # --- preços ------------------------------------------------------------
    closes = data.prices["Close"] if (isinstance(data.prices, pd.DataFrame) and "Close" in data.prices.columns) else None
    row["price_history_days"] = 0 if closes is None else int(pd.to_numeric(closes, errors="coerce").notna().sum())
    row["avg_dollar_volume_60d"] = average_dollar_volume(data.prices, int(metrics_cfg.get("dollar_volume_window_days", 60)))
    row["sma_200"] = simple_moving_average(closes, int(metrics_cfg.get("sma_window_days", 200)))
    row["price_vs_sma200"] = price_vs_sma(price, row["sma_200"])
    row["momentum_12_1"] = momentum_12_1(
        closes,
        int(metrics_cfg.get("momentum_lookback_days", 252)),
        int(metrics_cfg.get("momentum_skip_days", 21)),
    )

    # --- demonstrações: anos disponíveis ----------------------------------
    row["years_income"] = st.count_periods_with_value(data.income, INCOME_ANCHORS)
    row["years_balance"] = st.count_periods_with_value(data.balance, BALANCE_ANCHORS)
    row["years_cashflow"] = st.count_periods_with_value(data.cashflow, CASHFLOW_ANCHORS)
    row["years_of_statements"] = min(row["years_income"], row["years_balance"], row["years_cashflow"])
    periods = st.statement_periods(data.income)
    row["latest_fiscal_year_end"] = periods[0].date().isoformat() if periods and hasattr(periods[0], "date") else None

    # --- demonstrações: último ano fiscal ---------------------------------
    for key, names in INCOME_ROWS.items():
        row[key] = st.latest_value(data.income, names)
    for key, names in BALANCE_ROWS.items():
        row[key] = st.latest_value(data.balance, names)
    for key, names in CASHFLOW_ROWS.items():
        row[key] = st.latest_value(data.cashflow, names)

    # Ativo total: identidade contabilística quando o Yahoo omite a rubrica no último ano
    row["total_assets_source"] = "Total Assets" if not math.isnan(row["total_assets"]) else None
    if math.isnan(row["total_assets"]) and not math.isnan(row["total_liabilities"]) and not math.isnan(row["total_equity_gross"]):
        row["total_assets"] = row["total_liabilities"] + row["total_equity_gross"]
        row["total_assets_source"] = "derivado (passivo + capital próprio)"
        notes.append("ativo total do último ano derivado de passivo + capital próprio")

    # EBIT: Operating Income preferido; "EBIT" do yfinance inclui resultados não operacionais
    ebit, ebit_source = st.latest_value_with_source(data.income, INCOME_ROWS["operating_income"])
    if math.isnan(ebit):
        ebit, ebit_source = st.latest_value_with_source(data.income, INCOME_ROWS["ebit_reported"])
    row["ebit"] = ebit
    row["ebit_source"] = ebit_source

    # EBITDA: reportado ou derivado de EBIT + D&A (nunca inventado)
    d_and_a = _first_available(row["depreciation_amortization"], row["depreciation_amortization_cf"])
    ebitda = row["ebitda_reported"]
    ebitda_source = "EBITDA" if not math.isnan(ebitda) else None
    if math.isnan(ebitda) and not math.isnan(ebit) and not math.isnan(d_and_a):
        ebitda, ebitda_source = ebit + abs(d_and_a), "derivado (EBIT + D&A)"
    row["ebitda"] = ebitda
    row["ebitda_source"] = ebitda_source

    # Despesa de juros: resultados -> juros pagos (cash flow) -> ano anterior (assinalado).
    # Nunca "Net Interest Income".
    income_interest = fiscal_year_index(st.get_row_combined(data.income, INCOME_ROWS["interest_expense"]))
    income_interest_latest = st.value_at(income_interest, 0)
    income_interest_previous = st.value_at(income_interest, 1)
    interest, interest_source = st.latest_value_with_source(data.income, INCOME_ROWS["interest_expense"])
    if math.isnan(interest):
        interest, interest_source = st.latest_value_with_source(data.cashflow, CASHFLOW_ROWS["interest_paid"])
    if math.isnan(interest) and not math.isnan(income_interest_previous):
        interest = income_interest_previous
        interest_source = f"Interest Expense ({income_interest.index[1]}, último ano em falta)"
        notes.append("despesa de juros do último ano em falta: usada a do ano anterior")
    row["interest_expense"] = abs(interest) if not math.isnan(interest) else NAN
    row["interest_expense_source"] = interest_source

    # Dívida total: reportada, soma das componentes ou 0 quando não há qualquer rubrica de dívida
    # no balanço nem juros nos resultados (o Yahoo omite rubricas a zero)
    total_debt = row["total_debt_reported"]
    total_debt_source = "Total Debt" if not math.isnan(total_debt) else None
    if math.isnan(total_debt):
        total_debt = _sum_available(row["long_term_debt"], row["current_debt"])
        total_debt_source = "soma das componentes" if not math.isnan(total_debt) else None
    no_debt_rows = (
        math.isnan(total_debt) and math.isnan(income_interest_latest) and math.isnan(income_interest_previous)
        and row["years_balance"] > 0
    )
    if no_debt_rows:
        total_debt, total_debt_source = 0.0, "assumido 0 (sem rubricas de dívida no balanço nem juros nos resultados)"
        notes.append("dívida assumida 0: sem rubricas de dívida nem despesa de juros")
    row["total_debt"] = total_debt
    row["total_debt_source"] = total_debt_source

    # Heurística de lenders ("Credit Services" com perfil de banco)
    row["business_type"], row["classification_note"] = apply_lender_heuristic(
        row["business_type"], industry, ebit, row["net_interest_income"], row["revenue"], cfg.get("business_types")
    )
    is_reit = row["business_type"] == REIT

    # CapEx: "Capital Expenditure" -> "Purchase Of PPE" -> (REIT) "Purchase Of Investment Properties"
    capex_names = REIT_CAPEX_ROWS if is_reit else CASHFLOW_ROWS["capex"]
    row["capex"], row["capex_source"] = st.latest_value_with_source(data.cashflow, capex_names)

    # --- séries por ano fiscal (candidatas combinadas período a período) ---
    series: dict[str, pd.Series | None] = {}
    for key, names in INCOME_ROWS.items():
        series[key] = fiscal_year_index(st.get_row_combined(data.income, names))
    for key, names in BALANCE_ROWS.items():
        series[key] = fiscal_year_index(st.get_row_combined(data.balance, names))
    for key, names in CASHFLOW_ROWS.items():
        series[key] = fiscal_year_index(st.get_row_combined(data.cashflow, capex_names if key == "capex" else names))

    series["total_assets"] = _fill_missing(
        series["total_assets"], _sum_series(series["total_liabilities"], series["total_equity_gross"], min_count=2)
    )
    series["ebit"] = series["operating_income"] if _has_values(series["operating_income"]) else series["ebit_reported"]
    series["shares"] = series["shares_diluted"] if _has_values(series["shares_diluted"]) else series["shares_outstanding_bs"]
    if _has_values(series["total_debt_reported"]):
        series["total_debt"] = series["total_debt_reported"]
    else:
        series["total_debt"] = _sum_series(series["long_term_debt"], series["current_debt"])
    if no_debt_rows and _has_values(series["equity"]):
        zeros = pd.Series(0.0, index=series["equity"].index)
        series["total_debt"] = _fill_missing(series["total_debt"], zeros)
    series["free_cash_flow"] = free_cash_flow_series(series["operating_cash_flow"], series["capex"])
    year_frame = st.align_series(**{k: v for k, v in series.items() if _has_values(v)})

    row["free_cash_flow"] = st.value_at(series["free_cash_flow"], 0)
    row["fcf_positive_years"], row["fcf_years_observed"] = count_positive(
        series["free_cash_flow"], int(metrics_cfg.get("fcf_history_years", 4))
    )

    # --- rácios do último ano ---------------------------------------------
    row["gross_margin"] = margin(row["gross_profit"], row["revenue"])
    row["operating_margin"] = margin(ebit, row["revenue"])
    row["net_margin"] = margin(row["net_income"], row["revenue"])
    tax_rate_raw = effective_tax_rate(row["tax_provision"], row["pretax_income"])
    if math.isnan(tax_rate_raw):
        tax_rate_raw = row["tax_rate_reported"]
    row["effective_tax_rate"] = tax_rate_raw
    tax_floor = float(metrics_cfg.get("tax_rate_floor", 0.10))
    tax_cap = float(metrics_cfg.get("tax_rate_cap", 0.30))
    tax_default = float(metrics_cfg.get("tax_rate_default", 0.21))
    row["effective_tax_rate_clamped"] = clamp_tax_rate(tax_rate_raw, tax_floor, tax_cap)
    row["net_debt"] = net_debt(total_debt, row["cash"])
    row["net_debt_to_ebitda"] = net_debt_to_ebitda(row["net_debt"], ebitda)
    row["debt_status"] = debt_status(total_debt, row["interest_expense"])
    row["interest_coverage"] = interest_coverage(ebit, row["interest_expense"])
    ratio = current_ratio(row["current_assets"], row["current_liabilities"])
    if math.isnan(ratio):
        ratio = _info_number(info, ("currentRatio",))
    row["current_ratio"] = ratio
    row["equity_to_assets"] = (
        row["equity"] / row["total_assets"]
        if not math.isnan(row["equity"]) and not math.isnan(row["total_assets"]) and row["total_assets"] > 0
        else NAN
    )

    # --- métricas multi-ano -----------------------------------------------
    roic_values = roic_series(year_frame, tax_floor, tax_cap, tax_default)
    roic_years = int(metrics_cfg.get("roic_average_years", 3))
    for i in range(roic_years):
        row[f"roic_fy{i}"] = st.value_at(roic_values, i)
    row["roic_avg_3y"], row["roic_years_observed"] = average_of_last(roic_values, roic_years)

    roe_values = roe_series(year_frame)
    roe_years = int(metrics_cfg.get("roe_average_years", 3))
    for i in range(roe_years):
        row[f"roe_fy{i}"] = st.value_at(roe_values, i)
    row["roe_avg_3y"], row["roe_years_observed"] = average_of_last(roe_values, roe_years)
    row["roe_positive_years"], _ = count_positive_of_last(roe_values, roe_years)

    row["cash_conversion"], row["cash_conversion_years"] = cash_conversion(
        series["free_cash_flow"], series["net_income"], int(metrics_cfg.get("cash_conversion_years", 3))
    )
    row["gross_margin_std_pp"] = gross_margin_std_pp(
        series["gross_profit"], series["revenue"], int(metrics_cfg.get("gross_margin_years", 4))
    )
    row["share_dilution_cagr"], row["share_dilution_span_years"] = share_count_cagr(
        series["shares"], int(metrics_cfg.get("dilution_years", 3))
    )
    row["revenue_cagr_3y"], row["revenue_cagr_span_years"] = series_cagr(
        series["revenue"], int(metrics_cfg.get("revenue_cagr_years", 3))
    )
    row["eps_consecutive_declines"] = consecutive_declines(series["eps_diluted"])

    # --- scores clássicos -------------------------------------------------
    row["piotroski_f"], row["piotroski_criteria"] = piotroski_f_score(year_frame)
    working_capital = row["working_capital"]
    if math.isnan(working_capital) and not math.isnan(row["current_assets"]) and not math.isnan(row["current_liabilities"]):
        working_capital = row["current_assets"] - row["current_liabilities"]
    row["altman_z"] = altman_z_score(
        working_capital, row["retained_earnings"], ebit, row["total_assets"], row["market_cap"],
        row["total_liabilities"], row["revenue"],
    )
    row["beneish_m"] = beneish_m_score(year_frame)

    # --- valuation --------------------------------------------------------
    row["fcf_yield"] = fcf_yield(row["free_cash_flow"], row["enterprise_value"])
    row["ev_to_ebit"] = ev_to_ebit(row["enterprise_value"], ebit)
    row["ffo"] = funds_from_operations(row["net_income"], d_and_a)
    row["p_ffo"] = price_to_ffo(row["market_cap"], row["ffo"])

    # --- colunas por ano --------------------------------------------------
    for key in YEAR_SERIES:
        row.update(st.series_to_year_columns(series.get(key), key, years))

    # --- proveniência -----------------------------------------------------
    row["data_warnings"] = "; ".join(data.warnings)
    row["data_notes"] = "; ".join(notes)
    row["from_cache"] = bool(data.from_cache)
    row["fetched_at"] = data.fetched_at.isoformat(timespec="seconds")

    for key in ("ebit_reported", "ebitda_reported", "total_debt_reported", "total_equity_gross"):
        row.pop(key, None)
    return row


def build_metrics_table(datas: list[TickerData], cfg: dict[str, Any]) -> tuple[pd.DataFrame, list[tuple[str, str]]]:
    """Tabela de métricas e lista de (ticker, motivo) para os que falharam o cálculo."""
    rows: list[dict[str, Any]] = []
    failures: list[tuple[str, str]] = []
    for data in datas:
        try:
            rows.append(build_metrics_row(data, cfg))
        except Exception as exc:  # noqa: BLE001 - um ticker estranho não pode parar o pipeline
            reason = f"erro ao calcular métricas: {type(exc).__name__}: {exc}"
            log.exception("%s: %s", data.ticker, reason)
            failures.append((data.ticker, reason))

    if not rows:
        return pd.DataFrame(columns=list(LEADING_COLUMNS)), failures

    table = pd.DataFrame(rows)
    leading = [col for col in LEADING_COLUMNS if col in table.columns]
    remaining = [col for col in table.columns if col not in leading]
    table = table[leading + remaining].sort_values("ticker").reset_index(drop=True)
    return table, failures

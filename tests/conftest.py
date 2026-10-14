"""Fixtures partilhadas: demonstrações sintéticas e TickerData de exemplo."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.config import load_config
from src.data.fetcher import TickerData

FISCAL_YEARS = [
    pd.Timestamp("2025-12-31"),
    pd.Timestamp("2024-12-31"),
    pd.Timestamp("2023-12-31"),
    pd.Timestamp("2022-12-31"),
]


def make_statement(rows: dict[str, list[float]], periods: list[pd.Timestamp] | None = None) -> pd.DataFrame:
    """DataFrame no formato do yfinance: rubricas nas linhas, anos fiscais (desc) nas colunas."""
    periods = periods or FISCAL_YEARS
    return pd.DataFrame.from_dict(rows, orient="index", columns=periods).astype(float)


def make_prices(days: int = 600, start_price: float = 100.0, daily_return: float = 0.0005,
                volume: int = 1_000_000) -> pd.DataFrame:
    index = pd.bdate_range(end="2026-10-14", periods=days)
    closes = start_price * (1.0 + daily_return) ** np.arange(days)
    return pd.DataFrame(
        {"Open": closes, "High": closes, "Low": closes, "Close": closes, "Volume": float(volume)},
        index=index,
    )


def standard_income() -> dict[str, list[float]]:
    return {
        "Total Revenue": [1000.0, 900.0, 800.0, 700.0],
        "Operating Revenue": [1000.0, 900.0, 800.0, 700.0],  # armadilha: nunca é EBIT
        "Gross Profit": [500.0, 450.0, 400.0, 350.0],
        "Operating Income": [200.0, 180.0, 160.0, 140.0],
        "EBIT": [205.0, 185.0, 165.0, 145.0],
        "EBITDA": [250.0, 225.0, 200.0, 175.0],
        "Net Interest Income": [50.0, 50.0, 50.0, 50.0],  # armadilha: nunca é despesa de juros
        "Interest Expense": [10.0, 10.0, 10.0, 10.0],
        "Pretax Income": [190.0, 170.0, 150.0, 130.0],
        "Tax Provision": [38.0, 34.0, 30.0, 26.0],
        "Net Income": [152.0, 136.0, 120.0, 104.0],
        "Diluted EPS": [1.52, 1.36, 1.20, 1.04],
        "Diluted Average Shares": [100.0, 100.0, 100.0, 100.0],
        "Reconciled Depreciation": [50.0, 45.0, 40.0, 35.0],
    }


def standard_balance() -> dict[str, list[float]]:
    return {
        "Total Assets": [2000.0, 1800.0, 1600.0, 1400.0],
        "Current Assets": [600.0, 540.0, 480.0, 420.0],
        "Current Liabilities": [300.0, 270.0, 240.0, 210.0],
        "Total Liabilities Net Minority Interest": [1100.0, 1000.0, 900.0, 800.0],
        "Total Debt": [400.0, 400.0, 400.0, 400.0],
        "Cash Cash Equivalents And Short Term Investments": [150.0, 130.0, 110.0, 90.0],
        "Stockholders Equity": [900.0, 800.0, 700.0, 600.0],
        "Retained Earnings": [500.0, 400.0, 300.0, 200.0],
        "Ordinary Shares Number": [100.0, 100.0, 100.0, 100.0],
    }


def standard_cashflow() -> dict[str, list[float]]:
    return {
        "Operating Cash Flow": [220.0, 200.0, 180.0, -10.0],
        "Capital Expenditure": [-60.0, -55.0, -50.0, -45.0],
        "Depreciation And Amortization": [50.0, 45.0, 40.0, 35.0],
    }


def standard_info() -> dict:
    return {
        "longName": "Empresa Exemplo S.A.",
        "shortName": "Exemplo",
        "sector": "Technology",
        "industry": "Software—Application",
        "country": "United States",
        "exchange": "NMS",
        "currency": "USD",
        "financialCurrency": "USD",
        "quoteType": "EQUITY",
        "currentPrice": 50.0,
        "regularMarketPrice": 50.0,
        "marketCap": 5_000_000_000,
        "enterpriseValue": 5_250_000_000,
        "sharesOutstanding": 100_000_000,
        "forwardPE": 20.0,
        "trailingPE": 25.0,
        "pegRatio": 1.5,
        "priceToBook": 5.5,
        "dividendYield": 1.5,  # percentagem no yfinance
        "revenueGrowth": 0.11,
        "companyOfficers": [{"name": "não deve ser serializado"}],
    }


def make_ticker_data(
    ticker: str = "TEST",
    income: dict[str, list[float]] | None = None,
    balance: dict[str, list[float]] | None = None,
    cashflow: dict[str, list[float]] | None = None,
    info: dict | None = None,
    prices: pd.DataFrame | None = None,
    estimates: dict[str, pd.DataFrame] | None = None,
) -> TickerData:
    return TickerData(
        ticker=ticker,
        info=info if info is not None else standard_info(),
        income=make_statement(income if income is not None else standard_income()),
        balance=make_statement(balance if balance is not None else standard_balance()),
        cashflow=make_statement(cashflow if cashflow is not None else standard_cashflow()),
        prices=prices if prices is not None else make_prices(),
        estimates=estimates or {},
    )


@pytest.fixture
def base_config() -> dict:
    return load_config()


@pytest.fixture
def standard_ticker_data() -> TickerData:
    return make_ticker_data()

"""Métricas sobre o histórico de preços: liquidez em dólares, SMA e momentum 12-1."""
from __future__ import annotations

import math

import pandas as pd

from src.metrics.statements import NAN, to_float


def last_close(prices: pd.DataFrame | None) -> float:
    if prices is None or prices.empty or "Close" not in prices.columns:
        return NAN
    closes = pd.to_numeric(prices["Close"], errors="coerce").dropna()
    return NAN if closes.empty else float(closes.iloc[-1])


def average_dollar_volume(prices: pd.DataFrame | None, window: int = 60, min_observations: int = 20) -> float:
    """Média de Close × Volume nos últimos ``window`` dias de bolsa."""
    if prices is None or prices.empty or "Close" not in prices.columns or "Volume" not in prices.columns:
        return NAN
    dollar = (pd.to_numeric(prices["Close"], errors="coerce") * pd.to_numeric(prices["Volume"], errors="coerce")).dropna()
    if len(dollar) < min_observations:
        return NAN
    return float(dollar.tail(window).mean())


def simple_moving_average(close: pd.Series | None, window: int = 200) -> float:
    """SMA dos últimos ``window`` fechos; NaN se o histórico for mais curto."""
    if close is None:
        return NAN
    closes = pd.to_numeric(close, errors="coerce").dropna()
    if len(closes) < window:
        return NAN
    return float(closes.tail(window).mean())


def momentum_12_1(close: pd.Series | None, lookback: int = 252, skip: int = 21) -> float:
    """Retorno dos últimos ``lookback`` dias excluindo os últimos ``skip`` dias (momentum 12-1)."""
    if close is None:
        return NAN
    closes = pd.to_numeric(close, errors="coerce").dropna()
    if len(closes) <= lookback or skip >= lookback:
        return NAN
    end_price = float(closes.iloc[-1 - skip])
    start_price = float(closes.iloc[-1 - lookback])
    if start_price <= 0 or math.isnan(start_price) or math.isnan(end_price):
        return NAN
    return end_price / start_price - 1.0


def price_vs_sma(price: float, sma: float) -> float:
    """Distância relativa do preço à média móvel (0.05 = 5% acima)."""
    current = to_float(price)
    average = to_float(sma)
    if math.isnan(current) or math.isnan(average) or average <= 0:
        return NAN
    return current / average - 1.0

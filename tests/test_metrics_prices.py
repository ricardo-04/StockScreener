"""Testes das métricas de preço: volume em dólares, SMA, momentum 12-1."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from src.metrics.prices import average_dollar_volume, last_close, momentum_12_1, price_vs_sma, simple_moving_average
from tests.conftest import make_prices


def test_average_dollar_volume_uses_last_window():
    prices = make_prices(days=100, start_price=10.0, daily_return=0.0, volume=1_000)
    assert average_dollar_volume(prices, window=60) == 10_000.0
    assert math.isnan(average_dollar_volume(prices.head(5), window=60))  # poucas observações
    assert math.isnan(average_dollar_volume(pd.DataFrame(), window=60))


def test_sma_requires_full_window():
    prices = make_prices(days=250, start_price=100.0, daily_return=0.0)
    assert simple_moving_average(prices["Close"], window=200) == 100.0
    assert math.isnan(simple_moving_average(prices["Close"].head(150), window=200))


def test_momentum_12_1_skips_last_month():
    closes = pd.Series(np.linspace(100.0, 200.0, 300))
    expected = closes.iloc[-1 - 21] / closes.iloc[-1 - 252] - 1.0
    assert momentum_12_1(closes, lookback=252, skip=21) == expected
    assert math.isnan(momentum_12_1(closes.head(200), lookback=252, skip=21))


def test_price_vs_sma_and_last_close():
    assert price_vs_sma(110.0, 100.0) == pytest.approx(0.1)
    assert math.isnan(price_vs_sma(110.0, 0.0))
    prices = make_prices(days=10, start_price=5.0, daily_return=0.0)
    assert last_close(prices) == 5.0
    assert math.isnan(last_close(None))

"""Múltiplos de valuation: FCF yield (FCF/EV), EV/EBIT, FFO e P/FFO aproximado (REITs)."""
from __future__ import annotations

import math

from src.metrics.statements import NAN, to_float


def fcf_yield(free_cash_flow: float, enterprise_value: float) -> float:
    """FCF / Enterprise Value. NaN se o EV não for positivo ou faltar algo."""
    fcf, ev = to_float(free_cash_flow), to_float(enterprise_value)
    if math.isnan(fcf) or math.isnan(ev) or ev <= 0:
        return NAN
    return fcf / ev


def ev_to_ebit(enterprise_value: float, ebit: float) -> float:
    """EV / EBIT. NaN se o EBIT não for positivo (múltiplo sem significado)."""
    ev, earnings = to_float(enterprise_value), to_float(ebit)
    if math.isnan(ev) or math.isnan(earnings) or earnings <= 0 or ev <= 0:
        return NAN
    return ev / earnings


def funds_from_operations(net_income: float, depreciation_amortization: float) -> float:
    """FFO aproximado = Lucro líquido + D&A (ignora ganhos na venda de imóveis)."""
    profit, d_and_a = to_float(net_income), to_float(depreciation_amortization)
    if math.isnan(profit) or math.isnan(d_and_a):
        return NAN
    return profit + abs(d_and_a)


def price_to_ffo(market_cap: float, ffo: float) -> float:
    cap, funds = to_float(market_cap), to_float(ffo)
    if math.isnan(cap) or math.isnan(funds) or funds <= 0 or cap <= 0:
        return NAN
    return cap / funds

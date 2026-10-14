"""Margens e taxa de imposto efetiva (funções puras)."""
from __future__ import annotations

import math

from src.metrics.statements import NAN, to_float


def margin(numerator: float, revenue: float) -> float:
    """Margem = numerador / receita. NaN se a receita for nula, negativa ou desconhecida."""
    value = to_float(numerator)
    sales = to_float(revenue)
    if math.isnan(value) or math.isnan(sales) or sales <= 0:
        return NAN
    return value / sales


def effective_tax_rate(tax_provision: float, pretax_income: float) -> float:
    """Taxa efetiva = imposto / resultado antes de impostos. NaN se não for interpretável."""
    tax = to_float(tax_provision)
    pretax = to_float(pretax_income)
    if math.isnan(tax) or math.isnan(pretax) or pretax <= 0 or tax < 0:
        return NAN
    return tax / pretax


def clamp_tax_rate(rate: float, floor: float, cap: float) -> float:
    """Limita a taxa ao intervalo [floor, cap] (usado no NOPAT). NaN continua NaN."""
    value = to_float(rate)
    if math.isnan(value):
        return NAN
    return min(max(value, float(floor)), float(cap))

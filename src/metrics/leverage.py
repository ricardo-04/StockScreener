"""Endividamento e solvência: dívida líquida, Net Debt/EBITDA, cobertura de juros, liquidez.

Regra central: sem despesa de juros a cobertura é NaN. "Sem dívida" é um estado
próprio (``debt_status``) e não um número arbitrário como o 25x do código antigo.
"""
from __future__ import annotations

import math

from src.metrics.statements import NAN, to_float

DEBT_FREE = "sem_divida"
HAS_DEBT = "com_divida"
UNKNOWN = "desconhecido"


def net_debt(total_debt: float, cash: float) -> float:
    """Dívida total − caixa (e equivalentes). NaN se faltar qualquer parcela."""
    debt = to_float(total_debt)
    cash_value = to_float(cash)
    if math.isnan(debt) or math.isnan(cash_value):
        return NAN
    return debt - cash_value


def is_net_cash(net_debt_value: float) -> bool:
    value = to_float(net_debt_value)
    return not math.isnan(value) and value <= 0


def net_debt_to_ebitda(net_debt_value: float, ebitda: float) -> float:
    """Net Debt / EBITDA. NaN se o EBITDA for nulo, negativo ou desconhecido."""
    debt = to_float(net_debt_value)
    earnings = to_float(ebitda)
    if math.isnan(debt) or math.isnan(earnings) or earnings <= 0:
        return NAN
    return debt / earnings


def interest_coverage(ebit: float, interest_expense: float) -> float:
    """EBIT / |despesa de juros|. NaN se não houver despesa de juros reportada."""
    earnings = to_float(ebit)
    interest = to_float(interest_expense)
    if math.isnan(earnings) or math.isnan(interest):
        return NAN
    interest = abs(interest)
    if interest == 0:
        return NAN
    return earnings / interest


def debt_status(total_debt: float, interest_expense: float) -> str:
    """Classifica explicitamente a situação de dívida: sem_divida | com_divida | desconhecido."""
    debt = to_float(total_debt)
    if not math.isnan(debt):
        return DEBT_FREE if debt <= 0 else HAS_DEBT
    interest = to_float(interest_expense)
    if not math.isnan(interest):
        return DEBT_FREE if abs(interest) == 0 else HAS_DEBT
    return UNKNOWN


def current_ratio(current_assets: float, current_liabilities: float) -> float:
    assets = to_float(current_assets)
    liabilities = to_float(current_liabilities)
    if math.isnan(assets) or math.isnan(liabilities) or liabilities <= 0:
        return NAN
    return assets / liabilities

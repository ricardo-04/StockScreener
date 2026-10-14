"""Scores clássicos: Piotroski F-Score, Altman Z-Score e Beneish M-Score (funções puras).

Recebem um DataFrame alinhado por ano fiscal (linha 0 = mais recente, linha 1 =
anterior) com as colunas nomeadas como no builder (``net_income``,
``total_assets``, ...). Devolvem NaN quando os dados não chegam; nunca preenchem
com valores assumidos.
"""
from __future__ import annotations

import math

import pandas as pd

from src.metrics.statements import NAN, to_float

PIOTROSKI_CRITERIA = (
    "ROA positivo",
    "Cash flow operacional positivo",
    "ROA em melhoria",
    "Cash flow operacional > lucro líquido (accruals)",
    "Alavancagem de longo prazo sem aumento",
    "Current ratio em melhoria",
    "Sem emissão líquida de ações",
    "Margem bruta em melhoria",
    "Rotação do ativo em melhoria",
)

# Coeficientes de Beneish (1999), modelo de 8 variáveis
BENEISH_INTERCEPT = -4.84
BENEISH_COEFFICIENTS = {
    "dsri": 0.920, "gmi": 0.528, "aqi": 0.404, "sgi": 0.892,
    "depi": 0.115, "sgai": -0.172, "tata": 4.679, "lvgi": -0.327,
}


def _get(frame: pd.DataFrame | None, column: str, position: int) -> float:
    if frame is None or frame.empty or column not in frame.columns or position >= len(frame):
        return NAN
    return to_float(frame[column].iloc[position])


def _first(frame: pd.DataFrame | None, columns: tuple[str, ...], position: int) -> float:
    for column in columns:
        value = _get(frame, column, position)
        if not math.isnan(value):
            return value
    return NAN


def _ratio(numerator: float, denominator: float) -> float:
    if math.isnan(numerator) or math.isnan(denominator) or denominator == 0:
        return NAN
    return numerator / denominator


def _nan(*values: float) -> bool:
    return any(math.isnan(v) for v in values)


# ---------------------------------------------------------------------------
# Piotroski
# ---------------------------------------------------------------------------
def piotroski_f_score(frame: pd.DataFrame | None) -> tuple[float, int]:
    """(pontuação 0-9, nº de critérios avaliáveis). Pontuação NaN se nenhum critério for avaliável.

    A pontuação conta apenas os critérios avaliáveis; quem a usa deve verificar o
    segundo valor antes de a comparar com o limiar.
    """
    ni0, ni1 = _get(frame, "net_income", 0), _get(frame, "net_income", 1)
    ta0, ta1 = _get(frame, "total_assets", 0), _get(frame, "total_assets", 1)
    ocf0 = _get(frame, "operating_cash_flow", 0)
    ltd0 = _first(frame, ("long_term_debt", "total_debt"), 0)
    ltd1 = _first(frame, ("long_term_debt", "total_debt"), 1)
    ca0, ca1 = _get(frame, "current_assets", 0), _get(frame, "current_assets", 1)
    cl0, cl1 = _get(frame, "current_liabilities", 0), _get(frame, "current_liabilities", 1)
    sh0, sh1 = _get(frame, "shares", 0), _get(frame, "shares", 1)
    gp0, gp1 = _get(frame, "gross_profit", 0), _get(frame, "gross_profit", 1)
    rev0, rev1 = _get(frame, "revenue", 0), _get(frame, "revenue", 1)

    roa0, roa1 = _ratio(ni0, ta0), _ratio(ni1, ta1)
    lev0, lev1 = _ratio(ltd0, ta0), _ratio(ltd1, ta1)
    cr0, cr1 = _ratio(ca0, cl0), _ratio(ca1, cl1)
    gm0, gm1 = _ratio(gp0, rev0), _ratio(gp1, rev1)
    ato0, ato1 = _ratio(rev0, ta0), _ratio(rev1, ta1)

    checks: list[bool | None] = [
        None if _nan(roa0) else roa0 > 0,
        None if _nan(ocf0) else ocf0 > 0,
        None if _nan(roa0, roa1) else roa0 > roa1,
        None if _nan(ocf0, ni0) else ocf0 > ni0,
        None if _nan(lev0, lev1) else lev0 <= lev1,
        None if _nan(cr0, cr1) else cr0 > cr1,
        None if _nan(sh0, sh1) else sh0 <= sh1,
        None if _nan(gm0, gm1) else gm0 > gm1,
        None if _nan(ato0, ato1) else ato0 > ato1,
    ]
    evaluated = [c for c in checks if c is not None]
    if not evaluated:
        return NAN, 0
    return float(sum(1 for c in evaluated if c)), len(evaluated)


# ---------------------------------------------------------------------------
# Altman Z (modelo original, empresas cotadas)
# ---------------------------------------------------------------------------
def altman_z_score(working_capital: float, retained_earnings: float, ebit: float, total_assets: float,
                   market_cap: float, total_liabilities: float, revenue: float) -> float:
    """Z = 1.2·WC/TA + 1.4·RE/TA + 3.3·EBIT/TA + 0.6·MVE/TL + 1.0·Vendas/TA. NaN se faltar algo."""
    wc, re_, eb = to_float(working_capital), to_float(retained_earnings), to_float(ebit)
    ta, mve, tl, sales = to_float(total_assets), to_float(market_cap), to_float(total_liabilities), to_float(revenue)
    if _nan(wc, re_, eb, ta, mve, tl, sales) or ta <= 0 or tl <= 0:
        return NAN
    return 1.2 * wc / ta + 1.4 * re_ / ta + 3.3 * eb / ta + 0.6 * mve / tl + 1.0 * sales / ta


# ---------------------------------------------------------------------------
# Beneish M (8 variáveis)
# ---------------------------------------------------------------------------
def beneish_components(frame: pd.DataFrame | None) -> dict[str, float]:
    """Os 8 índices de Beneish (NaN individualmente quando não calculáveis)."""
    rev0, rev1 = _get(frame, "revenue", 0), _get(frame, "revenue", 1)
    rec0, rec1 = _get(frame, "receivables", 0), _get(frame, "receivables", 1)
    cogs0, cogs1 = _get(frame, "cost_of_revenue", 0), _get(frame, "cost_of_revenue", 1)
    gp0, gp1 = _get(frame, "gross_profit", 0), _get(frame, "gross_profit", 1)
    if math.isnan(cogs0) and not _nan(rev0, gp0):
        cogs0 = rev0 - gp0
    if math.isnan(cogs1) and not _nan(rev1, gp1):
        cogs1 = rev1 - gp1
    ca0, ca1 = _get(frame, "current_assets", 0), _get(frame, "current_assets", 1)
    ppe0, ppe1 = _get(frame, "net_ppe", 0), _get(frame, "net_ppe", 1)
    ta0, ta1 = _get(frame, "total_assets", 0), _get(frame, "total_assets", 1)
    dep0 = _first(frame, ("depreciation_amortization", "depreciation_amortization_cf"), 0)
    dep1 = _first(frame, ("depreciation_amortization", "depreciation_amortization_cf"), 1)
    sga0, sga1 = _get(frame, "sga_expense", 0), _get(frame, "sga_expense", 1)
    ltd0 = _first(frame, ("long_term_debt", "total_debt"), 0)
    ltd1 = _first(frame, ("long_term_debt", "total_debt"), 1)
    cl0, cl1 = _get(frame, "current_liabilities", 0), _get(frame, "current_liabilities", 1)
    ni0 = _get(frame, "net_income", 0)
    ocf0 = _get(frame, "operating_cash_flow", 0)

    gm0 = _ratio(rev0 - cogs0, rev0) if not _nan(rev0, cogs0) else NAN
    gm1 = _ratio(rev1 - cogs1, rev1) if not _nan(rev1, cogs1) else NAN

    return {
        "dsri": _ratio(_ratio(rec0, rev0), _ratio(rec1, rev1)),
        "gmi": _ratio(gm1, gm0),
        "aqi": _ratio(1.0 - _ratio(ca0 + ppe0, ta0), 1.0 - _ratio(ca1 + ppe1, ta1)) if not _nan(ca0, ppe0, ca1, ppe1) else NAN,
        "sgi": _ratio(rev0, rev1),
        "depi": _ratio(_ratio(dep1, dep1 + ppe1), _ratio(dep0, dep0 + ppe0)) if not _nan(dep0, dep1, ppe0, ppe1) else NAN,
        "sgai": _ratio(_ratio(sga0, rev0), _ratio(sga1, rev1)),
        "lvgi": _ratio(_ratio(ltd0 + cl0, ta0), _ratio(ltd1 + cl1, ta1)) if not _nan(ltd0, cl0, ltd1, cl1) else NAN,
        "tata": _ratio(ni0 - ocf0, ta0) if not _nan(ni0, ocf0) else NAN,
    }


def beneish_m_score(frame: pd.DataFrame | None) -> float:
    """M = −4.84 + 0.920·DSRI + 0.528·GMI + 0.404·AQI + 0.892·SGI + 0.115·DEPI − 0.172·SGAI + 4.679·TATA − 0.327·LVGI.

    NaN se qualquer componente não for calculável (quem usa trata como "só aviso").
    """
    components = beneish_components(frame)
    if any(math.isnan(v) for v in components.values()):
        return NAN
    return BENEISH_INTERCEPT + sum(BENEISH_COEFFICIENTS[k] * v for k, v in components.items())

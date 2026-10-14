"""Qualidade do negócio: ROIC, ROE, conversão de caixa, estabilidade da margem bruta e diluição.

Convenções: as séries/frames vêm ordenados do ano fiscal mais recente (posição 0)
para o mais antigo. Nunca se inventam valores: dados em falta dão NaN.
"""
from __future__ import annotations

import math

import pandas as pd

from src.metrics.profitability import clamp_tax_rate, effective_tax_rate
from src.metrics.statements import NAN, align_series, to_float


def invested_capital(equity: float, total_debt: float, cash: float) -> float:
    """Capital investido = Capital próprio + Dívida − Caixa."""
    e, d, c = to_float(equity), to_float(total_debt), to_float(cash)
    if math.isnan(e) or math.isnan(d) or math.isnan(c):
        return NAN
    return e + d - c


def nopat(ebit: float, tax_rate: float) -> float:
    """NOPAT = EBIT × (1 − taxa de imposto)."""
    e, t = to_float(ebit), to_float(tax_rate)
    if math.isnan(e) or math.isnan(t):
        return NAN
    return e * (1.0 - t)


def roic(ebit: float, tax_rate: float, equity: float, total_debt: float, cash: float) -> float:
    """ROIC = NOPAT / Capital investido. NaN se o capital investido não for positivo."""
    capital = invested_capital(equity, total_debt, cash)
    profit = nopat(ebit, tax_rate)
    if math.isnan(capital) or math.isnan(profit) or capital <= 0:
        return NAN
    return profit / capital


def resolve_tax_rate(tax_provision: float, pretax_income: float, reported_rate: float,
                     floor: float, cap: float, default: float) -> float:
    """Taxa efetiva (imposto/RAI) → taxa reportada → taxa por defeito; sempre limitada a [floor, cap]."""
    rate = effective_tax_rate(tax_provision, pretax_income)
    if math.isnan(rate):
        rate = to_float(reported_rate)
    if math.isnan(rate):
        rate = to_float(default)
    return clamp_tax_rate(rate, floor, cap)


def roic_series(frame: pd.DataFrame | None, tax_floor: float, tax_cap: float, tax_default: float) -> pd.Series:
    """ROIC por ano fiscal a partir de um frame com ebit, tax_provision, pretax_income,
    tax_rate_reported, equity, total_debt e cash (colunas em falta contam como NaN)."""
    if frame is None or frame.empty:
        return pd.Series(dtype=float)
    values = []
    for _, year in frame.iterrows():
        rate = resolve_tax_rate(
            year.get("tax_provision"), year.get("pretax_income"), year.get("tax_rate_reported"),
            tax_floor, tax_cap, tax_default,
        )
        values.append(roic(year.get("ebit"), rate, year.get("equity"), year.get("total_debt"), year.get("cash")))
    return pd.Series(values, index=frame.index, dtype=float)


def roe(net_income: float, equity: float) -> float:
    """ROE = Lucro líquido / Capital próprio. NaN se o capital próprio não for positivo."""
    profit, capital = to_float(net_income), to_float(equity)
    if math.isnan(profit) or math.isnan(capital) or capital <= 0:
        return NAN
    return profit / capital


def roe_series(frame: pd.DataFrame | None) -> pd.Series:
    if frame is None or frame.empty:
        return pd.Series(dtype=float)
    values = [roe(year.get("net_income"), year.get("equity")) for _, year in frame.iterrows()]
    return pd.Series(values, index=frame.index, dtype=float)


def average_of_last(series: pd.Series | None, years: int) -> tuple[float, int]:
    """(média dos valores disponíveis nos últimos ``years`` períodos, nº de valores usados)."""
    if series is None or len(series) == 0:
        return NAN, 0
    window = pd.to_numeric(series.iloc[:years], errors="coerce").dropna()
    if window.empty:
        return NAN, 0
    return float(window.mean()), int(len(window))


def count_positive_of_last(series: pd.Series | None, years: int) -> tuple[int, int]:
    """(nº de valores > 0, nº de valores observados) nos últimos ``years`` períodos."""
    if series is None or len(series) == 0:
        return 0, 0
    window = pd.to_numeric(series.iloc[:years], errors="coerce").dropna()
    return int((window > 0).sum()), int(len(window))


def cash_conversion(fcf_series: pd.Series | None, net_income_series: pd.Series | None, years: int) -> tuple[float, int]:
    """Conversão de caixa = Σ FCF / Σ Lucro líquido nos últimos ``years`` anos com ambos disponíveis.

    Usa somas (e não a média dos rácios anuais) para não ser distorcida por um ano
    de lucro quase nulo. NaN se houver menos de 2 anos ou se o lucro acumulado não for positivo.
    """
    frame = align_series(fcf=fcf_series, net_income=net_income_series)
    if frame.empty:
        return NAN, 0
    window = frame.iloc[:years].dropna()
    if len(window) < 2:
        return NAN, int(len(window))
    profit = float(window["net_income"].sum())
    if profit <= 0:
        return NAN, int(len(window))
    return float(window["fcf"].sum()) / profit, int(len(window))


def gross_margin_std_pp(gross_profit_series: pd.Series | None, revenue_series: pd.Series | None,
                        years: int, min_points: int = 3) -> float:
    """Desvio padrão (amostral) da margem bruta em pontos percentuais nos últimos ``years`` anos."""
    frame = align_series(gross_profit=gross_profit_series, revenue=revenue_series)
    if frame.empty:
        return NAN
    window = frame.iloc[:years].dropna()
    window = window[window["revenue"] > 0]
    if len(window) < min_points:
        return NAN
    margins = window["gross_profit"] / window["revenue"]
    return float(margins.std(ddof=1) * 100.0)


def share_count_cagr(shares_series: pd.Series | None, years: int) -> tuple[float, int]:
    """CAGR do nº de ações entre o ano mais recente e o mais antigo disponível até ``years`` atrás.

    Devolve (cagr, nº de anos do intervalo). Positivo = diluição; negativo = recompras líquidas.
    """
    if shares_series is None or len(shares_series) == 0:
        return NAN, 0
    values = pd.to_numeric(shares_series, errors="coerce")
    latest = to_float(values.iloc[0])
    if math.isnan(latest) or latest <= 0:
        return NAN, 0
    for span in range(min(years, len(values) - 1), 0, -1):
        oldest = to_float(values.iloc[span])
        if not math.isnan(oldest) and oldest > 0:
            return (latest / oldest) ** (1.0 / span) - 1.0, span
    return NAN, 0

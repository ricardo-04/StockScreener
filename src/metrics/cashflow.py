"""Fluxo de caixa livre (FCF) e estatísticas da sua série histórica.

FCF = Operating Cash Flow − |CapEx|. Se qualquer parcela faltar o resultado é
NaN: nunca se usa o OCF como substituto do FCF (isso sobrestimava o FCF no
código antigo).
"""
from __future__ import annotations

import math

import pandas as pd

from src.metrics.statements import NAN, align_series, to_float


def free_cash_flow(operating_cash_flow: float, capex: float) -> float:
    """FCF de um período. O CapEx pode vir com sinal negativo (yfinance) ou positivo."""
    ocf = to_float(operating_cash_flow)
    cap = to_float(capex)
    if math.isnan(ocf) or math.isnan(cap):
        return NAN
    return ocf - abs(cap)


def free_cash_flow_series(ocf_series: pd.Series | None, capex_series: pd.Series | None) -> pd.Series:
    """Série de FCF por período (mais recente primeiro); NaN onde falta OCF ou CapEx."""
    frame = align_series(operating_cash_flow=ocf_series, capex=capex_series)
    if frame.empty:
        return pd.Series(dtype=float)
    values = [free_cash_flow(row.operating_cash_flow, row.capex) for row in frame.itertuples(index=False)]
    return pd.Series(values, index=frame.index, dtype=float)


def count_positive(series: pd.Series | None, last_n: int | None = None) -> tuple[int, int]:
    """(períodos com valor > 0, períodos observados) nos últimos ``last_n`` períodos."""
    if series is None or len(series) == 0:
        return 0, 0
    values = pd.to_numeric(series, errors="coerce")
    if last_n:
        values = values.iloc[:last_n]
    observed = values.dropna()
    return int((observed > 0).sum()), int(len(observed))

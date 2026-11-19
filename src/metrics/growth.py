"""Crescimento: CAGR e quedas consecutivas (funções puras sobre séries ordenadas do mais recente ao mais antigo)."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from src.metrics.statements import NAN, to_float


def _extract_year(label: object) -> int | None:
    """Extrai o ano fiscal (inteiro) de um rótulo de índice, se aplicável."""
    if isinstance(label, bool):
        return None
    if isinstance(label, (int, np.integer)):
        year = int(label)
        return year if 1900 <= year <= 2200 else None
    year = getattr(label, "year", None)
    if year is not None:
        return int(year)
    return None


def cagr(start_value: float, end_value: float, periods: int) -> float:
    """Taxa de crescimento anual composta. NaN se os valores não forem positivos."""
    start, end = to_float(start_value), to_float(end_value)
    if math.isnan(start) or math.isnan(end) or start <= 0 or end <= 0 or periods <= 0:
        return NAN
    return (end / start) ** (1.0 / periods) - 1.0


def series_cagr(series: pd.Series | None, years: int) -> tuple[float, int]:
    """CAGR entre o valor mais recente e o valor ``years`` períodos atrás.

    Se esse ano não existir, usa o intervalo mais longo disponível (mínimo 1 ano).
    Quando o índice representa anos fiscais, calcula o intervalo real em anos em vez
    da distância posicional. Devolve (cagr, nº de anos do intervalo usado).
    """
    if series is None or len(series) == 0:
        return NAN, 0
    values = pd.to_numeric(series, errors="coerce")
    latest = to_float(values.iloc[0])
    if math.isnan(latest):
        return NAN, 0
    latest_year = _extract_year(values.index[0])
    for pos in range(len(values) - 1, 0, -1):
        older_year = _extract_year(values.index[pos])
        span = (latest_year - older_year) if (latest_year is not None and older_year is not None) else pos
        if span <= 0 or span > years:
            continue
        oldest = to_float(values.iloc[pos])
        result = cagr(oldest, latest, span)
        if not math.isnan(result):
            return result, span
    return NAN, 0


def consecutive_declines(series: pd.Series | None) -> float:
    """Nº de quedas anuais consecutivas terminando no ano mais recente (0 = o último ano não caiu).

    NaN se não houver pelo menos 2 valores válidos em anos consecutivos para comparar.
    """
    if series is None or len(series) < 2:
        return NAN
    values = pd.to_numeric(series, errors="coerce")
    if math.isnan(to_float(values.iloc[0])) or math.isnan(to_float(values.iloc[1])):
        return NAN
    y0, y1 = _extract_year(values.index[0]), _extract_year(values.index[1])
    if y0 is not None and y1 is not None and (y0 - y1) != 1:
        return NAN
    declines = 0
    for i in range(len(values) - 1):
        yi, ynext = _extract_year(values.index[i]), _extract_year(values.index[i + 1])
        if yi is not None and ynext is not None and (yi - ynext) != 1:
            break
        current, previous = to_float(values.iloc[i]), to_float(values.iloc[i + 1])
        if math.isnan(current) or math.isnan(previous) or current >= previous:
            break
        declines += 1
    return float(declines)

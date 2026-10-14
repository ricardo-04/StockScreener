"""Acesso uniforme às demonstrações financeiras (linhas = rubricas, colunas = anos fiscais).

Funções puras sobre DataFrames no formato devolvido pelo yfinance
(``Ticker.income_stmt``, ``balance_sheet``, ``cashflow``). Nunca inventam
valores: uma rubrica em falta devolve NaN/None.
"""
from __future__ import annotations

import math
from typing import Any, Sequence

import pandas as pd

NAN = float("nan")


def is_missing(value: Any) -> bool:
    """True para None, NaN, NaT ou pd.NA."""
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def to_float(value: Any) -> float:
    """Converte para float; devolve NaN se não for possível."""
    if is_missing(value):
        return NAN
    try:
        return float(value)
    except (TypeError, ValueError):
        return NAN


def statement_periods(frame: pd.DataFrame | None) -> list:
    """Períodos (colunas) com pelo menos um valor, do mais recente para o mais antigo."""
    if frame is None or frame.empty:
        return []
    columns = [col for col in frame.columns if frame[col].notna().any()]
    try:
        return sorted(columns, reverse=True)
    except TypeError:
        return columns


def _row(frame: pd.DataFrame, name: str) -> pd.Series:
    raw = frame.loc[name]
    if isinstance(raw, pd.DataFrame):  # rubrica duplicada: usa a primeira
        raw = raw.iloc[0]
    return pd.to_numeric(raw, errors="coerce").astype(float)


def get_row_with_source(frame: pd.DataFrame | None, candidates: Sequence[str]) -> tuple[pd.Series | None, str | None]:
    """Primeira rubrica disponível (com >= 1 valor) e o seu nome; série ordenada por período desc."""
    if frame is None or frame.empty:
        return None, None
    for name in candidates:
        if name in frame.index:
            series = _row(frame, name)
            if series.notna().any():
                try:
                    series = series.sort_index(ascending=False)
                except TypeError:
                    pass
                return series, name
    return None, None


def get_row(frame: pd.DataFrame | None, candidates: Sequence[str]) -> pd.Series | None:
    return get_row_with_source(frame, candidates)[0]


def get_row_combined(frame: pd.DataFrame | None, candidates: Sequence[str]) -> pd.Series | None:
    """Combina as rubricas candidatas período a período: em cada ano usa a primeira que tiver valor.

    Mantém a coerência com ``latest_value_with_source`` (que também escolhe por período).
    """
    if frame is None or frame.empty:
        return None
    combined: pd.Series | None = None
    for name in candidates:
        if name not in frame.index:
            continue
        series = _row(frame, name)
        combined = series.copy() if combined is None else combined.combine_first(series)
    if combined is None or not combined.notna().any():
        return None
    try:
        return combined.sort_index(ascending=False)
    except TypeError:
        return combined


def latest_value_with_source(frame: pd.DataFrame | None, candidates: Sequence[str]) -> tuple[float, str | None]:
    """Valor no período mais recente da demonstração, da primeira rubrica que o tenha.

    Nunca recua para anos anteriores: se a rubrica não existir no último ano, é NaN.
    """
    periods = statement_periods(frame)
    if not periods:
        return NAN, None
    latest = periods[0]
    for name in candidates:
        if name in frame.index:
            value = to_float(_row(frame, name).get(latest))
            if not math.isnan(value):
                return value, name
    return NAN, None


def latest_value(frame: pd.DataFrame | None, candidates: Sequence[str]) -> float:
    return latest_value_with_source(frame, candidates)[0]


def value_at(series: pd.Series | None, position: int = 0) -> float:
    """Valor na posição ``position`` (0 = mais recente); NaN se não existir."""
    if series is None or position >= len(series):
        return NAN
    return to_float(series.iloc[position])


def count_periods_with_value(frame: pd.DataFrame | None, anchors: Sequence[str]) -> int:
    """Número de anos fiscais em que a rubrica âncora tem valor."""
    series = get_row(frame, anchors)
    return 0 if series is None else int(series.notna().sum())


def align_series(**named: pd.Series | None) -> pd.DataFrame:
    """Alinha várias séries pelo período (união), ordenadas do mais recente ao mais antigo."""
    columns = {name: (series if series is not None else pd.Series(dtype=float)) for name, series in named.items()}
    frame = pd.DataFrame(columns)
    if frame.empty:
        return frame
    try:
        frame = frame.sort_index(ascending=False)
    except TypeError:
        pass
    return frame.astype(float)


def series_to_year_columns(series: pd.Series | None, prefix: str, years: int) -> dict[str, float]:
    """Expande uma série em colunas ``<prefix>_fy0`` (mais recente) ... ``<prefix>_fy{years-1}``."""
    return {f"{prefix}_fy{i}": value_at(series, i) for i in range(years)}

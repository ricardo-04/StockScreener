"""Estatísticas relativas ao setor: percentis, z-scores robustos winsorizados e medianas.

Calculadas sobre o universo completo (todas as empresas com dados), dentro de cada
``sector`` do yfinance. NaN mantém-se NaN: dados em falta nunca recebem o percentil 50.
"""
from __future__ import annotations

import logging
from typing import Iterable

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

SECTOR_COLUMN = "sector"
UNKNOWN_SECTOR = "Desconhecido"
MAD_SCALE = 1.4826  # torna o MAD comparável ao desvio padrão numa normal


def sector_key(df: pd.DataFrame) -> pd.Series:
    if SECTOR_COLUMN not in df.columns:
        return pd.Series([UNKNOWN_SECTOR] * len(df), index=df.index)
    key = df[SECTOR_COLUMN].astype("string").fillna("").str.strip()
    return key.where(key != "", UNKNOWN_SECTOR).astype(object)


def robust_zscore(values: pd.Series, winsor: float = 3.0, robust: bool = True) -> pd.Series:
    """z-score de uma série: (x − mediana) / (1.4826·MAD) ou (x − média)/σ, limitado a ±winsor.

    Com menos de 2 valores válidos devolve NaN; com dispersão nula devolve 0.
    """
    numeric = pd.to_numeric(values, errors="coerce").astype(float)
    valid = numeric.dropna()
    result = pd.Series(np.nan, index=numeric.index, dtype=float)
    if len(valid) < 2:
        return result
    if robust:
        center = float(valid.median())
        scale = float((valid - center).abs().median()) * MAD_SCALE
        if scale == 0:  # distribuição degenerada: recorre a média/σ
            center, scale = float(valid.mean()), float(valid.std(ddof=0))
    else:
        center, scale = float(valid.mean()), float(valid.std(ddof=0))
    if scale == 0 or np.isnan(scale):
        result.loc[valid.index] = 0.0
        return result
    z = (numeric - center) / scale
    return z.clip(lower=-winsor, upper=winsor)


def sector_percentiles(df: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    """Percentil (0-100, crescente) de cada coluna dentro do setor; NaN preservado."""
    key = sector_key(df)
    out = pd.DataFrame(index=df.index)
    for column in columns:
        if column not in df.columns:
            continue
        values = pd.to_numeric(df[column], errors="coerce")
        out[f"{column}_sector_pct"] = values.groupby(key).rank(pct=True, method="average") * 100.0
    return out


def sector_zscores(df: pd.DataFrame, columns: Iterable[str], winsor: float = 3.0, robust: bool = True) -> pd.DataFrame:
    key = sector_key(df)
    out = pd.DataFrame(index=df.index)
    for column in columns:
        if column not in df.columns:
            continue
        values = pd.to_numeric(df[column], errors="coerce")
        out[f"{column}_sector_z"] = values.groupby(key, group_keys=False).transform(
            lambda s: robust_zscore(s, winsor=winsor, robust=robust)
        )
    return out


def sector_medians(df: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    key = sector_key(df)
    out = pd.DataFrame(index=df.index)
    for column in columns:
        if column not in df.columns:
            continue
        values = pd.to_numeric(df[column], errors="coerce")
        out[f"{column}_sector_median"] = values.groupby(key).transform("median")
    return out


def add_sector_stats(
    df: pd.DataFrame,
    percentile_columns: Iterable[str],
    zscore_columns: Iterable[str],
    median_columns: Iterable[str] = (),
    winsor: float = 3.0,
    robust: bool = True,
) -> pd.DataFrame:
    """Devolve uma cópia do DataFrame com ``sector_size`` e as colunas *_sector_pct / *_sector_z / *_sector_median."""
    if df.empty:
        return df.copy()
    result = df.copy()
    key = sector_key(result)
    result["sector_size"] = key.groupby(key).transform("size").astype(int)
    pieces = [
        sector_percentiles(result, percentile_columns),
        sector_zscores(result, zscore_columns, winsor=winsor, robust=robust),
        sector_medians(result, median_columns),
    ]
    for piece in pieces:
        for column in piece.columns:
            result[column] = piece[column]
    return result

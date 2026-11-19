"""Percentis, z-scores robustos e medianas dentro do setor (NaN preservado)."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from src.metrics.sector import add_sector_stats, robust_zscore, sector_key, sector_percentiles


@pytest.fixture
def frame():
    return pd.DataFrame(
        {
            "ticker": list("ABCDEFG"),
            "sector": ["Tech", "Tech", "Tech", "Tech", "Tech", "Energy", ""],
            "fcf_yield": [0.01, 0.02, 0.03, 0.04, np.nan, 0.08, 0.05],
            "ev_to_ebit": [10.0, 20.0, 30.0, 40.0, 50.0, 8.0, 12.0],
        }
    )


def test_percentiles_are_within_sector_and_keep_nan(frame):
    pct = sector_percentiles(frame, ["fcf_yield", "ev_to_ebit"])
    tech = pct.loc[frame["sector"] == "Tech", "fcf_yield_sector_pct"]
    assert list(tech.iloc[:4]) == [25.0, 50.0, 75.0, 100.0]
    assert math.isnan(tech.iloc[4])  # NaN nunca recebe 50
    assert pct.loc[5, "fcf_yield_sector_pct"] == 100.0  # único no setor Energy
    assert pct.loc[6, "ev_to_ebit_sector_pct"] == 100.0  # setor desconhecido agrupado à parte


def test_sector_key_handles_missing_sector(frame):
    key = sector_key(frame)
    assert key.iloc[6] == "Desconhecido"
    assert key.iloc[0] == "Tech"


def test_robust_zscore_winsorizes_outliers():
    values = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 1000.0])
    z = robust_zscore(values, winsor=3.0)
    assert z.iloc[-1] == 3.0
    assert abs(z.iloc[2]) < 1.0
    assert z.max() <= 3.0 and z.min() >= -3.0
    assert robust_zscore(pd.Series([1.0]), 3.0).isna().all()
    assert (robust_zscore(pd.Series([2.0, 2.0, 2.0]), 3.0) == 0.0).all()
    classic = robust_zscore(pd.Series([1.0, 2.0, 3.0]), winsor=3.0, robust=False)
    assert classic.iloc[1] == 0.0 and classic.iloc[2] == pytest.approx(1.2247, abs=1e-3)


def test_add_sector_stats_adds_size_pct_z_and_median(frame):
    out = add_sector_stats(frame, ["fcf_yield"], ["ev_to_ebit"], ["ev_to_ebit"], winsor=3.0)
    assert list(out["sector_size"]) == [5, 5, 5, 5, 5, 1, 1]
    assert "fcf_yield_sector_pct" in out.columns and "ev_to_ebit_sector_z" in out.columns
    assert out.loc[2, "ev_to_ebit_sector_z"] == 0.0  # mediana do setor Tech
    assert out.loc[0, "ev_to_ebit_sector_median"] == 30.0
    assert math.isnan(out.loc[5, "ev_to_ebit_sector_z"])  # setor com 1 empresa
    assert len(out.columns) == len(frame.columns) + 4
    assert add_sector_stats(pd.DataFrame(), ["x"], ["y"]).empty


def test_sector_stats_separate_business_types_within_same_sector():
    df = pd.DataFrame(
        {
            "ticker": ["JPM", "BAC", "WFC", "V", "MA", "O", "PLD", "CBRE"],
            "sector": [
                "Financial Services", "Financial Services", "Financial Services",
                "Financial Services", "Financial Services",
                "Real Estate", "Real Estate", "Real Estate",
            ],
            "business_type": [
                "bank_insurance", "bank_insurance", "bank_insurance",
                "standard", "standard",
                "reit", "reit", "standard",
            ],
            "price_to_book": [1.0, 2.0, 3.0, 40.0, 50.0, 2.0, 3.0, 10.0],
        }
    )
    key = sector_key(df)
    assert key.iloc[0] == "Financial Services::bank_insurance"
    assert key.iloc[3] == "Financial Services::standard"
    assert key.iloc[5] == "Real Estate::reit"
    assert key.iloc[7] == "Real Estate::standard"

    out = add_sector_stats(df, ["price_to_book"], ["price_to_book"], ["price_to_book"], winsor=3.0)
    # Bancos agrupados entre si (size=3), separados de Visa/MA (size=2)
    assert list(out["sector_size"]) == [3, 3, 3, 2, 2, 2, 2, 1]
    assert out.loc[1, "price_to_book_sector_median"] == 2.0
    assert out.loc[1, "price_to_book_sector_z"] == 0.0
    assert out.loc[2, "price_to_book_sector_pct"] == 100.0  # WFC é o mais caro entre bancos, não barato face a V/MA
    assert out.loc[3, "price_to_book_sector_median"] == 45.0
    assert out.loc[5, "price_to_book_sector_median"] == 2.5
    assert math.isnan(out.loc[7, "price_to_book_sector_z"])  # CBRE isolado dos REITs

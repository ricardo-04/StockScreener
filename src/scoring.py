"""Etapa 7 – Ranking final das sobreviventes.

Score composto 0-100 a partir de z-scores calculados dentro do setor (sobre o
universo completo, winsorizados a ±winsor) em 4 pilares: Qualidade, Valuation,
Saúde Financeira e Momentum. Métricas por pilar, direções e pesos vêm de
``config.yaml`` (secção ``scoring``). 50 corresponde à mediana do setor.
"""
from __future__ import annotations

import logging
import math
from typing import Any

import pandas as pd

from src.metrics.statements import NAN, to_float

log = logging.getLogger(__name__)

PILLARS = ("quality", "valuation", "financial_health", "momentum")


def pillar_metric_names(cfg: dict[str, Any]) -> list[str]:
    """Todas as métricas referidas nos pilares (para calcular os z-scores setoriais)."""
    names: list[str] = []
    for specs in (cfg.get("scoring", {}).get("pillars") or {}).values():
        for spec in specs or []:
            metric = spec.get("metric")
            if metric and metric not in names:
                names.append(metric)
    return names


def _applies(spec: dict[str, Any], business_type: str) -> bool:
    types = spec.get("types")
    if types and business_type not in types:
        return False
    exclude = spec.get("exclude_types")
    if exclude and business_type in exclude:
        return False
    return True


def pillar_z(row: pd.Series, specs: list[dict[str, Any]]) -> tuple[float, int]:
    """(média dos z-scores das métricas aplicáveis, orientados pela direção; nº de métricas usadas)."""
    business_type = str(row.get("business_type") or "standard")
    values: list[float] = []
    for spec in specs or []:
        if not _applies(spec, business_type):
            continue
        z = to_float(row.get(f"{spec['metric']}_sector_z"))
        if math.isnan(z):
            continue
        values.append(float(spec.get("direction", 1)) * z)
    if not values:
        return NAN, 0
    return sum(values) / len(values), len(values)


def z_to_score(z: float, winsor: float) -> float:
    """Mapeia z ∈ [−winsor, winsor] para 0-100 (50 = mediana do setor)."""
    value = to_float(z)
    if math.isnan(value):
        return NAN
    return float(min(max(50.0 + value * (50.0 / winsor), 0.0), 100.0))


def rank_survivors(survivors: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    """Acrescenta pilares, score composto e posição; devolve ordenado do melhor para o pior."""
    scoring = cfg.get("scoring", {})
    weights = {k: float(v) for k, v in (scoring.get("weights") or {}).items()}
    pillars = scoring.get("pillars") or {}
    winsor = float(scoring.get("winsorize_z", 3.0))

    ranked = survivors.copy()
    if ranked.empty:
        for pillar in PILLARS:
            ranked[f"score_{pillar}"] = pd.Series(dtype=float)
        ranked["composite_score"] = pd.Series(dtype=float)
        ranked["rank"] = pd.Series(dtype=int)
        return ranked

    pillar_values: dict[str, list[float]] = {p: [] for p in PILLARS}
    pillar_counts: dict[str, list[int]] = {p: [] for p in PILLARS}
    composite: list[float] = []
    for _, row in ranked.iterrows():
        weighted, weight_sum = 0.0, 0.0
        for pillar in PILLARS:
            z, used = pillar_z(row, pillars.get(pillar) or [])
            pillar_values[pillar].append(z)
            pillar_counts[pillar].append(used)
            weight = weights.get(pillar, 0.0)
            if not math.isnan(z) and weight > 0:
                weighted += weight * z
                weight_sum += weight
        composite.append(weighted / weight_sum if weight_sum > 0 else NAN)

    for pillar in PILLARS:
        ranked[f"pillar_{pillar}_z"] = pillar_values[pillar]
        ranked[f"pillar_{pillar}_metrics"] = pillar_counts[pillar]
        ranked[f"score_{pillar}"] = [z_to_score(z, winsor) for z in pillar_values[pillar]]
    ranked["composite_z"] = composite
    ranked["composite_score"] = [z_to_score(z, winsor) for z in composite]

    sort_columns = ["composite_score"] + (["ticker"] if "ticker" in ranked.columns else [])
    ranked = ranked.sort_values(sort_columns, ascending=[False] + [True] * (len(sort_columns) - 1),
                                na_position="last").reset_index(drop=True)
    ranked["rank"] = range(1, len(ranked) + 1)
    return ranked

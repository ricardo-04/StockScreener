"""Explicações em texto, baseadas em regras (sem IA), para cada empresa do Top N.

Exemplo: "Passou todas as 6 etapas. Pontos fortes: ROIC médio 3 anos de 24.0%
(top 15% do setor), caixa líquida, FCF yield de 6.1% (top 30% do setor).
Pontos de atenção: crescimento de receita de 3.2% (abaixo da mediana do setor),
PEG de 2.3 (bottom 20% do setor)."

Os pontos fortes/fracos são escolhidos pela posição da métrica no setor
(colunas ``*_sector_pct`` calculadas sobre o universo), orientada pela direção
"maior é melhor" / "menor é melhor".
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

from src.metrics.classification import BANK_INSURANCE, REIT
from src.metrics.leverage import DEBT_FREE, is_net_cash
from src.metrics.statements import to_float

# metric, rótulo, formato, direção (+1 maior é melhor), tipos a que se aplica (None = todos), tipos excluídos
EXPLAIN_METRICS: list[dict[str, Any]] = [
    {"metric": "roic_avg_3y", "label": "ROIC médio 3 anos", "format": "pct", "direction": 1, "exclude": [BANK_INSURANCE]},
    {"metric": "roe_avg_3y", "label": "ROE médio 3 anos", "format": "pct", "direction": 1, "types": [BANK_INSURANCE]},
    {"metric": "fcf_yield", "label": "FCF yield", "format": "pct", "direction": 1, "exclude": [BANK_INSURANCE]},
    {"metric": "ev_to_ebit", "label": "EV/EBIT", "format": "x", "direction": -1, "exclude": [BANK_INSURANCE, REIT]},
    {"metric": "forward_pe", "label": "Forward P/E", "format": "x", "direction": -1},
    {"metric": "price_to_book", "label": "P/B", "format": "x", "direction": -1, "types": [BANK_INSURANCE]},
    {"metric": "p_ffo", "label": "P/FFO", "format": "x", "direction": -1, "types": [REIT]},
    {"metric": "dividend_yield", "label": "dividend yield", "format": "pct", "direction": 1, "types": [REIT]},
    {"metric": "revenue_cagr_3y", "label": "crescimento de receita (CAGR 3 anos)", "format": "pct", "direction": 1},
    {"metric": "gross_margin", "label": "margem bruta", "format": "pct", "direction": 1, "exclude": [BANK_INSURANCE]},
    {"metric": "operating_margin", "label": "margem operacional", "format": "pct", "direction": 1, "exclude": [BANK_INSURANCE]},
    {"metric": "cash_conversion", "label": "conversão de caixa", "format": "x", "direction": 1, "exclude": [BANK_INSURANCE]},
    {"metric": "net_debt_to_ebitda", "label": "Net Debt/EBITDA", "format": "x", "direction": -1, "exclude": [BANK_INSURANCE]},
    {"metric": "interest_coverage", "label": "cobertura de juros", "format": "x", "direction": 1, "exclude": [BANK_INSURANCE]},
    {"metric": "altman_z", "label": "Altman Z", "format": "num", "direction": 1, "exclude": [BANK_INSURANCE]},
    {"metric": "piotroski_f", "label": "Piotroski F", "format": "int", "direction": 1, "exclude": [BANK_INSURANCE]},
    {"metric": "momentum_12_1", "label": "momentum 12-1", "format": "pct", "direction": 1},
    {"metric": "peg_ratio", "label": "PEG", "format": "num", "direction": -1},
    {"metric": "share_dilution_cagr", "label": "variação anual do nº de ações", "format": "pct", "direction": -1},
]


def explain_metric_names() -> list[str]:
    return [spec["metric"] for spec in EXPLAIN_METRICS]


def format_metric(value: float, kind: str) -> str:
    number = to_float(value)
    if math.isnan(number):
        return "n/d"
    if kind == "pct":
        return f"{number:.1%}"
    if kind == "x":
        return f"{number:.1f}x"
    if kind == "int":
        return f"{number:.0f}"
    return f"{number:.1f}"


def position_phrase(effective_pct: float, sector_size: float = float("nan"), small_sector: int = 5) -> str:
    """Descrição da posição no setor a partir do percentil "quanto maior melhor".

    Em setores pequenos (menos de ``small_sector`` empresas no universo) os percentis
    são degenerados, por isso usa-se a posição ordinal ("2.º de 3 no setor").
    """
    size = to_float(sector_size)
    if not math.isnan(size) and 0 < size < small_sector:
        n = int(size)
        rank = n - int(round(effective_pct / 100.0 * n)) + 1
        rank = min(max(rank, 1), n)
        return f"{rank}.º de {n} no setor"
    if effective_pct >= 75:
        return f"top {max(1, round(100 - effective_pct)):d}% do setor"
    if effective_pct >= 50:
        return "acima da mediana do setor"
    if effective_pct >= 25:
        return "abaixo da mediana do setor"
    return f"bottom {max(1, round(effective_pct)):d}% do setor"


def _applies(spec: dict[str, Any], business_type: str) -> bool:
    types = spec.get("types")
    if types and business_type not in types:
        return False
    exclude = spec.get("exclude")
    if exclude and business_type in exclude:
        return False
    return True


def ranked_points(row: pd.Series, small_sector: int = 5) -> list[dict[str, Any]]:
    """Métricas disponíveis com posição relativa no setor, ordenadas da melhor para a pior."""
    business_type = str(row.get("business_type") or "standard")
    sector_size = to_float(row.get("sector_size"))
    points: list[dict[str, Any]] = []
    for spec in EXPLAIN_METRICS:
        if not _applies(spec, business_type):
            continue
        value = to_float(row.get(spec["metric"]))
        pct = to_float(row.get(f"{spec['metric']}_sector_pct"))
        if math.isnan(value) or math.isnan(pct):
            continue
        effective = pct if spec["direction"] > 0 else 100.0 - pct
        position = position_phrase(effective, sector_size, small_sector)
        label_value = f"{spec['label']} de {format_metric(value, spec['format'])}"
        points.append({
            "metric": spec["metric"],
            "effective": effective,
            "text": f"{label_value} ({position})",
            # ponto fraco apenas em termos relativos: a métrica até está acima da mediana
            "weak_text": f"{label_value} ({position})" if effective < 50
            else f"{label_value} ({position}, mas o ponto relativamente mais fraco)",
        })
    points.sort(key=lambda p: p["effective"], reverse=True)
    return points


def explain_company(row: pd.Series, cfg: dict[str, Any] | None = None, n_strengths: int = 3, n_weaknesses: int = 2) -> str:
    """Parágrafo com etapas passadas, 3 pontos fortes, 2 pontos de atenção e alertas do funil."""
    small_sector = int(((cfg or {}).get("stages", {}).get("valuation", {}) or {}).get("min_sector_size", 5))
    points = ranked_points(row, small_sector)
    strengths: list[str] = []
    net_debt = to_float(row.get("net_debt"))
    if str(row.get("debt_status") or "") == DEBT_FREE:
        strengths.append("sem dívida")
    elif is_net_cash(net_debt):
        strengths.append("caixa líquida")

    used: set[str] = set()
    for point in points:
        if len(strengths) >= n_strengths:
            break
        strengths.append(point["text"])
        used.add(point["metric"])

    weaknesses: list[str] = []
    for point in reversed(points):
        if len(weaknesses) >= n_weaknesses:
            break
        if point["metric"] in used:
            continue
        weaknesses.append(point["weak_text"])

    stages_passed = to_float(row.get("stages_passed"))
    parts: list[str] = []
    if not math.isnan(stages_passed) and stages_passed > 0:
        parts.append(f"Passou todas as {int(stages_passed)} etapas.")
    parts.append("Pontos fortes: " + (", ".join(strengths) if strengths else "sem métricas relativas disponíveis") + ".")
    parts.append("Pontos de atenção: " + (", ".join(weaknesses) if weaknesses else "nenhum ponto fraco relativo identificado") + ".")
    warnings = str(row.get("funnel_warnings") or "").strip()
    if warnings and warnings.lower() != "nan":
        parts.append(f"Alertas: {warnings}.")
    return " ".join(parts)

"""Ranking final (z-scores por pilar) e explicações em texto."""
from __future__ import annotations

import math

import pandas as pd
import pytest

from src.explain import explain_company, explain_metric_names, format_metric, position_phrase, ranked_points
from src.scoring import pillar_metric_names, pillar_z, rank_survivors, z_to_score

NAN = float("nan")


def test_pillar_metric_names_from_config(base_config):
    names = pillar_metric_names(base_config)
    assert "roic_avg_3y" in names and "momentum_12_1" in names and "price_to_book" in names
    assert len(names) == len(set(names))


def test_pillar_z_respects_types_and_direction():
    specs = [
        {"metric": "roic_avg_3y", "direction": 1, "exclude_types": ["bank_insurance"]},
        {"metric": "roe_avg_3y", "direction": 1, "types": ["bank_insurance"]},
        {"metric": "ev_to_ebit", "direction": -1},
    ]
    row = pd.Series({"business_type": "standard", "roic_avg_3y_sector_z": 2.0, "roe_avg_3y_sector_z": 9.0, "ev_to_ebit_sector_z": 1.0})
    assert pillar_z(row, specs) == (pytest.approx(0.5), 2)  # (2.0 + (-1.0)) / 2
    bank = pd.Series({"business_type": "bank_insurance", "roic_avg_3y_sector_z": 2.0, "roe_avg_3y_sector_z": 1.0, "ev_to_ebit_sector_z": NAN})
    assert pillar_z(bank, specs) == (1.0, 1)
    assert pillar_z(pd.Series({"business_type": "standard"}), specs) == (pytest.approx(NAN, nan_ok=True), 0)


def test_z_to_score_mapping():
    assert z_to_score(0.0, 3.0) == 50.0
    assert z_to_score(3.0, 3.0) == 100.0
    assert z_to_score(-4.0, 3.0) == 0.0
    assert math.isnan(z_to_score(NAN, 3.0))


def test_rank_survivors_orders_by_composite(base_config):
    frame = pd.DataFrame(
        {
            "ticker": ["A", "B", "C"],
            "business_type": ["standard"] * 3,
            "roic_avg_3y_sector_z": [2.0, 0.0, -1.0],
            "operating_margin_sector_z": [1.0, 0.0, -1.0],
            "fcf_yield_sector_z": [1.0, 0.0, 2.0],
            "forward_pe_sector_z": [-1.0, 0.0, NAN],
            "net_debt_to_ebitda_sector_z": [-1.0, 0.0, 1.0],
            "momentum_12_1_sector_z": [0.5, 0.0, -2.0],
        }
    )
    ranked = rank_survivors(frame, base_config)
    assert list(ranked["ticker"]) == ["A", "B", "C"]
    assert list(ranked["rank"]) == [1, 2, 3]
    assert ranked.loc[1, "composite_score"] == 50.0  # tudo na mediana
    assert ranked.loc[0, "composite_score"] > 50.0 > ranked.loc[2, "composite_score"]
    assert ranked.loc[0, "pillar_quality_metrics"] == 2
    assert ranked.loc[2, "pillar_valuation_metrics"] == 1  # forward_pe em falta é ignorado
    for pillar in ("quality", "valuation", "financial_health", "momentum"):
        assert f"score_{pillar}" in ranked.columns
    empty = rank_survivors(frame.iloc[0:0], base_config)
    assert empty.empty and "composite_score" in empty.columns


def test_rank_survivors_uses_bank_metrics(base_config):
    frame = pd.DataFrame(
        {"ticker": ["BK"], "business_type": ["bank_insurance"], "roe_avg_3y_sector_z": [1.5],
         "price_to_book_sector_z": [-1.5], "equity_to_assets_sector_z": [0.0], "momentum_12_1_sector_z": [NAN]}
    )
    ranked = rank_survivors(frame, base_config)
    assert ranked.loc[0, "pillar_quality_z"] == 1.5
    assert ranked.loc[0, "pillar_valuation_z"] == 1.5
    assert math.isnan(ranked.loc[0, "pillar_momentum_z"])
    assert ranked.loc[0, "composite_z"] == pytest.approx((0.35 * 1.5 + 0.30 * 1.5 + 0.20 * 0.0) / 0.85)


# ----------------------------------------------------------------------------- explicações
def test_format_and_position_phrases():
    assert format_metric(0.2412, "pct") == "24.1%"
    assert format_metric(12.345, "x") == "12.3x"
    assert format_metric(7.0, "int") == "7"
    assert format_metric(NAN, "pct") == "n/d"
    assert position_phrase(90.0) == "top 10% do setor"
    assert position_phrase(60.0) == "acima da mediana do setor"
    assert position_phrase(30.0) == "abaixo da mediana do setor"
    assert position_phrase(10.0) == "bottom 10% do setor"
    assert position_phrase(100.0, sector_size=3) == "1.º de 3 no setor"
    assert position_phrase(33.3, sector_size=3) == "3.º de 3 no setor"
    assert position_phrase(50.0, sector_size=2) == "2.º de 2 no setor"
    assert position_phrase(100.0, sector_size=10) == "top 1% do setor"  # setor grande: percentil
    assert "roic_avg_3y" in explain_metric_names()


def test_explain_company_has_three_strengths_two_weaknesses_and_alerts():
    row = pd.Series({
        "business_type": "standard", "stages_passed": 6, "net_debt": -1e8, "debt_status": "com_divida",
        "roic_avg_3y": 0.24, "roic_avg_3y_sector_pct": 85.0,
        "fcf_yield": 0.061, "fcf_yield_sector_pct": 70.0,
        "ev_to_ebit": 12.0, "ev_to_ebit_sector_pct": 30.0,          # menor é melhor -> efetivo 70
        "revenue_cagr_3y": 0.032, "revenue_cagr_3y_sector_pct": 35.0,
        "peg_ratio": 2.3, "peg_ratio_sector_pct": 90.0,              # menor é melhor -> efetivo 10
        "momentum_12_1": 0.05, "momentum_12_1_sector_pct": 55.0,
        "funnel_warnings": "PEG de 2.3 acima de 2.5",
    })
    text = explain_company(row)
    assert text.startswith("Passou todas as 6 etapas. Pontos fortes: caixa líquida, ROIC médio 3 anos de 24.0% (top 15% do setor), ")
    assert "Pontos de atenção: PEG de 2.3 (bottom 10% do setor), crescimento de receita (CAGR 3 anos) de 3.2% (abaixo da mediana do setor)." in text
    assert text.endswith("Alertas: PEG de 2.3 acima de 2.5.")
    points = ranked_points(row)
    assert [p["metric"] for p in points][:2] == ["roic_avg_3y", "fcf_yield"]
    assert points[-1]["metric"] == "peg_ratio"


def test_explain_company_without_relative_data():
    row = pd.Series({"business_type": "bank_insurance", "stages_passed": 6, "debt_status": "desconhecido"})
    text = explain_company(row)
    assert "sem métricas relativas disponíveis" in text
    assert "nenhum ponto fraco relativo identificado" in text
    assert "Alertas" not in text
    debt_free = pd.Series({"business_type": "standard", "debt_status": "sem_divida", "roic_avg_3y": 0.3, "roic_avg_3y_sector_pct": 99.0})
    assert "Pontos fortes: sem dívida, ROIC médio 3 anos de 30.0% (top 1% do setor)." in explain_company(debt_free)


def test_weakness_above_median_is_labelled_as_relative():
    row = pd.Series({
        "business_type": "standard", "debt_status": "com_divida", "net_debt": 1.0,
        "roic_avg_3y": 0.30, "roic_avg_3y_sector_pct": 95.0,
        "fcf_yield": 0.07, "fcf_yield_sector_pct": 90.0,
        "gross_margin": 0.60, "gross_margin_sector_pct": 85.0,
        "altman_z": 4.0, "altman_z_sector_pct": 60.0,
        "momentum_12_1": 0.08, "momentum_12_1_sector_pct": 55.0,
    })
    text = explain_company(row)
    assert "Pontos de atenção: momentum 12-1 de 8.0% (acima da mediana do setor, mas o ponto relativamente mais fraco), Altman Z de 4.0 (acima da mediana do setor, mas o ponto relativamente mais fraco)." in text

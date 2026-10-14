"""Classificação por tipo de negócio a partir de ``sector`` + ``industry`` do yfinance.

Tipos: ``standard`` (default), ``bank_insurance``, ``reit``, ``utility``,
``cyclical_commodity``. As regras vêm do config.yaml (secção ``business_types``);
``DEFAULT_RULES`` só existe para testes e uso sem configuração.

O setor "Financial Services" do yfinance inclui Visa/Mastercard (Credit
Services), bolsas e fornecedores de dados; só bancos, seguradoras, gestoras de
ativos e capital markets vão para o tratamento de financeiras. Uma heurística
opcional reclassifica "Credit Services" que são na prática bancos (sem EBIT e
com margem financeira relevante, ex.: American Express, Capital One).
"""
from __future__ import annotations

import math
from typing import Any

from src.metrics.statements import to_float

STANDARD = "standard"
BANK_INSURANCE = "bank_insurance"
REIT = "reit"
UTILITY = "utility"
CYCLICAL_COMMODITY = "cyclical_commodity"
BUSINESS_TYPES = (STANDARD, BANK_INSURANCE, REIT, UTILITY, CYCLICAL_COMMODITY)

DEFAULT_RULES: dict[str, Any] = {
    "bank_insurance": {
        "industry_keywords": ["bank", "insurance", "asset management", "capital markets",
                              "financial conglomerates", "mortgage finance"],
        "industry_exclude": ["insurance brokers"],
    },
    "reit": {"sectors": ["Real Estate"], "industry_keywords": ["reit"]},
    "utility": {"sectors": ["Utilities"]},
    "cyclical_commodity": {"sectors": ["Energy", "Basic Materials"]},
    "lender_heuristic": {
        "enabled": True,
        "industries": ["credit services"],
        "min_net_interest_income_to_revenue": 0.15,
    },
}


def _lower_set(values) -> set[str]:
    return {str(v).strip().lower() for v in (values or [])}


def _contains_any(text: str, keywords) -> bool:
    return any(str(k).strip().lower() in text for k in (keywords or []))


def classify_business_type(sector: str | None, industry: str | None, rules: dict[str, Any] | None = None) -> str:
    """Devolve o tipo de negócio segundo as regras configuradas."""
    rules = rules or DEFAULT_RULES
    sector_l = (sector or "").strip().lower()
    industry_l = (industry or "").strip().lower()

    bank_rules = rules.get("bank_insurance", {})
    excluded = _contains_any(industry_l, bank_rules.get("industry_exclude"))
    if not excluded and _contains_any(industry_l, bank_rules.get("industry_keywords")):
        return BANK_INSURANCE

    reit_rules = rules.get("reit", {})
    if sector_l in _lower_set(reit_rules.get("sectors")) and _contains_any(
        industry_l, reit_rules.get("industry_keywords") or ["reit"]
    ):
        return REIT

    if sector_l in _lower_set(rules.get("utility", {}).get("sectors")):
        return UTILITY

    if sector_l in _lower_set(rules.get("cyclical_commodity", {}).get("sectors")):
        return CYCLICAL_COMMODITY

    return STANDARD


def apply_lender_heuristic(
    business_type: str,
    industry: str | None,
    ebit: float,
    net_interest_income: float,
    revenue: float,
    rules: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Reclassifica como ``bank_insurance`` as "Credit Services" com perfil de banco.

    Critério: sem EBIT (demonstração de resultados em formato bancário) e margem
    financeira ("Net Interest Income") >= X% da receita. Visa/Mastercard têm EBIT e
    ficam ``standard``. Devolve (tipo, nota explicativa).
    """
    rules = rules or DEFAULT_RULES
    heuristic = rules.get("lender_heuristic") or {}
    if not heuristic.get("enabled", False) or business_type != STANDARD:
        return business_type, ""
    if not _contains_any((industry or "").lower(), heuristic.get("industries") or ["credit services"]):
        return business_type, ""
    if not math.isnan(to_float(ebit)):
        return business_type, ""
    nii, sales = to_float(net_interest_income), to_float(revenue)
    if math.isnan(nii) or math.isnan(sales) or sales <= 0:
        return business_type, ""
    share = nii / sales
    threshold = float(heuristic.get("min_net_interest_income_to_revenue", 0.15))
    if share >= threshold:
        return BANK_INSURANCE, f"reclassificado como bank_insurance: sem EBIT e margem financeira = {share:.0%} da receita"
    return business_type, ""

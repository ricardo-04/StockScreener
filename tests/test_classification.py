"""Classificação por tipo de negócio: financeiras verdadeiras vs. processadores de pagamentos, etc."""
from __future__ import annotations

import pytest

from src.metrics.classification import classify_business_type


@pytest.mark.parametrize(
    ("sector", "industry", "expected"),
    [
        ("Financial Services", "Credit Services", "standard"),                     # Visa / Mastercard
        ("Financial Services", "Financial Data & Stock Exchanges", "standard"),    # S&P Global, CME
        ("Financial Services", "Insurance Brokers", "standard"),                   # Aon, Marsh
        ("Financial Services", "Banks—Diversified", "bank_insurance"),             # JPMorgan
        ("Financial Services", "Banks—Regional", "bank_insurance"),
        ("Financial Services", "Insurance—Property & Casualty", "bank_insurance"), # Chubb
        ("Financial Services", "Insurance—Diversified", "bank_insurance"),         # Berkshire
        ("Financial Services", "Asset Management", "bank_insurance"),              # BlackRock
        ("Financial Services", "Capital Markets", "bank_insurance"),               # Goldman Sachs
        ("Real Estate", "REIT—Industrial", "reit"),                                # Prologis
        ("Real Estate", "Real Estate Services", "standard"),                       # CBRE
        ("Utilities", "Utilities—Regulated Electric", "utility"),                  # NextEra
        ("Energy", "Oil & Gas Integrated", "cyclical_commodity"),                  # Exxon
        ("Basic Materials", "Specialty Chemicals", "cyclical_commodity"),          # Linde
        ("Technology", "Consumer Electronics", "standard"),                        # Apple
        ("", "", "standard"),
        (None, None, "standard"),
    ],
)
def test_classification_with_default_rules(sector, industry, expected):
    assert classify_business_type(sector, industry) == expected


def test_classification_uses_config_rules(base_config):
    rules = base_config["business_types"]
    assert classify_business_type("Financial Services", "Credit Services", rules) == "standard"
    assert classify_business_type("Financial Services", "Mortgage Finance", rules) == "bank_insurance"
    assert classify_business_type("Real Estate", "REIT—Office", rules) == "reit"


def test_classification_custom_rules_override_defaults():
    rules = {"bank_insurance": {"industry_keywords": ["credit services"], "industry_exclude": []}}
    assert classify_business_type("Financial Services", "Credit Services", rules) == "bank_insurance"
    assert classify_business_type("Utilities", "Utilities—Regulated Gas", rules) == "standard"  # sem regra de utility

"""Validação estrita de config.yaml (erros de sintaxe, tipos, pesos e limites)."""
from __future__ import annotations

import copy

import pytest

from src.config import PRESETS, PRESET_NAMES, ConfigError, apply_preset, load_config, validate_config


def test_config_error_is_value_error():
    assert issubclass(ConfigError, ValueError)


def test_load_config_catches_yaml_syntax_error(tmp_path):
    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text("scoring:\n  weights: [unclosed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="sintaxe YAML"):
        load_config(bad_yaml)


def test_load_config_rejects_non_dict_top_level(tmp_path):
    list_yaml = tmp_path / "list.yaml"
    list_yaml.write_text("- item1\n- item2\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="mapeamento YAML"):
        load_config(list_yaml)

    with pytest.raises(ConfigError, match="dicionário"):
        validate_config(["not", "a", "dict"])  # type: ignore[arg-type]


def test_validate_config_rejects_negative_weights(base_config):
    cfg = copy.deepcopy(base_config)
    cfg["scoring"]["weights"] = {
        "quality": 0.60,
        "valuation": 0.40,
        "financial_health": 0.20,
        "momentum": -0.20,
    }
    with pytest.raises(ConfigError, match="negativo"):
        validate_config(cfg)


def test_validate_config_rejects_unknown_pillar_in_weights(base_config):
    cfg = copy.deepcopy(base_config)
    cfg["scoring"]["weights"] = {
        "qualit": 0.35,
        "valuation": 0.30,
        "financial_health": 0.20,
        "momentum": 0.15,
    }
    with pytest.raises(ConfigError, match="desconhecido"):
        validate_config(cfg)


@pytest.mark.parametrize("bad_winsor", [0, 0.0, -1.5, float("nan"), True])
def test_validate_config_rejects_invalid_winsorize_z(base_config, bad_winsor):
    cfg = copy.deepcopy(base_config)
    cfg["scoring"]["winsorize_z"] = bad_winsor
    with pytest.raises(ConfigError, match="winsorize_z"):
        validate_config(cfg)


@pytest.mark.parametrize("bad_top_n", [0, -5, True, 1.5, "10"])
def test_validate_config_rejects_invalid_top_n(base_config, bad_top_n):
    cfg = copy.deepcopy(base_config)
    cfg["scoring"]["top_n"] = bad_top_n
    with pytest.raises(ConfigError, match="top_n"):
        validate_config(cfg)


@pytest.mark.parametrize("preset_name", PRESET_NAMES)
def test_apply_preset_produces_valid_deep_copy(base_config, preset_name):
    before = copy.deepcopy(base_config)
    updated = apply_preset(base_config, preset_name)
    assert base_config == before  # não muta o original
    assert updated["_preset"] == preset_name
    assert sum(updated["scoring"]["weights"].values()) == pytest.approx(1.0)
    assert set(PRESETS) == {"value", "quality", "garp", "dividend"}


def test_apply_preset_rejects_unknown_name(base_config):
    with pytest.raises(ConfigError, match="Preset desconhecido"):
        apply_preset(base_config, "crypto_yolo")

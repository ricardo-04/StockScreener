"""Carregamento e validação da configuração central (config.yaml).

Todos os limiares, pesos e opções do screener vivem no ``config.yaml``; este
módulo apenas os lê, valida o mínimo indispensável e resolve caminhos relativos
à raiz do projeto.
"""
from __future__ import annotations

import copy
import math
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"

REQUIRED_SECTIONS = (
    "universe",
    "data",
    "logging",
    "output",
    "business_types",
    "metrics",
    "stages",
    "scoring",
)
# Ordem canónica das etapas do funil (também usada por --skip-stage)
STAGE_NAMES = ("eligibility", "financial_health", "quality", "growth", "valuation", "momentum")
PILLAR_NAMES = ("quality", "valuation", "financial_health", "momentum")

# Perfis de estratégia pré-definidos (--preset)
PRESETS: dict[str, dict[str, Any]] = {
    "value": {
        "scoring": {
            "weights": {"quality": 0.25, "valuation": 0.45, "financial_health": 0.20, "momentum": 0.10},
        },
        "stages": {
            "valuation": {
                "default": {
                    "min_fcf_yield_sector_pct": 50,
                    "max_ev_ebit_sector_pct": 60,
                    "forward_pe_max": 25.0,
                },
            },
        },
    },
    "quality": {
        "scoring": {
            "weights": {"quality": 0.45, "valuation": 0.20, "financial_health": 0.25, "momentum": 0.10},
        },
        "stages": {
            "quality": {
                "default": {
                    "min_roic_avg_3y": 0.15,
                    "min_cash_conversion": 0.80,
                },
            },
        },
    },
    "garp": {
        "scoring": {
            "weights": {"quality": 0.35, "valuation": 0.30, "financial_health": 0.15, "momentum": 0.20},
        },
        "stages": {
            "growth": {
                "default": {
                    "min_revenue_cagr_3y": 0.08,
                },
            },
            "valuation": {
                "default": {
                    "forward_pe_max": 35.0,
                },
            },
        },
    },
    "dividend": {
        "scoring": {
            "weights": {"quality": 0.25, "valuation": 0.35, "financial_health": 0.35, "momentum": 0.05},
        },
        "stages": {
            "financial_health": {
                "by_type": {
                    "standard": {
                        "max_net_debt_to_ebitda": 2.5,
                    },
                },
            },
            "growth": {
                "default": {
                    "min_revenue_cagr_3y": 0.01,
                },
            },
        },
    },
}
PRESET_NAMES = tuple(PRESETS.keys())


class ConfigError(ValueError):
    """Configuração inválida ou em falta."""


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Lê o YAML, valida as secções obrigatórias e devolve o dicionário de configuração."""
    cfg_path = Path(path).expanduser() if path else DEFAULT_CONFIG_PATH
    if not cfg_path.is_absolute():
        cfg_path = (Path.cwd() / cfg_path).resolve()
    if not cfg_path.exists():
        raise ConfigError(f"Ficheiro de configuração não encontrado: {cfg_path}")
    try:
        with cfg_path.open(encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Erro de sintaxe YAML em {cfg_path}: {exc}") from exc
    cfg = {} if raw is None else raw
    if not isinstance(cfg, dict):
        raise ConfigError(f"Configuração inválida em {cfg_path}: esperado um mapeamento YAML no topo")
    validate_config(cfg)
    cfg["_config_path"] = str(cfg_path)
    return cfg


def validate_config(cfg: dict[str, Any]) -> None:
    """Falha cedo se faltarem secções essenciais ou houver valores inválidos."""
    if not isinstance(cfg, dict):
        raise ConfigError("Configuração inválida: esperado um dicionário no topo")

    missing = [section for section in REQUIRED_SECTIONS if section not in cfg]
    if missing:
        raise ConfigError(f"Secções em falta no config.yaml: {', '.join(missing)}")

    scoring = cfg["scoring"]
    if not isinstance(scoring, dict):
        raise ConfigError("A secção scoring tem de ser um dicionário")

    weights = scoring.get("weights")
    if not isinstance(weights, dict) or not weights:
        raise ConfigError("scoring.weights tem de ser um dicionário não vazio")

    unknown_pillars = [name for name in weights if name not in PILLAR_NAMES]
    if unknown_pillars:
        raise ConfigError(
            f"Pilar(es) desconhecido(s) em scoring.weights: {', '.join(unknown_pillars)}. "
            f"Válidos: {', '.join(PILLAR_NAMES)}"
        )

    total = 0.0
    for name, raw_weight in weights.items():
        if isinstance(raw_weight, bool):
            raise ConfigError(f"Peso inválido em scoring.weights.{name}: {raw_weight}")
        try:
            weight = float(raw_weight)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"Peso não numérico em scoring.weights.{name}: {raw_weight}") from exc
        if math.isnan(weight) or weight < 0:
            raise ConfigError(f"Peso negativo ou inválido em scoring.weights.{name}: {weight}")
        total += weight
    if abs(total - 1.0) > 1e-6:
        raise ConfigError(f"Os pesos em scoring.weights têm de somar 1.0 (soma atual: {total:.4f})")

    if "winsorize_z" in scoring:
        raw_winsor = scoring["winsorize_z"]
        if isinstance(raw_winsor, bool):
            raise ConfigError("scoring.winsorize_z tem de ser > 0")
        try:
            winsor = float(raw_winsor)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"scoring.winsorize_z inválido: {raw_winsor}") from exc
        if math.isnan(winsor) or winsor <= 0:
            raise ConfigError(f"scoring.winsorize_z tem de ser > 0 (atual: {winsor})")

    if "top_n" in scoring:
        raw_top_n = scoring["top_n"]
        if isinstance(raw_top_n, bool) or not isinstance(raw_top_n, int) or raw_top_n < 1:
            raise ConfigError(f"scoring.top_n tem de ser um inteiro >= 1 (atual: {raw_top_n})")

    for stage in STAGE_NAMES:
        if stage not in cfg["stages"]:
            raise ConfigError(f"Etapa '{stage}' em falta na secção stages")

    for key in ("cache_ttl_hours", "max_workers", "retries"):
        if key not in cfg["data"]:
            raise ConfigError(f"Chave data.{key} em falta no config.yaml")

    raw_workers = cfg["data"]["max_workers"]
    if isinstance(raw_workers, bool):
        raise ConfigError("data.max_workers tem de ser >= 1")
    try:
        workers = int(raw_workers)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"data.max_workers inválido: {raw_workers}") from exc
    if workers < 1:
        raise ConfigError("data.max_workers tem de ser >= 1")


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)
    return base


def apply_preset(cfg: dict[str, Any], preset_name: str) -> dict[str, Any]:
    """Devolve uma cópia profunda de ``cfg`` com o preset aplicado e revalidado."""
    key = (preset_name or "").strip().lower()
    if key not in PRESETS:
        raise ConfigError(
            f"Preset desconhecido: '{preset_name}'. Válidos: {', '.join(PRESET_NAMES)}"
        )
    merged = copy.deepcopy(cfg)
    _deep_merge(merged, PRESETS[key])
    validate_config(merged)
    merged["_preset"] = key
    return merged


def resolve_path(path: str | Path) -> Path:
    """Caminho absoluto: relativo à raiz do projeto quando não for absoluto."""
    candidate = Path(path).expanduser()
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate

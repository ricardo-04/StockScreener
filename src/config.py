"""Carregamento e validação da configuração central (config.yaml).

Todos os limiares, pesos e opções do screener vivem no ``config.yaml``; este
módulo apenas os lê, valida o mínimo indispensável e resolve caminhos relativos
à raiz do projeto.
"""
from __future__ import annotations

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


class ConfigError(RuntimeError):
    """Configuração inválida ou em falta."""


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Lê o YAML, valida as secções obrigatórias e devolve o dicionário de configuração."""
    cfg_path = Path(path).expanduser() if path else DEFAULT_CONFIG_PATH
    if not cfg_path.is_absolute():
        cfg_path = (Path.cwd() / cfg_path).resolve()
    if not cfg_path.exists():
        raise ConfigError(f"Ficheiro de configuração não encontrado: {cfg_path}")
    with cfg_path.open(encoding="utf-8") as handle:
        cfg = yaml.safe_load(handle) or {}
    validate_config(cfg)
    cfg["_config_path"] = str(cfg_path)
    return cfg


def validate_config(cfg: dict[str, Any]) -> None:
    """Falha cedo se faltarem secções essenciais ou se os pesos não somarem 1."""
    missing = [section for section in REQUIRED_SECTIONS if section not in cfg]
    if missing:
        raise ConfigError(f"Secções em falta no config.yaml: {', '.join(missing)}")

    weights = cfg["scoring"].get("weights") or {}
    total = sum(float(v) for v in weights.values())
    if abs(total - 1.0) > 1e-6:
        raise ConfigError(f"Os pesos em scoring.weights têm de somar 1.0 (soma atual: {total:.4f})")

    for stage in STAGE_NAMES:
        if stage not in cfg["stages"]:
            raise ConfigError(f"Etapa '{stage}' em falta na secção stages")

    for key in ("cache_ttl_hours", "max_workers", "retries"):
        if key not in cfg["data"]:
            raise ConfigError(f"Chave data.{key} em falta no config.yaml")

    workers = int(cfg["data"]["max_workers"])
    if workers < 1:
        raise ConfigError("data.max_workers tem de ser >= 1")


def resolve_path(path: str | Path) -> Path:
    """Caminho absoluto: relativo à raiz do projeto quando não for absoluto."""
    candidate = Path(path).expanduser()
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate

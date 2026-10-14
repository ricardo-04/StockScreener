"""Infraestrutura do funil: classes base das etapas, regras e registo de eliminações.

Cada etapa recebe o DataFrame das sobreviventes e devolve (aprovadas,
eliminadas, avisos). Uma empresa é eliminada na primeira etapa em que falha,
mas ficam registadas todas as regras falhadas nessa etapa, com valor observado
e limiar. As etapas concretas vivem em ``src/funnel/stages.py`` e registam-se
com ``@register_stage``.
"""
from __future__ import annotations

import logging
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterable

import pandas as pd

from src.config import STAGE_NAMES
from src.metrics.statements import NAN, to_float

log = logging.getLogger(__name__)

PRE_STAGE_NAME = "download"
PRE_STAGE_LABEL = "Etapa 0 – Dados"


def format_value(value: Any) -> str:
    """Representação curta e legível de um valor observado/limiar."""
    if value is None:
        return "n/d"
    if isinstance(value, bool):
        return "sim" if value else "não"
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return f"{value:,}"
    number = to_float(value)
    if math.isnan(number):
        return "n/d"
    if abs(number) >= 1e9:
        return f"{number / 1e9:,.2f} mil M"
    if abs(number) >= 1e6:
        return f"{number / 1e6:,.1f} M"
    return f"{number:,.2f}"


@dataclass(frozen=True)
class RuleFailure:
    """Uma regra falhada: identificador, descrição, valor observado e limiar."""

    rule: str
    description: str
    observed: Any
    threshold: Any

    def text(self) -> str:
        return f"{self.description}: {format_value(self.observed)} (limiar {format_value(self.threshold)})"


@dataclass
class Elimination:
    """Registo de uma empresa eliminada numa etapa, com todas as regras falhadas."""

    ticker: str
    stage: str
    stage_label: str
    stage_order: int
    failures: list[RuleFailure] = field(default_factory=list)
    company: str = ""
    business_type: str = ""

    @property
    def primary(self) -> RuleFailure | None:
        return self.failures[0] if self.failures else None

    def summary(self) -> str:
        return f"{self.stage_label}: " + "; ".join(f.text() for f in self.failures)

    def to_records(self) -> list[dict[str, Any]]:
        """Uma linha por regra falhada (para a folha "Eliminadas")."""
        all_rules = "; ".join(f.text() for f in self.failures)
        return [
            {
                "ticker": self.ticker,
                "company": self.company,
                "business_type": self.business_type,
                "stage_order": self.stage_order,
                "stage": self.stage_label,
                "rule": failure.rule,
                "description": failure.description,
                "observed": format_value(failure.observed),
                "threshold": format_value(failure.threshold),
                "all_failed_rules": all_rules,
            }
            for failure in self.failures
        ]

    @classmethod
    def from_download_failure(cls, ticker: str, reason: str, rule: str = "download") -> "Elimination":
        failure = RuleFailure(rule=rule, description="Descarga/cálculo de dados", observed=reason,
                              threshold="dados disponíveis")
        return cls(ticker=ticker, stage=PRE_STAGE_NAME, stage_label=PRE_STAGE_LABEL, stage_order=0,
                   failures=[failure])


# ---------------------------------------------------------------------------
# Auxiliares para escrever regras
# ---------------------------------------------------------------------------
def check_min(value: Any, threshold: float, rule: str, description: str, nan_fails: bool = True) -> RuleFailure | None:
    """Falha se ``value < threshold``. Valor em falta falha (``nan_fails``) ou é ignorado."""
    number = to_float(value)
    if math.isnan(number):
        return RuleFailure(rule, description, NAN, threshold) if nan_fails else None
    return None if number >= threshold else RuleFailure(rule, description, number, threshold)


def check_max(value: Any, threshold: float, rule: str, description: str, nan_fails: bool = True) -> RuleFailure | None:
    """Falha se ``value > threshold``. Valor em falta falha (``nan_fails``) ou é ignorado."""
    number = to_float(value)
    if math.isnan(number):
        return RuleFailure(rule, description, NAN, threshold) if nan_fails else None
    return None if number <= threshold else RuleFailure(rule, description, number, threshold)


def row_number(row: pd.Series, column: str) -> float:
    """Valor numérico de uma coluna da linha (NaN se não existir)."""
    return to_float(row.get(column))


def row_text(row: pd.Series, column: str, default: str = "") -> str:
    value = row.get(column)
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    text = str(value)
    return text if text else default


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value)


# ---------------------------------------------------------------------------
# Classe base
# ---------------------------------------------------------------------------
class Stage(ABC):
    """Uma etapa do funil. Subclasses definem ``name``, ``order``, ``label`` e ``evaluate``."""

    name: str = "base"
    order: int = 0
    label: str = "Etapa"

    def __init__(self, config: dict[str, Any] | None = None, global_config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.global_config = global_config or {}

    def prepare(self, df: pd.DataFrame) -> None:
        """Gancho opcional para cálculos ao nível do DataFrame."""

    @abstractmethod
    def evaluate(self, row: pd.Series) -> list[RuleFailure]:
        """Regras falhadas por uma empresa (lista vazia = aprovada)."""

    def warnings(self, row: pd.Series) -> list[str]:
        """Avisos que não eliminam (ex.: PEG elevado, "faca a cair")."""
        return []

    def apply(self, df: pd.DataFrame) -> tuple[pd.DataFrame, list[Elimination], dict[str, list[str]]]:
        """Aplica a etapa: devolve (aprovadas, eliminadas, avisos por ticker)."""
        self.prepare(df)
        eliminations: list[Elimination] = []
        kept: list[Any] = []
        warnings: dict[str, list[str]] = {}
        for index, row in df.iterrows():
            ticker = _text(row.get("ticker", index)) or str(index)
            failures = list(self.evaluate(row))
            notes = list(self.warnings(row))
            if notes:
                warnings[ticker] = notes
            if failures:
                eliminations.append(
                    Elimination(
                        ticker=ticker,
                        stage=self.name,
                        stage_label=self.label,
                        stage_order=self.order,
                        failures=failures,
                        company=_text(row.get("company", "")),
                        business_type=_text(row.get("business_type", "")),
                    )
                )
                log.debug("%s eliminado em %s: %s", ticker, self.label, "; ".join(f.text() for f in failures))
            else:
                kept.append(index)
        return df.loc[kept].copy(), eliminations, warnings


# ---------------------------------------------------------------------------
# Registo de etapas
# ---------------------------------------------------------------------------
STAGE_REGISTRY: dict[str, type[Stage]] = {}


def register_stage(cls: type[Stage]) -> type[Stage]:
    STAGE_REGISTRY[cls.name] = cls
    return cls


def build_stages(cfg: dict[str, Any], skip: Iterable[str] = ()) -> list[Stage]:
    """Instancia as etapas ativas na configuração, pela ordem canónica, ignorando ``skip``."""
    skipped = {s.strip().lower() for s in skip}
    stages: list[Stage] = []
    for name in STAGE_NAMES:
        stage_cfg = cfg.get("stages", {}).get(name, {})
        if name in skipped:
            log.info("Etapa '%s' ignorada por --skip-stage", name)
            continue
        if not stage_cfg.get("enabled", True):
            log.info("Etapa '%s' desligada no config.yaml", name)
            continue
        stage_cls = STAGE_REGISTRY.get(name)
        if stage_cls is None:
            log.warning("Etapa '%s' não tem implementação registada", name)
            continue
        stages.append(stage_cls(stage_cfg, cfg))
    return sorted(stages, key=lambda s: s.order)

"""Motor do funil: corre as etapas em sequência e regista eliminações e contagens."""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

import pandas as pd

from src.funnel.base import PRE_STAGE_LABEL, PRE_STAGE_NAME, Elimination, Stage

log = logging.getLogger(__name__)

ELIMINATION_COLUMNS = (
    "ticker", "company", "business_type", "stage_order", "stage", "rule",
    "description", "observed", "threshold", "all_failed_rules",
)


@dataclass
class StageCount:
    name: str
    label: str
    order: int
    entered: int
    passed: int

    @property
    def eliminated(self) -> int:
        return self.entered - self.passed


@dataclass
class FunnelResult:
    """Resultado completo: sobreviventes, eliminações, contagens por etapa e avisos."""

    universe_size: int
    survivors: pd.DataFrame
    eliminations: list[Elimination]
    stage_counts: list[StageCount]
    warnings: dict[str, list[str]] = field(default_factory=dict)

    @property
    def n_survivors(self) -> int:
        return len(self.survivors)

    def eliminations_table(self) -> pd.DataFrame:
        """Uma linha por regra falhada (ticker, etapa, regra, valor observado, limiar)."""
        records = [record for elimination in self.eliminations for record in elimination.to_records()]
        table = pd.DataFrame(records, columns=list(ELIMINATION_COLUMNS))
        if not table.empty:
            table = table.sort_values(["stage_order", "ticker", "rule"]).reset_index(drop=True)
        return table

    def rule_counts(self) -> Counter:
        """Quantas empresas falharam cada regra (uma contagem por empresa e regra)."""
        counter: Counter = Counter()
        for elimination in self.eliminations:
            for failure in elimination.failures:
                counter[f"{elimination.stage_label} · {failure.description}"] += 1
        return counter

    def top_rules(self, n: int = 5) -> list[tuple[str, int]]:
        return self.rule_counts().most_common(n)

    def stage_table(self) -> pd.DataFrame:
        rows = [
            {"etapa": c.label, "entraram": c.entered, "passaram": c.passed, "eliminadas": c.eliminated}
            for c in self.stage_counts
        ]
        return pd.DataFrame(rows, columns=["etapa", "entraram", "passaram", "eliminadas"])

    def summary_text(self, top_n: int | None = None, n_rules: int = 5) -> str:
        """Ex.: "503 empresas → Etapa 1: 471 → ... → 27 sobreviventes → Top 20"."""
        parts = [f"{self.universe_size} empresas"]
        parts += [f"{count.label}: {count.passed}" for count in self.stage_counts]
        parts.append(f"{self.n_survivors} sobreviventes")
        if top_n:
            parts.append(f"Top {min(int(top_n), self.n_survivors)}")
        lines = [" → ".join(parts)]
        if len(self.stage_counts) <= 1:
            lines.append("(nenhuma etapa do funil ativa: as etapas 1-6 chegam na Fase 2)")
        top = self.top_rules(n_rules)
        if top:
            lines.append(f"Regras que mais eliminaram (top {len(top)}):")
            lines += [f"  {i + 1}. {rule} — {count}" for i, (rule, count) in enumerate(top)]
        return "\n".join(lines)


class FunnelEngine:
    """Executa as etapas por ordem; cada empresa sai na primeira etapa em que falha."""

    def __init__(self, stages: Iterable[Stage]) -> None:
        self.stages = sorted(stages, key=lambda s: s.order)

    def run(self, df: pd.DataFrame, pre_eliminations: Iterable[Elimination] = ()) -> FunnelResult:
        pre = list(pre_eliminations)
        universe_size = len(df) + len(pre)
        eliminations: list[Elimination] = list(pre)
        counts = [StageCount(PRE_STAGE_NAME, PRE_STAGE_LABEL, 0, universe_size, len(df))]
        warnings: dict[str, list[str]] = {}
        current = df.copy()

        for stage in self.stages:
            entered = len(current)
            current, stage_eliminations, stage_warnings = stage.apply(current)
            eliminations.extend(stage_eliminations)
            for ticker, notes in stage_warnings.items():
                warnings.setdefault(ticker, []).extend(notes)
            counts.append(StageCount(stage.name, stage.label, stage.order, entered, len(current)))
            log.info("%s: %d entraram, %d passaram, %d eliminadas",
                     stage.label, entered, len(current), entered - len(current))

        survivors = current.copy()
        if "ticker" in survivors.columns:
            survivors["funnel_warnings"] = survivors["ticker"].map(lambda t: "; ".join(warnings.get(str(t), [])))
        survivors["stages_passed"] = len(self.stages)
        return FunnelResult(
            universe_size=universe_size,
            survivors=survivors,
            eliminations=eliminations,
            stage_counts=counts,
            warnings=warnings,
        )

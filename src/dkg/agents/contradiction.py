"""Contradiction review agent."""

from __future__ import annotations

from ..core.db import Database
from ..evidence.contradiction import scan_contradictions
from .base import Agent, Task, TaskResult


class ContradictionAgent(Agent):
    name = "contradiction"

    def __init__(self, db: Database) -> None:
        self.db = db

    def handles(self, kind: str) -> bool:
        return kind in ("contradiction.scan",)

    def run(self, task: Task) -> TaskResult:
        # scan_contradictions, not find_contradictions: a truncated scan that
        # reports its count without saying it was truncated reads as a complete
        # answer, and a caller acting on "3 conflicts" when the real number was
        # capped is acting on a false statement.
        report = scan_contradictions(self.db, tenant_id=str(task.input.get("tenant_id", "local")))
        return TaskResult(
            ok=True,
            output={
                "conflicts": report.signals,
                "count": len(report.signals),
                "truncated": report.truncated,
                "claims_scanned": report.claims_scanned,
                "pair_comparisons": report.comparisons,
            },
            used_units=len(report.signals),
        )

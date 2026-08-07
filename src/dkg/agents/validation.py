"""Evidence validation agent."""

from __future__ import annotations

from ..core.db import Database
from ..evidence.confidence import ConfidenceInputs, score_confidence
from .base import Agent, Task, TaskResult


class ValidationAgent(Agent):
    name = "validation"

    def __init__(self, db: Database) -> None:
        self.db = db

    def handles(self, kind: str) -> bool:
        return kind in ("evidence.score", "evidence.validate")

    def run(self, task: Task) -> TaskResult:
        claim_id = task.input.get("claim_id")
        if not claim_id:
            return TaskResult(ok=False, error={"code": "input", "message": "claim_id required"})
        supp = self.db.fetchone(
            "SELECT COUNT(*) AS n FROM relationships WHERE subject_id = "
            "(SELECT subject_id FROM claims WHERE claim_id = ?) AND support='supports';",
            (claim_id,),
        )
        cont = self.db.fetchone(
            "SELECT COUNT(*) AS n FROM relationships WHERE subject_id = "
            "(SELECT subject_id FROM claims WHERE claim_id = ?) AND support='contradicts';",
            (claim_id,),
        )
        result = score_confidence(
            ConfidenceInputs(
                source_quality=float(task.input.get("source_quality", 0.5)),
                n_supporting=int(supp["n"] if supp else 0),
                n_contradicting=int(cont["n"] if cont else 0),
                days_since_ingest=int(task.input.get("days_since_ingest", 0)),
            )
        )
        return TaskResult(
            ok=True,
            output={"claim_id": claim_id, "score": result.score, "explain": result.explain},
            used_units=1,
        )

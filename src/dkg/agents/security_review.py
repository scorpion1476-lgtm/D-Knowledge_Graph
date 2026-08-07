"""Security review agent: run prompt-injection scan and redaction check on chunks."""

from __future__ import annotations

from ..core.db import Database
from ..security.prompt_defense import scan
from ..security.redact import redact
from .base import Agent, Task, TaskResult


class SecurityReviewAgent(Agent):
    name = "security_review"

    def __init__(self, db: Database) -> None:
        self.db = db

    def handles(self, kind: str) -> bool:
        return kind in ("security.scan",)

    def run(self, task: Task) -> TaskResult:
        limit = int(task.input.get("limit", 500))
        rows = self.db.fetchall(
            "SELECT chunk_id, text FROM chunks ORDER BY rowid DESC LIMIT ?;",
            (limit,),
        )
        alerts = []
        for r in rows:
            report = scan(r["text"])
            if report.suspicious:
                alerts.append(
                    {"chunk_id": r["chunk_id"], "score": report.score, "hits": report.hits}
                )
            _redacted, red_report = redact(r["text"])
            if red_report.matched:
                alerts.append(
                    {
                        "chunk_id": r["chunk_id"],
                        "redaction_matches": red_report.matched,
                        "kind": "credentials_found",
                    }
                )
        return TaskResult(
            ok=True,
            output={"alerts": alerts, "chunks_scanned": len(rows)},
            used_units=len(rows),
        )

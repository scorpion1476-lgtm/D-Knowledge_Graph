"""Graph curation agent: merge entities that share a canonical name."""

from __future__ import annotations

from ..core.db import Database
from .base import Agent, Task, TaskResult


class CurationAgent(Agent):
    name = "curation"

    def __init__(self, db: Database) -> None:
        self.db = db

    def handles(self, kind: str) -> bool:
        return kind in ("curate.merge_duplicates",)

    def run(self, task: Task) -> TaskResult:
        tenant_id = str(task.input.get("tenant_id", "local"))
        rows = self.db.fetchall(
            "SELECT canonical, kind, COUNT(*) AS n FROM entities "
            "WHERE tenant_id=? GROUP BY canonical, kind HAVING n > 1;",
            (tenant_id,),
        )
        merged = 0
        details = []
        for r in rows:
            duplicates = self.db.fetchall(
                "SELECT entity_id FROM entities WHERE tenant_id=? AND canonical=? AND kind=?;",
                (tenant_id, r["canonical"], r["kind"]),
            )
            if len(duplicates) < 2:
                continue
            keep = duplicates[0]["entity_id"]
            for d in duplicates[1:]:
                dup = d["entity_id"]
                self.db.execute(
                    "UPDATE mentions SET entity_id=? WHERE entity_id=?;", (keep, dup)
                )
                self.db.execute(
                    "UPDATE claims SET subject_id=? WHERE subject_id=?;", (keep, dup)
                )
                self.db.execute(
                    "UPDATE relationships SET subject_id=? WHERE subject_id=?;", (keep, dup)
                )
                self.db.execute(
                    "UPDATE relationships SET object_id=? WHERE object_id=?;", (keep, dup)
                )
                self.db.execute("DELETE FROM entities WHERE entity_id=?;", (dup,))
                merged += 1
                details.append({"kept": keep, "removed": dup, "canonical": r["canonical"]})
        return TaskResult(
            ok=True,
            output={"merged_entities": merged, "details": details},
            used_units=merged,
        )

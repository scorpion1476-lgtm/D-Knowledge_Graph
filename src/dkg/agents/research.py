"""Deterministic research agent.

Given a query, retrieve top-K chunks via hybrid search, collate their
entities, and return an outline. Uses no external model.
"""

from __future__ import annotations

from ..core.db import Database
from ..search.hybrid import hybrid_search
from .base import Agent, Task, TaskResult


class ResearchAgent(Agent):
    name = "research"

    def __init__(self, db: Database) -> None:
        self.db = db

    def handles(self, kind: str) -> bool:
        return kind in ("research.gather", "research.outline")

    def run(self, task: Task) -> TaskResult:
        query = str(task.input.get("query", ""))
        limit = int(task.input.get("limit", 10))
        if not query:
            return TaskResult(ok=False, error={"code": "input", "message": "query is required"})
        results = hybrid_search(self.db, query, limit=limit)
        chunk_ids = [r["chunk_id"] for r in results]
        entities = self.db.fetchall(
            "SELECT DISTINCT e.entity_id, e.kind, e.display FROM mentions m "
            "JOIN entities e ON e.entity_id = m.entity_id "
            f"WHERE m.chunk_id IN ({','.join('?' * len(chunk_ids))});" if chunk_ids else
            "SELECT entity_id, kind, display FROM entities WHERE 0=1;",
            chunk_ids or (),
        )
        outline = {
            "query": query,
            "top_chunks": results,
            "entities": [dict(e) for e in entities],
            "notes": [
                "Deterministic research agent. No LLM was invoked.",
                "Ranking uses reciprocal-rank fusion of keyword and FTS5 signals.",
            ],
        }
        return TaskResult(ok=True, output=outline, used_units=len(results))

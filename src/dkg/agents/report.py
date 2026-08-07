"""Report generation agent."""

from __future__ import annotations

from ..core.db import Database
from .base import Agent, Task, TaskResult


class ReportAgent(Agent):
    name = "report"

    def __init__(self, db: Database) -> None:
        self.db = db

    def handles(self, kind: str) -> bool:
        return kind in ("report.render",)

    def run(self, task: Task) -> TaskResult:
        sections = task.input.get("sections") or []
        query = str(task.input.get("query", ""))
        parts: list[str] = []
        parts.append("# D-Knowledge_Graph report")
        if query:
            parts.append(f"\n_Query:_ **{query}**")
        for s in sections:
            parts.append(f"\n## {s.get('title', 'section')}\n")
            body = s.get("body")
            if isinstance(body, list):
                for item in body:
                    parts.append(f"- {item}")
            elif isinstance(body, str):
                parts.append(body)
        markdown = "\n".join(parts) + "\n"
        return TaskResult(ok=True, output={"markdown": markdown}, used_units=len(sections))

"""Ingestion agent: wraps ingest_path with structured task input."""

from __future__ import annotations

from pathlib import Path

from ..core.db import Database
from ..ingest.base import ingest_path
from .base import Agent, Task, TaskResult


class IngestionAgent(Agent):
    name = "ingestion"

    def __init__(self, db: Database) -> None:
        self.db = db

    def handles(self, kind: str) -> bool:
        return kind in ("ingest.path", "ingest.dir")

    def run(self, task: Task) -> TaskResult:
        path = task.input.get("path")
        if not path:
            return TaskResult(ok=False, error={"code": "input", "message": "path is required"})
        recursive = bool(task.input.get("recursive", False))
        forced_format = task.input.get("format")
        try:
            report = ingest_path(
                self.db,
                Path(path),
                forced_format=forced_format,
                recursive=recursive,
            )
        except Exception as e:
            return TaskResult(ok=False, error={"code": "ingest", "message": str(e)})
        return TaskResult(ok=True, output=report, used_units=int(report.get("chunks_added", 0)))

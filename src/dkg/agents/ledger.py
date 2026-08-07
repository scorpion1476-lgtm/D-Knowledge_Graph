"""Append-only shared evidence ledger for agents.

Backed by ``task_runs`` in the database plus a mirror on disk. Callers write
one row per task with a starting event and a completion event.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.db import Database
from ..core.ids import ulid_like


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentLedger:
    def __init__(self, db: Database, ledger_path: Path | None = None) -> None:
        self.db = db
        self.ledger_path = ledger_path
        if self.ledger_path is not None:
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)

    def start(
        self,
        *,
        agent: str,
        kind: str,
        input_: dict[str, Any],
        parent_id: str | None = None,
        budget_units: int = 100,
        tenant_id: str = "local",
    ) -> str:
        run_id = ulid_like()
        self.db.execute(
            """
            INSERT INTO task_runs(
                task_run_id, parent_id, tenant_id, agent, kind, status,
                started_at, budget_units, used_units, input_json, output_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?);
            """,
            (
                run_id,
                parent_id,
                tenant_id,
                agent,
                kind,
                "running",
                _now(),
                budget_units,
                0,
                json.dumps(input_, sort_keys=True, ensure_ascii=False),
                "{}",
            ),
        )
        self._append(
            {"event": "start", "task_run_id": run_id, "agent": agent, "kind": kind}
        )
        return run_id

    def finish(
        self,
        run_id: str,
        *,
        status: str,
        output: dict[str, Any],
        error: dict[str, Any] | None = None,
        used_units: int = 0,
    ) -> None:
        self.db.execute(
            """
            UPDATE task_runs
               SET status=?, finished_at=?, output_json=?, error_json=?, used_units=?
             WHERE task_run_id=?;
            """,
            (
                status,
                _now(),
                json.dumps(output, sort_keys=True, ensure_ascii=False),
                json.dumps(error, sort_keys=True, ensure_ascii=False) if error else None,
                int(used_units),
                run_id,
            ),
        )
        self._append(
            {
                "event": "finish",
                "task_run_id": run_id,
                "status": status,
                "used_units": used_units,
                "error": error,
            }
        )

    def get(self, run_id: str) -> dict | None:
        row = self.db.fetchone(
            "SELECT * FROM task_runs WHERE task_run_id = ?;", (run_id,)
        )
        return dict(row) if row else None

    def _append(self, payload: dict) -> None:
        if self.ledger_path is None:
            return
        with self.ledger_path.open("a", encoding="utf-8") as f:
            payload = {"ts": _now(), **payload}
            f.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

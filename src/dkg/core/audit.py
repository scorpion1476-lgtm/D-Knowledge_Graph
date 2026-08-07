"""Append-only audit log with a per-row hash chain.

Any deletion or manipulation of a prior row breaks the chain and is detectable
at verify time. The audit log is stored in SQLite so it participates in
backup/restore; a mirrored line-delimited JSON journal is also written under
``$DKG_HOME/audit.log`` for out-of-band inspection.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .db import Database
from .ids import ulid_like


@dataclass
class AuditEntry:
    action: str
    outcome: str
    tenant_id: str = "local"
    actor: str = "system"
    subject_kind: str | None = None
    subject_id: str | None = None
    details: dict = field(default_factory=dict)


def _canonical_bytes(row: dict) -> bytes:
    return json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


class AuditLog:
    def __init__(self, db: Database, journal_path: Path | None = None) -> None:
        self.db = db
        self.journal_path = journal_path
        if self.journal_path is not None:
            self.journal_path.parent.mkdir(parents=True, exist_ok=True)

    def _last_hash(self) -> str | None:
        # Use SQLite's implicit ROWID for insertion order. Windows and macOS
        # datetime precision differ, so sorting by ts + audit_id can invert
        # rapid-succession inserts and break the chain.
        row = self.db.fetchone(
            "SELECT row_hash FROM audit_log ORDER BY ROWID DESC LIMIT 1;"
        )
        return row["row_hash"] if row else None

    def record(self, entry: AuditEntry) -> str:
        audit_id = ulid_like()
        ts = datetime.now(timezone.utc).isoformat()
        prev = self._last_hash()
        body = {
            "audit_id": audit_id,
            "ts": ts,
            "tenant_id": entry.tenant_id,
            "actor": entry.actor,
            "action": entry.action,
            "subject_kind": entry.subject_kind,
            "subject_id": entry.subject_id,
            "outcome": entry.outcome,
            "details": entry.details,
            "prev_hash": prev,
        }
        row_hash = hashlib.sha256(_canonical_bytes(body)).hexdigest()
        self.db.execute(
            """
            INSERT INTO audit_log(
                audit_id, ts, tenant_id, actor, action, subject_kind,
                subject_id, outcome, details_json, prev_hash, row_hash
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?);
            """,
            (
                audit_id,
                ts,
                entry.tenant_id,
                entry.actor,
                entry.action,
                entry.subject_kind,
                entry.subject_id,
                entry.outcome,
                json.dumps(entry.details, sort_keys=True, ensure_ascii=False),
                prev,
                row_hash,
            ),
        )
        if self.journal_path is not None:
            with self.journal_path.open("a", encoding="utf-8") as f:
                body["row_hash"] = row_hash
                f.write(json.dumps(body, sort_keys=True, ensure_ascii=False) + "\n")
        return audit_id

    def list(self, limit: int = 100) -> list[dict]:
        rows = self.db.fetchall(
            "SELECT * FROM audit_log ORDER BY ROWID DESC LIMIT ?;",
            (int(limit),),
        )
        out = []
        for r in rows:
            d = dict(r)
            d["details"] = json.loads(d.pop("details_json") or "{}")
            out.append(d)
        return out

    def verify_chain(self) -> tuple[bool, str | None]:
        """Verify the hash chain from the earliest entry forward.

        Returns (True, None) on success, or (False, audit_id_of_first_break)
        on failure. This is the operation an administrator runs to detect
        tampering.
        """
        rows = self.db.fetchall(
            "SELECT * FROM audit_log ORDER BY ROWID ASC;"
        )
        prev_hash: str | None = None
        for r in rows:
            body = {
                "audit_id": r["audit_id"],
                "ts": r["ts"],
                "tenant_id": r["tenant_id"],
                "actor": r["actor"],
                "action": r["action"],
                "subject_kind": r["subject_kind"],
                "subject_id": r["subject_id"],
                "outcome": r["outcome"],
                "details": json.loads(r["details_json"] or "{}"),
                "prev_hash": prev_hash,
            }
            expected = hashlib.sha256(_canonical_bytes(body)).hexdigest()
            if expected != r["row_hash"]:
                return False, r["audit_id"]
            prev_hash = r["row_hash"]
        return True, None

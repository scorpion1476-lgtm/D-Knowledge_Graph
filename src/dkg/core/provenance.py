"""Provenance envelope helpers.

Every ingested source and every derived record carries a provenance envelope
recording where the data came from, when it was recorded, what actor recorded
it, and by what method. The envelope is intentionally small and JSON-serialisable
so it can be embedded in exports and MCP responses.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .db import Database
from .ids import random_id


@dataclass
class ProvenanceEnvelope:
    subject_kind: str
    subject_id: str
    actor: str
    method: str
    inputs: dict[str, Any] = field(default_factory=dict)
    recorded_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    signature: str | None = None
    tenant_id: str = "local"

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject_kind": self.subject_kind,
            "subject_id": self.subject_id,
            "actor": self.actor,
            "method": self.method,
            "inputs": self.inputs,
            "recorded_at": self.recorded_at,
            "signature": self.signature,
            "tenant_id": self.tenant_id,
        }


def record_provenance(db: Database, env: ProvenanceEnvelope) -> str:
    """Persist an envelope. Returns the new provenance_id."""
    prov_id = random_id("prov")
    db.execute(
        """
        INSERT INTO provenance(
            provenance_id, tenant_id, subject_kind, subject_id,
            recorded_at, actor, method, inputs_json, signature
        )
        VALUES (?,?,?,?,?,?,?,?,?);
        """,
        (
            prov_id,
            env.tenant_id,
            env.subject_kind,
            env.subject_id,
            env.recorded_at,
            env.actor,
            env.method,
            json.dumps(env.inputs, sort_keys=True, ensure_ascii=False),
            env.signature,
        ),
    )
    return prov_id


def fetch_provenance(db: Database, subject_kind: str, subject_id: str) -> list[dict[str, Any]]:
    rows = db.fetchall(
        """
        SELECT provenance_id, tenant_id, subject_kind, subject_id, recorded_at,
               actor, method, inputs_json, signature
        FROM provenance
        WHERE subject_kind = ? AND subject_id = ?
        ORDER BY recorded_at ASC;
        """,
        (subject_kind, subject_id),
    )
    out = []
    for r in rows:
        d = dict(r)
        d["inputs"] = json.loads(d.pop("inputs_json") or "{}")
        out.append(d)
    return out

"""Export the graph and content to JSON."""

from __future__ import annotations

import json
from pathlib import Path

from ..core.db import Database


def collect(db: Database, source_id: str | None = None) -> dict:
    def q(sql, params=()):
        return [dict(r) for r in db.fetchall(sql, params)]

    if source_id is None:
        sources = q("SELECT * FROM sources ORDER BY added_at;")
        documents = q("SELECT * FROM documents ORDER BY ingested_at;")
        chunks = q("SELECT * FROM chunks ORDER BY document_id, ord;")
        entities = q("SELECT * FROM entities ORDER BY canonical;")
        relationships = q("SELECT * FROM relationships;")
        claims = q("SELECT * FROM claims;")
    else:
        sources = q("SELECT * FROM sources WHERE source_id=?;", (source_id,))
        documents = q("SELECT * FROM documents WHERE source_id=?;", (source_id,))
        doc_ids = [d["document_id"] for d in documents]
        if doc_ids:
            placeholders = ",".join("?" * len(doc_ids))
            chunks = q(f"SELECT * FROM chunks WHERE document_id IN ({placeholders});", doc_ids)
        else:
            chunks = []
        entities = q("SELECT * FROM entities;")
        relationships = q("SELECT * FROM relationships;")
        claims = q("SELECT * FROM claims;")
    return {
        "sources": sources,
        "documents": documents,
        "chunks": chunks,
        "entities": entities,
        "relationships": relationships,
        "claims": claims,
    }


def export_json(db: Database, out: Path, *, source_id: str | None = None) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    data = collect(db, source_id)
    out.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return out

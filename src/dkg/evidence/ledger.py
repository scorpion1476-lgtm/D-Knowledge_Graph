"""Shared evidence ledger used by agents and the CLI.

Built on top of citations and provenance. A ledger entry is an ``answer packet``
that ties an answer, one or more supporting claims, their citations, and their
provenance envelopes together in one JSON-serialisable structure.
"""

from __future__ import annotations

from typing import Any

from ..core.db import Database
from ..core.provenance import fetch_provenance


def claim_evidence(db: Database, claim_id: str) -> dict[str, Any]:
    claim = db.fetchone(
        """
        SELECT claim_id, chunk_id, subject_id, predicate, object_text, confidence, extractor
        FROM claims WHERE claim_id = ?;
        """,
        (claim_id,),
    )
    if claim is None:
        return {"claim": None, "citations": [], "chunk": None, "provenance": []}

    chunk = db.fetchone(
        "SELECT chunk_id, document_id, text, start_offset, end_offset FROM chunks WHERE chunk_id = ?;",
        (claim["chunk_id"],),
    )
    citations = [
        dict(r)
        for r in db.fetchall(
            "SELECT citation_id, target_kind, target_id, chunk_id, locator_json "
            "FROM citations WHERE target_kind='claim' AND target_id=?;",
            (claim_id,),
        )
    ]
    provenance = []
    if chunk is not None:
        provenance = fetch_provenance(db, "document", chunk["document_id"])
    return {
        "claim": dict(claim),
        "chunk": dict(chunk) if chunk else None,
        "citations": citations,
        "provenance": provenance,
    }


def answer_packet(db: Database, query: str, chunk_ids: list[str]) -> dict[str, Any]:
    if not chunk_ids:
        return {"query": query, "citations": [], "chunks": []}
    placeholders = ",".join(["?"] * len(chunk_ids))
    chunks = [
        dict(r)
        for r in db.fetchall(
            f"SELECT chunk_id, document_id, text FROM chunks WHERE chunk_id IN ({placeholders});",
            chunk_ids,
        )
    ]
    citations = []
    for cid in chunk_ids:
        for r in db.fetchall(
            "SELECT citation_id, target_kind, target_id, chunk_id FROM citations WHERE chunk_id = ?;",
            (cid,),
        ):
            citations.append(dict(r))
    return {
        "query": query,
        "chunks": chunks,
        "citations": citations,
    }

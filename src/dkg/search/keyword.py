"""Keyword search using LIKE with parameter bindings and simple ranking."""

from __future__ import annotations

import re
from typing import Any

from ..core.db import Database

_TOKEN = re.compile(r"[A-Za-z0-9]{2,}")


def keyword_search(
    db: Database,
    query: str,
    *,
    limit: int = 10,
    source_id: str | None = None,
    entity_id: str | None = None,
) -> list[dict[str, Any]]:
    tokens = _TOKEN.findall(query.lower())[:8]
    if not tokens:
        return []
    where_parts = []
    params: list[Any] = []
    for tok in tokens:
        where_parts.append("LOWER(c.text) LIKE ?")
        params.append(f"%{tok}%")
    sql = (
        "SELECT c.chunk_id AS chunk_id, c.document_id AS document_id, "
        "d.source_id AS source_id, substr(c.text, 1, 240) AS snippet "
        "FROM chunks c JOIN documents d ON d.document_id = c.document_id "
        f"WHERE {' AND '.join(where_parts)}"
    )
    if source_id is not None:
        sql += " AND d.source_id = ?"
        params.append(source_id)
    if entity_id is not None:
        sql += " AND c.chunk_id IN (SELECT chunk_id FROM mentions WHERE entity_id = ?)"
        params.append(entity_id)
    sql += " LIMIT ?"
    params.append(int(limit) * 3)  # over-fetch so we can score
    rows = db.fetchall(sql, params)

    results = []
    for r in rows:
        # ``substr(c.text, 1, 240)`` can occasionally return NULL when the
        # underlying chunk row is still being written by a concurrent
        # ingest. Treat missing snippets as empty rather than raising.
        snippet_raw = r["snippet"] or ""
        text_lower = snippet_raw.lower()
        score = sum(1 for tok in tokens if tok in text_lower) / max(1, len(tokens))
        results.append(
            {
                "chunk_id": r["chunk_id"],
                "document_id": r["document_id"],
                "source_id": r["source_id"],
                "snippet": snippet_raw,
                "score": score,
                "why": {"matched_tokens": [t for t in tokens if t in text_lower]},
            }
        )
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:limit]


def facet_by_source(db: Database) -> list[dict[str, Any]]:
    rows = db.fetchall(
        """
        SELECT d.source_id AS source_id, s.display_name AS name,
               COUNT(c.chunk_id) AS chunks
        FROM chunks c
        JOIN documents d ON d.document_id = c.document_id
        JOIN sources s ON s.source_id = d.source_id
        GROUP BY d.source_id, s.display_name
        ORDER BY chunks DESC;
        """
    )
    return [dict(r) for r in rows]

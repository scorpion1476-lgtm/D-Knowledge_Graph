"""FTS5-backed search over chunks."""

from __future__ import annotations

import re

from ..core.db import Database

_TOKEN = re.compile(r"[A-Za-z0-9]{2,}")


def _to_match_expr(query: str) -> str | None:
    tokens = _TOKEN.findall(query)
    if not tokens:
        return None
    # Use OR to avoid accidental strict conjunctions on tiny corpora.
    return " OR ".join(tokens[:12])


def fts_search(db: Database, query: str, *, limit: int = 10) -> list[dict]:
    expr = _to_match_expr(query)
    if expr is None:
        return []
    rows = db.fetchall(
        """
        SELECT c.chunk_id AS chunk_id, c.document_id AS document_id,
               d.source_id AS source_id, snippet(chunks_fts, 0, '[', ']', '...', 12) AS snippet,
               bm25(chunks_fts) AS raw_score
        FROM chunks_fts
        JOIN chunks c ON c.rowid = chunks_fts.rowid
        JOIN documents d ON d.document_id = c.document_id
        WHERE chunks_fts MATCH ?
        ORDER BY bm25(chunks_fts)
        LIMIT ?;
        """,
        (expr, int(limit)),
    )
    results = []
    for r in rows:
        # bm25 is lower-is-better; convert to 0..1 with 1/(1+bm25).
        # An entry can transiently return NULL bm25 when the FTS index
        # references a chunk whose row is mid-commit under WAL; treat
        # missing scores as a neutral 0.5.
        raw_val = r["raw_score"]
        if raw_val is None:
            raw = 0.0
            score = 0.5
        else:
            raw = float(raw_val)
            score = 1.0 / (1.0 + max(0.0, raw))
        results.append(
            {
                "chunk_id": r["chunk_id"],
                "document_id": r["document_id"],
                "source_id": r["source_id"],
                "snippet": r["snippet"] or "",
                "score": score,
                "why": {"engine": "fts5", "match_expr": expr, "bm25": raw},
            }
        )
    return results

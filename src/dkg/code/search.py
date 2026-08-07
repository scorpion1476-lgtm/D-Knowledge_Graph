"""Code search over the shared store: code entities by name and code chunk text.

Reuses the shared tables; it scopes results to code entities and code documents
so a code query does not return document-plane chunks.
"""

from __future__ import annotations

from ..core.db import Database


def code_search(db: Database, query: str, *, limit: int = 10, tenant_id: str = "local") -> dict:
    q = f"%{query.lower()}%"
    lim = max(1, min(int(limit), 100))
    symbols = [
        {"kind": r["kind"], "canonical": r["canonical"], "display": r["display"]}
        for r in db.fetchall(
            """
            SELECT kind, canonical, display FROM entities
            WHERE tenant_id=? AND kind LIKE 'code:%'
              AND (LOWER(display) LIKE ? OR LOWER(canonical) LIKE ?)
            LIMIT ?;
            """,
            (tenant_id, q, q, lim),
        )
    ]
    chunks = [
        {"path": r["path"], "text": r["text"][:400]}
        for r in db.fetchall(
            """
            SELECT c.text AS text, json_extract(d.metadata_json,'$.path') AS path
            FROM chunks c JOIN documents d ON d.document_id = c.document_id
            WHERE c.tenant_id=? AND d.format LIKE 'code:%' AND LOWER(c.text) LIKE ?
            LIMIT ?;
            """,
            (tenant_id, q, lim),
        )
    ]
    return {"symbols": symbols, "chunks": chunks}

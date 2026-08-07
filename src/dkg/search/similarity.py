"""Similarity search over chunks using the built-in embedding adapter."""

from __future__ import annotations

from ..adapters.embedding import EmbeddingAdapter, HashingEmbeddingAdapter, cosine
from ..core.db import Database


def similarity_search(
    db: Database,
    query: str,
    *,
    limit: int = 10,
    tenant_id: str = "local",
    adapter: EmbeddingAdapter | None = None,
) -> list[dict]:
    """Return chunks ranked by cosine similarity to the query.

    Uses the hashing embedding adapter by default. No model download.
    """
    if not query or not query.strip():
        return []
    ad = adapter or HashingEmbeddingAdapter(dimension=256)
    q_vec = ad.embed([query])[0]
    rows = db.fetchall(
        "SELECT chunk_id, document_id, text FROM chunks WHERE tenant_id=?;",
        (tenant_id,),
    )
    if not rows:
        return []
    texts = [r["text"] for r in rows]
    vectors = ad.embed(texts)
    scored = []
    for r, vec in zip(rows, vectors, strict=False):
        c = cosine(q_vec, vec)
        scored.append(
            {
                "chunk_id": r["chunk_id"],
                "document_id": r["document_id"],
                "snippet": (r["text"] or "")[:240],
                "score": c,
                "why": {"engine": "hashing-embedding-cosine", "dimension": ad.dimension},
            }
        )
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]

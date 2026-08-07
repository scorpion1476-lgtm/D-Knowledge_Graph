"""Semantic similarity dedupe backed by the built-in embedding adapter.

Uses ``dkg.adapters.embedding.HashingEmbeddingAdapter`` by default, which
requires no external model. Callers can pass any other implementation of
``EmbeddingAdapter`` for higher quality.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..adapters.embedding import EmbeddingAdapter, HashingEmbeddingAdapter, cosine
from ..core.db import Database


@dataclass
class SimilarPair:
    left_chunk_id: str
    right_chunk_id: str
    cosine: float


def find_near_duplicates(
    db: Database,
    *,
    tenant_id: str = "local",
    threshold: float = 0.9,
    limit: int = 500,
    adapter: EmbeddingAdapter | None = None,
) -> list[SimilarPair]:
    """Return chunks whose cosine similarity exceeds ``threshold``.

    Deterministic. Uses the hashing embedding adapter by default so no
    model download is required.
    """
    ad = adapter or HashingEmbeddingAdapter(dimension=256)
    rows = db.fetchall(
        "SELECT chunk_id, text FROM chunks WHERE tenant_id=? LIMIT ?;",
        (tenant_id, int(limit)),
    )
    if len(rows) < 2:
        return []
    texts = [r["text"] for r in rows]
    vectors = ad.embed(texts)
    ids = [r["chunk_id"] for r in rows]
    out: list[SimilarPair] = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            c = cosine(vectors[i], vectors[j])
            if c >= threshold:
                out.append(SimilarPair(ids[i], ids[j], c))
    out.sort(key=lambda p: p.cosine, reverse=True)
    return out

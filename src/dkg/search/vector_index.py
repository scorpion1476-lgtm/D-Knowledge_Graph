"""Persisted vector index over chunks for real local embedding search.

Vectors are stored in ``chunk_embeddings`` keyed by (chunk_id, model_tag) so two
backends' vectors can never be mixed. The tag combines three things that change
what a vector means: the adapter name (which for the remote backend carries a
digest of its endpoint and model), the dimension, and the text recipe the vector
was built from. Change any of them and the query stops reading the old rows, so a
backend switch or a recipe change needs a re-index rather than silently blending
two populations. ``reindex`` (re)populates the store; ``vector_search`` reads it.

The identifier-enriched recipe prefixes each chunk with its entity's dotted
qualified form, its word-split identifier, and its enclosing directory before
embedding, so a differently-cased or module-level query can reach a symbol whose
body never spells the query's form. It is the default; ``enrich=False`` keeps the
raw-text recipe, which is what the before-and-after measurement compares against.

Serialization uses the stdlib ``array`` module (32-bit floats), so the store has
no numpy dependency of its own; only the real embedding adapter needs numpy.
"""

from __future__ import annotations

from array import array

from ..adapters.embedding import EmbeddingAdapter, cosine
from ..core.db import Database
from .identifiers import chunk_identifier_context, enrich_embedding_text

# The two text recipes a vector can be built from. They are part of the store key
# because a vector built from enriched text is not comparable with one built from
# the raw chunk.
RECIPE_ENRICHED = "id1"
RECIPE_RAW = "raw"


def model_tag(adapter: EmbeddingAdapter, *, enrich: bool = True) -> str:
    """Stable identity for the vectors an adapter produces.

    Adapter name, dimension, and text recipe. Two backends, two endpoints behind
    the remote backend, or two recipes each get a distinct tag.
    """
    recipe = RECIPE_ENRICHED if enrich else RECIPE_RAW
    return f"{adapter.name}-{adapter.dimension}-{recipe}"


def _serialize(vector: list[float]) -> bytes:
    return array("f", vector).tobytes()


def _deserialize(blob: bytes) -> list[float]:
    a = array("f")
    a.frombytes(blob)
    return list(a)


def stored_count(db: Database, *, tag: str, tenant_id: str = "local") -> int:
    row = db.fetchone(
        "SELECT COUNT(*) AS n FROM chunk_embeddings WHERE model=? AND tenant_id=?;",
        (tag, tenant_id),
    )
    return int(row["n"]) if row else 0


def embedding_text(
    chunk_id: str, text: str, context: dict[str, dict[str, str]], *, enrich: bool
) -> str:
    """The exact text a chunk is embedded from, under the active recipe."""
    if not enrich:
        return text or ""
    info = context.get(chunk_id)
    if not info:
        return text or ""
    return enrich_embedding_text(
        text or "", qualified=info.get("qualified") or None, path=info.get("path") or None
    )


def reindex(
    db: Database,
    *,
    adapter: EmbeddingAdapter,
    tenant_id: str = "local",
    batch_size: int = 128,
    enrich: bool = True,
) -> dict:
    """Embed every chunk for the tenant with ``adapter`` and upsert the vectors.

    Returns a summary with the model tag, dimension, the number of vectors
    written, and how many of them were identifier-enriched. Safe to re-run: it
    replaces existing vectors for the same tag, and vectors under any other tag
    are left untouched.
    """
    ok, why = adapter.available()
    if not ok:
        raise RuntimeError(f"cannot reindex: embedding adapter unavailable: {why}")
    tag = model_tag(adapter, enrich=enrich)
    dim = adapter.dimension
    rows = db.fetchall(
        "SELECT chunk_id, text FROM chunks WHERE tenant_id=? ORDER BY chunk_id;",
        (tenant_id,),
    )
    context = chunk_identifier_context(db, tenant_id=tenant_id) if enrich else {}
    # Clear stale vectors for this tag so a re-index reflects the current corpus.
    db.execute("DELETE FROM chunk_embeddings WHERE model=? AND tenant_id=?;", (tag, tenant_id))
    written = 0
    enriched = 0
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        texts = []
        for r in batch:
            raw = r["text"] or ""
            prepared = embedding_text(r["chunk_id"], raw, context, enrich=enrich)
            if prepared != raw:
                enriched += 1
            texts.append(prepared)
        vectors = adapter.embed(texts)
        params = [
            (r["chunk_id"], tenant_id, tag, dim, _serialize(vec))
            for r, vec in zip(batch, vectors, strict=False)
        ]
        db.executemany(
            "INSERT OR REPLACE INTO chunk_embeddings(chunk_id, tenant_id, model, dim, vector) "
            "VALUES (?, ?, ?, ?, ?);",
            params,
        )
        written += len(params)
    return {
        "model": tag,
        "dimension": dim,
        "vectors": written,
        "enriched": enriched,
        "recipe": RECIPE_ENRICHED if enrich else RECIPE_RAW,
    }


def vector_search(
    db: Database,
    query: str,
    *,
    adapter: EmbeddingAdapter,
    tenant_id: str = "local",
    limit: int = 10,
    auto_index: bool = True,
    enrich: bool = True,
) -> list[dict]:
    """Return chunks ranked by cosine similarity using persisted vectors.

    When no vectors are stored for the active model and recipe and ``auto_index``
    is set, the store is populated first (a one-time cost that mirrors the
    documented re-index). Returns an empty list when the adapter is unavailable so
    the caller can degrade to keyword search with an honest reason.
    """
    if not query or not query.strip():
        return []
    ok, _ = adapter.available()
    if not ok:
        return []
    tag = model_tag(adapter, enrich=enrich)
    if auto_index and stored_count(db, tag=tag, tenant_id=tenant_id) == 0:
        reindex(db, adapter=adapter, tenant_id=tenant_id, enrich=enrich)
    q_vec = adapter.embed([query])[0]
    rows = db.fetchall(
        "SELECT e.chunk_id AS chunk_id, c.document_id AS document_id, c.text AS text, e.vector AS vector "
        "FROM chunk_embeddings e JOIN chunks c ON c.chunk_id = e.chunk_id "
        "WHERE e.model=? AND e.tenant_id=?;",
        (tag, tenant_id),
    )
    scored: list[dict] = []
    for r in rows:
        vec = _deserialize(r["vector"])
        scored.append(
            {
                "chunk_id": r["chunk_id"],
                "document_id": r["document_id"],
                "snippet": (r["text"] or "")[:240],
                "text": r["text"] or "",
                "score": cosine(q_vec, vec),
                "why": {"engine": "embedding-cosine", "model": tag},
            }
        )
    # Explicit sort key with the canonical id breaking ties, so the same store
    # and the same query always produce byte-identical output.
    scored.sort(key=lambda x: (-x["score"], x["chunk_id"]))
    return scored[:limit]

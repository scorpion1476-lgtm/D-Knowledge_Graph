"""Real local embedding adapter and persisted vector index.

These tests exercise the real model2vec adapter when it is installed and
pre-staged, and skip with an honest reason otherwise. The hashing fallback is
always exercised so the degraded path stays covered.
"""

from __future__ import annotations

import pytest

from dkg.adapters.embedding import (
    HashingEmbeddingAdapter,
    Model2VecEmbeddingAdapter,
    cosine,
    default_embedding_adapter,
)
from dkg.ingest.base import ingest_text
from dkg.search.vector_index import model_tag, reindex, stored_count, vector_search

_REAL_OK, _REAL_WHY = Model2VecEmbeddingAdapter().available()
requires_real = pytest.mark.skipif(not _REAL_OK, reason=f"real embedding model unavailable: {_REAL_WHY}")


def test_hashing_fallback_is_available_and_deterministic():
    a = HashingEmbeddingAdapter(dimension=64)
    assert a.available()[0]
    v1 = a.embed(["hello world"])
    v2 = a.embed(["hello world"])
    assert len(v1[0]) == 64
    assert v1 == v2  # deterministic


@requires_real
def test_model2vec_captures_semantic_similarity():
    a = Model2VecEmbeddingAdapter()
    q, rel, irrel = a.embed(
        ["database query planner", "tuning the sql query planner", "a cat sat on a mat"]
    )
    assert cosine(q, rel) > cosine(q, irrel)


@requires_real
def test_selector_prefers_real_model_when_available():
    assert default_embedding_adapter().name == "model2vec"


@requires_real
def test_reindex_then_vector_search(db):
    ingest_text(db, "The query planner optimizes SQL execution plans.", display_name="d1")
    ingest_text(db, "Photosynthesis converts sunlight into chemical energy.", display_name="d2")
    adapter = Model2VecEmbeddingAdapter()
    summary = reindex(db, adapter=adapter)
    assert summary["vectors"] >= 2
    assert summary["dimension"] == adapter.dimension
    assert stored_count(db, tag=model_tag(adapter)) == summary["vectors"]
    res = vector_search(db, "sql execution plan tuning", adapter=adapter, limit=1)
    assert res
    assert "planner" in res[0]["text"].lower()


@requires_real
def test_vectors_are_keyed_by_model_so_backends_never_mix(db):
    ingest_text(db, "alpha beta gamma delta", display_name="d1")
    real = Model2VecEmbeddingAdapter()
    stub = HashingEmbeddingAdapter(dimension=256)
    reindex(db, adapter=real)
    # The real model's rows must not be visible under a different backend's tag.
    assert stored_count(db, tag=model_tag(real)) >= 1
    assert stored_count(db, tag=model_tag(stub)) == 0
    assert model_tag(real) != model_tag(stub)


def test_text_recipe_is_part_of_the_store_key(db):
    """A raw-text vector and an enriched vector are never the same store row.

    The recipe is in the tag, so switching it needs a re-index in exactly the
    way switching the backend does, and the two populations cannot blend.
    """
    ingest_text(db, "alpha beta gamma delta", display_name="d1")
    stub = HashingEmbeddingAdapter(dimension=256)
    enriched_tag = model_tag(stub, enrich=True)
    raw_tag = model_tag(stub, enrich=False)
    assert enriched_tag != raw_tag
    reindex(db, adapter=stub, enrich=False)
    assert stored_count(db, tag=raw_tag) >= 1
    assert stored_count(db, tag=enriched_tag) == 0
    reindex(db, adapter=stub, enrich=True)
    assert stored_count(db, tag=enriched_tag) >= 1
    # Re-indexing under one tag must not delete the other population.
    assert stored_count(db, tag=raw_tag) >= 1


@requires_real
def test_reindex_is_idempotent(db):
    ingest_text(db, "one two three four five", display_name="d1")
    adapter = Model2VecEmbeddingAdapter()
    a = reindex(db, adapter=adapter)
    b = reindex(db, adapter=adapter)
    assert a["vectors"] == b["vectors"]
    assert stored_count(db, tag=model_tag(adapter)) == a["vectors"]


def test_free_text_without_a_path_is_not_enriched(db):
    """Document-plane prose has no qualified name, so its text is left alone.

    This is why the identifier work does not move the retained retrieval corpus:
    there is nothing to enrich it with.
    """
    ingest_text(db, "one two three four five", display_name="d1")
    stub = HashingEmbeddingAdapter(dimension=256)
    summary = reindex(db, adapter=stub, enrich=True)
    assert summary["vectors"] >= 1
    assert summary["enriched"] == 0

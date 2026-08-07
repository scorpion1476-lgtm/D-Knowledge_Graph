"""End-to-end tests for similarity search over ingested chunks.

Covers C-07 (similarity search via local vector adapter).
"""

from __future__ import annotations

from dkg.ingest.base import ingest_text
from dkg.search.similarity import similarity_search


def test_similarity_search_returns_ranked_chunks(db):
    ingest_text(db, "Alice studies protein folding at length.", display_name="a")
    ingest_text(db, "Bob writes Go microservices at scale.", display_name="b")
    ingest_text(db, "Charlie prefers coffee to tea in the morning.", display_name="c")

    results = similarity_search(db, "protein folding", limit=3)
    assert results, "similarity search must return at least one chunk"
    # The chunk about "protein folding" must sort at or near the top.
    top = results[0]
    assert "protein" in top["snippet"].lower() or "folding" in top["snippet"].lower()
    # Score must be a float between -1 and 1.
    assert isinstance(top["score"], float)
    assert -1.0 <= top["score"] <= 1.0


def test_similarity_search_empty_query_returns_empty(db):
    ingest_text(db, "Some body text.", display_name="d")
    assert similarity_search(db, "") == []
    assert similarity_search(db, "   ") == []


def test_similarity_search_missing_tenant_returns_empty(db):
    ingest_text(db, "Some body text.", display_name="d")
    assert similarity_search(db, "body", tenant_id="tenant_does_not_exist") == []


def test_similarity_search_limit_is_respected(db):
    for i in range(5):
        ingest_text(db, f"document number {i} with unique words", display_name=f"d{i}")
    results = similarity_search(db, "document number", limit=2)
    assert len(results) <= 2


def test_similarity_search_explains_engine(db):
    ingest_text(db, "text about hashing embeddings", display_name="e")
    results = similarity_search(db, "hashing")
    assert results
    assert results[0]["why"]["engine"] == "hashing-embedding-cosine"

"""Cross-encoder reranker and the reranked hybrid path.

The reranker tests skip with an honest reason when the 'reranker' extra or model
is absent. The pure-RRF fallback shape is asserted unconditionally.
"""

from __future__ import annotations

import pytest

from dkg.adapters.reranker import CrossEncoderReranker
from dkg.ingest.base import ingest_text
from dkg.search.hybrid import hybrid_search

_RR_OK, _RR_WHY = CrossEncoderReranker().available()
requires_reranker = pytest.mark.skipif(not _RR_OK, reason=f"reranker unavailable: {_RR_WHY}")


def test_hybrid_without_optional_arms_is_pure_rrf(db):
    ingest_text(db, "Alpha beta. Beta is fast and reliable.", display_name="d1")
    ingest_text(db, "Gamma is slow but accurate.", display_name="d2")
    res = hybrid_search(db, "beta fast", limit=5, use_vector=False, use_reranker=False)
    assert res
    for r in res:
        assert set(r["why"]["engines"]).issubset({"keyword", "fts"})
        assert r["why"].get("reranked") is False


@requires_reranker
def test_reranker_scores_relevant_above_irrelevant():
    rr = CrossEncoderReranker()
    scores = rr.rerank(
        "how to tune a query planner",
        ["tuning the sql query planner for speed", "a cat sat on a mat"],
    )
    assert len(scores) == 2
    assert scores[0] > scores[1]


@requires_reranker
def test_reranked_hybrid_marks_provenance_and_orders(db):
    ingest_text(db, "The query planner optimizes SQL execution plans for speed.", display_name="d1")
    ingest_text(db, "Cats are small domesticated mammals kept as pets.", display_name="d2")
    res = hybrid_search(db, "sql query planner tuning", limit=5)
    assert res
    assert res[0]["why"].get("reranked") is True
    assert "rerank_score" in res[0]
    assert "planner" in (res[0].get("text") or res[0].get("snippet") or "").lower()

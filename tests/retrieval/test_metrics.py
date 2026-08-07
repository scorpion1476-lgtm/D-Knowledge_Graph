"""Unit tests for the retrieval-quality metrics (always runnable, no models)."""

from __future__ import annotations

from dkg.search.metrics import mean, ndcg_at_k, recall_at_k, reciprocal_rank


def test_reciprocal_rank():
    assert reciprocal_rank(["a", "b", "c"], {"b"}) == 0.5
    assert reciprocal_rank(["a", "b", "c"], {"a"}) == 1.0
    assert reciprocal_rank(["a", "b", "c"], {"z"}) == 0.0


def test_recall_at_k():
    assert recall_at_k(["a", "b", "c"], {"a", "c"}, 3) == 1.0
    assert recall_at_k(["a", "b", "c"], {"a", "z"}, 3) == 0.5
    assert recall_at_k(["a", "b", "c"], set(), 3) == 0.0


def test_ndcg_at_k_perfect_and_ordering():
    # A relevant item at rank 1 is the ideal ranking: nDCG == 1.0.
    assert ndcg_at_k(["a", "b", "c"], {"a"}, 3) == 1.0
    # The same relevant item lower down scores strictly less than at the top.
    top = ndcg_at_k(["a", "b", "c"], {"a"}, 3)
    lower = ndcg_at_k(["b", "c", "a"], {"a"}, 3)
    assert lower < top


def test_mean():
    assert mean([1.0, 0.0]) == 0.5
    assert mean([]) == 0.0

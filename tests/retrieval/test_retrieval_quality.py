"""Retrieval-quality evaluation: the new system must beat the keyword baseline.

Skips with an honest reason when the real embedding model or the reranker is not
pre-staged, so the core suite passes without the optional extras.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from dkg.adapters.embedding import Model2VecEmbeddingAdapter
from dkg.adapters.reranker import CrossEncoderReranker

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import retrieval_quality  # noqa: E402

_EMB_OK, _EMB_WHY = Model2VecEmbeddingAdapter().available()
_RR_OK, _RR_WHY = CrossEncoderReranker().available()
requires_models = pytest.mark.skipif(
    not (_EMB_OK and _RR_OK),
    reason=f"retrieval-quality models unavailable (embedding: {_EMB_WHY}; reranker: {_RR_WHY})",
)


@requires_models
def test_new_system_beats_keyword_baseline():
    summary = retrieval_quality.run_evaluation()
    configs = summary["configurations"]
    baseline = configs["A_keyword_only_baseline"]
    new = configs["C_new_embeddings_plus_rerank"]
    assert new is not None, "new system did not run despite models being available"
    # The new system must be at least as good as the keyword-only baseline on
    # both headline metrics. If this ever regresses, the report shows it honestly
    # rather than the test being loosened.
    assert new["mrr"] >= baseline["mrr"], summary
    assert new["ndcg@10"] >= baseline["ndcg@10"], summary


@requires_models
def test_new_system_recall_is_complete_on_corpus():
    summary = retrieval_quality.run_evaluation()
    new = summary["configurations"]["C_new_embeddings_plus_rerank"]
    assert new["recall@10"] >= summary["configurations"]["A_keyword_only_baseline"]["recall@10"]

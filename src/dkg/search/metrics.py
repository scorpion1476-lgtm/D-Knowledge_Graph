"""Retrieval-quality metrics (deterministic, no dependencies).

Binary-relevance mean reciprocal rank, normalized discounted cumulative gain,
and recall at k. Inputs are a ranked list of item ids (deduplicated, best rank
first) and a set of relevant ids. These are used by the retrieval-quality harness
to publish measured before-and-after numbers.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence


def reciprocal_rank(ranked: Sequence[str], relevant: set[str]) -> float:
    for i, rid in enumerate(ranked, start=1):
        if rid in relevant:
            return 1.0 / i
    return 0.0


def recall_at_k(ranked: Sequence[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    topk = set(ranked[:k])
    hit = sum(1 for r in relevant if r in topk)
    return hit / len(relevant)


def dcg_at_k(ranked: Sequence[str], relevant: set[str], k: int) -> float:
    total = 0.0
    for i, rid in enumerate(ranked[:k], start=1):
        if rid in relevant:
            total += 1.0 / math.log2(i + 1)
    return total


def ndcg_at_k(ranked: Sequence[str], relevant: set[str], k: int) -> float:
    ideal_hits = min(len(relevant), k)
    if ideal_hits == 0:
        return 0.0
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_hits + 1))
    return dcg_at_k(ranked, relevant, k) / idcg


def mean(values: Iterable[float]) -> float:
    vals = list(values)
    return sum(vals) / len(vals) if vals else 0.0

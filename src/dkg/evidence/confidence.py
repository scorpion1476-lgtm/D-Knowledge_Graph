"""Confidence scoring.

The formula is intentionally simple and explainable:

    confidence(c) = clip(
        0.4 * source_quality
      + 0.3 * corroboration_penalty(n_supporting)
      + 0.2 * (1 - contradiction_penalty(n_contradicting))
      + 0.1 * recency_bonus(days_since_ingest),
      0.0, 1.0
    )

The individual weights, the exact functions, and the input signals are all
returned in the ``explain`` field so a caller can reproduce the score.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class ConfidenceInputs:
    source_quality: float  # 0..1
    n_supporting: int
    n_contradicting: int
    days_since_ingest: int


@dataclass
class ConfidenceResult:
    score: float
    explain: dict


def score_confidence(inp: ConfidenceInputs) -> ConfidenceResult:
    corroboration = 1.0 - math.exp(-max(0, inp.n_supporting) / 3.0)  # asymptotic to 1
    contradiction = 1.0 - math.exp(-max(0, inp.n_contradicting) / 3.0)
    recency = math.exp(-max(0, inp.days_since_ingest) / 365.0)
    raw = (
        0.4 * _clip(inp.source_quality)
        + 0.3 * corroboration
        + 0.2 * (1.0 - contradiction)
        + 0.1 * recency
    )
    score = _clip(raw)
    return ConfidenceResult(
        score=score,
        explain={
            "source_quality": _clip(inp.source_quality),
            "corroboration": corroboration,
            "contradiction": contradiction,
            "recency": recency,
            "weights": {"source": 0.4, "corroboration": 0.3, "contradiction": 0.2, "recency": 0.1},
            "raw": raw,
        },
    )


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(x)))

"""Derive deterministic relationships between entities that co-occur in a chunk."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations


@dataclass
class DerivedRelation:
    subject_id: str
    object_id: str
    predicate: str
    weight: float


def derive_cooccurrence_relations(entity_ids: list[str]) -> list[DerivedRelation]:
    """Create bounded symmetric co-occurrence edges within one chunk."""
    unique = sorted(set(entity_ids))
    if len(unique) < 2 or len(unique) > 32:
        # Guard against combinatorial explosion for dense chunks.
        return []
    out: list[DerivedRelation] = []
    for a, b in combinations(unique, 2):
        out.append(
            DerivedRelation(
                subject_id=a,
                object_id=b,
                predicate="co_occurs_with",
                weight=1.0,
            )
        )
    return out

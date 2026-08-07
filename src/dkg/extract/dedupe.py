"""Deduplication helpers.

Content-hash dedupe is enforced at ingestion time. This module additionally
provides a lightweight similarity function for post-ingest dedupe using
character-level shingles. A vector-based similarity path can be added later
behind the embedding adapter.
"""

from __future__ import annotations

import re
from collections import Counter

_WORD = re.compile(r"[A-Za-z0-9]+")


def token_set(text: str) -> set[str]:
    return set(w.lower() for w in _WORD.findall(text))


def jaccard(a: str, b: str) -> float:
    ta = token_set(a)
    tb = token_set(b)
    if not ta or not tb:
        return 0.0
    inter = ta & tb
    union = ta | tb
    return len(inter) / len(union)


def shingles(text: str, n: int = 5) -> Counter[str]:
    text = text.lower()
    tokens = _WORD.findall(text)
    grams: Counter[str] = Counter()
    if len(tokens) < n:
        if tokens:
            grams[" ".join(tokens)] += 1
        return grams
    for i in range(len(tokens) - n + 1):
        grams[" ".join(tokens[i : i + n])] += 1
    return grams


def cosine_shingles(a: str, b: str, n: int = 5) -> float:
    ga = shingles(a, n)
    gb = shingles(b, n)
    if not ga or not gb:
        return 0.0
    dot = sum(ga[k] * gb.get(k, 0) for k in ga)
    na = sum(v * v for v in ga.values()) ** 0.5
    nb = sum(v * v for v in gb.values()) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)

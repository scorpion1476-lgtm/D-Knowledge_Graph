"""Deterministic entity resolver.

Collapses trivial name variants of the same underlying entity:
- case differences
- surrounding punctuation
- common organisation suffix pairs (Corp / Corporation, Ltd / Limited,
  Inc / Incorporated, Co / Company)
- stripped trailing periods
- collapsed internal whitespace

Not a full resolver; intended as a defensible offline baseline. Callers
that want higher recall can plug in an LLM adapter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SUFFIX_ALIASES = {
    "corp": "corporation",
    "co": "company",
    "inc": "incorporated",
    "ltd": "limited",
    "labs": "laboratories",
}

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[\.,;:!?]+$")


def canonicalise(name: str) -> str:
    """Return a canonical, comparison-safe form of a name.

    Deterministic and independent of tokenisation choices.
    """
    if not isinstance(name, str):
        raise TypeError("name must be a string")
    s = _PUNCT.sub("", name).strip().lower()
    s = _WS.sub(" ", s)
    parts = s.split(" ")
    if parts:
        last = parts[-1]
        if last in _SUFFIX_ALIASES:
            parts[-1] = _SUFFIX_ALIASES[last]
    return " ".join(parts)


@dataclass
class ResolvedGroup:
    canonical: str
    members: list[str]


def resolve_names(names: list[str]) -> list[ResolvedGroup]:
    groups: dict[str, list[str]] = {}
    for n in names:
        c = canonicalise(n)
        groups.setdefault(c, []).append(n)
    return [ResolvedGroup(canonical=k, members=sorted(set(v))) for k, v in sorted(groups.items())]

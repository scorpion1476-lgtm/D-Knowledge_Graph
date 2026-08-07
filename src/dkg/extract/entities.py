"""Deterministic entity extraction.

Covers people-like Title-Case sequences, organisations (with suffix hints),
URLs, version strings, and file paths. The output is intentionally
conservative; the LLM adapter (when configured) is expected to lift precision
and recall.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ExtractedEntity:
    kind: str
    canonical: str
    display: str
    surface: str
    start: int
    end: int


_URL_RE = re.compile(
    r"\bhttps?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%\-]+",
)
_VERSION_RE = re.compile(r"\bv?\d+\.\d+(?:\.\d+)?(?:[-.][A-Za-z0-9]+)?\b")
_ORG_SUFFIXES = r"(?:Inc\.?|LLC|Ltd\.?|Corp\.?|Corporation|Foundation|Labs|Institute|Company|Co\.)"
_ORG_RE = re.compile(rf"\b([A-Z][A-Za-z0-9&\- ]+ {_ORG_SUFFIXES})")
_PERSON_RE = re.compile(r"\b([A-Z][a-z]{1,20})(?:\s+([A-Z][a-z]{1,20})){1,2}\b")

_STOP_TITLE = frozenset(
    {
        "The", "And", "But", "For", "Or", "Not", "With", "This", "That", "These",
        "Those", "From", "Into", "About", "Also", "Note", "See", "Only",
    }
)


def extract_entities(text: str) -> list[ExtractedEntity]:
    if not text:
        return []
    out: list[ExtractedEntity] = []
    seen: set[tuple[str, str]] = set()

    for m in _URL_RE.finditer(text):
        surf = m.group(0).rstrip(".,);]")
        _add(out, seen, "url", surf.lower(), surf, surf, m.start(), m.start() + len(surf))

    for m in _ORG_RE.finditer(text):
        surf = m.group(0).strip()
        canon = re.sub(r"\s+", " ", surf).rstrip(".").lower()
        _add(out, seen, "organisation", canon, surf, surf, m.start(), m.end())

    for m in _PERSON_RE.finditer(text):
        surf = m.group(0).strip()
        # Filter out leading stopwords like "The Something Other"
        if any(surf.startswith(sw + " ") for sw in _STOP_TITLE):
            continue
        # Reject if any token is a version-looking word
        if any(_VERSION_RE.fullmatch(tok) for tok in surf.split()):
            continue
        canon = surf.lower()
        _add(out, seen, "person", canon, surf, surf, m.start(), m.end())

    for m in _VERSION_RE.finditer(text):
        surf = m.group(0)
        _add(out, seen, "version", surf.lower(), surf, surf, m.start(), m.end())

    return out


def _add(
    out: list[ExtractedEntity],
    seen: set[tuple[str, str]],
    kind: str,
    canon: str,
    display: str,
    surface: str,
    s: int,
    e: int,
) -> None:
    key = (kind, canon)
    if key in seen:
        return
    seen.add(key)
    out.append(
        ExtractedEntity(kind=kind, canonical=canon, display=display, surface=surface, start=s, end=e)
    )

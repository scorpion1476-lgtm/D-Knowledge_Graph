"""Heuristic prompt-injection detection for fetched content.

Text from external sources is *content*, never instruction. This module scores
a text for the likelihood that it is trying to hijack an LLM prompt and
returns a report so callers can decide to strip, wrap, or refuse it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SUSPECT_PATTERNS: list[tuple[str, re.Pattern[str], int]] = [
    ("override_system", re.compile(r"(?i)ignore (?:all\s+)?(?:previous\s+|prior\s+)?(?:the\s+)?(?:rules|instructions|prompts?)"), 3),
    ("act_as_prefix", re.compile(r"(?i)\b(you (?:are|shall be)|act as) (an?|the) (assistant|model|system)"), 2),
    ("jailbreak_marker", re.compile(r"(?i)\b(DAN|do anything now|developer mode)\b"), 3),
    ("system_tag", re.compile(r"(?is)<system>[\s\S]{0,4000}</system>"), 3),
    ("secret_dump", re.compile(r"(?i)(?:print|dump|reveal|show)\s+(?:your|the|all)?\s*(system prompt|instructions|hidden|secret|api[_ ]?key)"), 3),
    ("exfiltrate", re.compile(r"(?i)send (?:all|the) (?:data|files|env|keys?) to https?://"), 3),
    ("tool_hijack", re.compile(r"(?i)call tool\s+[a-z_]+\("), 2),
    ("hidden_directive", re.compile(r"(?is)<!--\s*(?:instruction|prompt|system)\s*:[\s\S]{0,2000}-->"), 3),
]


@dataclass
class PromptInjectionReport:
    score: int
    hits: list[str]
    suspicious: bool

    def to_dict(self) -> dict:
        return {"score": self.score, "hits": self.hits, "suspicious": self.suspicious}


def scan(text: str, *, threshold: int = 3) -> PromptInjectionReport:
    if not isinstance(text, str) or not text:
        return PromptInjectionReport(score=0, hits=[], suspicious=False)
    total = 0
    hits: list[str] = []
    for name, pattern, weight in _SUSPECT_PATTERNS:
        if pattern.search(text):
            total += weight
            hits.append(name)
    return PromptInjectionReport(score=total, hits=hits, suspicious=total >= threshold)


def wrap_untrusted(text: str) -> str:
    """Return text wrapped with an untrusted-content marker.

    The wrapper is intentionally clear so a downstream reviewer can spot when a
    fragment came from outside the trust boundary.
    """
    return (
        "<untrusted-content>\n"
        f"{text}\n"
        "</untrusted-content>"
    )

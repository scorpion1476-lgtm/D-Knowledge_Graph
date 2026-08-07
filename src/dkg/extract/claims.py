"""Deterministic claim extraction.

Looks for simple "X is Y" / "X are Y" / "X reports Y" style sentences and
returns a small list of ``ExtractedClaim`` records. Confidence is fixed and
low (0.4) because the extractor is intentionally shallow.

Input is segmented before sentence splitting. Prose in this project arrives as
markdown far more often than as a single unbroken paragraph, and the sentence
splitter alone cannot see block structure: it splits on terminal punctuation
followed by a capital, so a leading ``# Heading`` line with no terminal
punctuation is glued to the first real sentence of the document and every
pattern, which is anchored at the start of the segment, then fails to match.
That defect silenced extraction for any document whose first block was a
heading. Segmentation strips the block markers markdown uses (headings, list
bullets, block quotes), drops fenced code blocks, joins hard-wrapped lines
inside one block, and hands each block to the sentence splitter separately.
Plain text is unaffected: a document with no markdown markers and no blank
lines is one block, exactly as before.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
_CLAIM_PATTERNS = [
    ("is",       re.compile(r"^(?P<s>[A-Z][A-Za-z0-9 .,'-]{2,80})\s+is\s+(?P<o>[a-zA-Z0-9][^.!?]{2,200})[.!?]?$")),
    ("are",      re.compile(r"^(?P<s>[A-Z][A-Za-z0-9 .,'-]{2,80})\s+are\s+(?P<o>[a-zA-Z0-9][^.!?]{2,200})[.!?]?$")),
    ("was",      re.compile(r"^(?P<s>[A-Z][A-Za-z0-9 .,'-]{2,80})\s+was\s+(?P<o>[a-zA-Z0-9][^.!?]{2,200})[.!?]?$")),
    ("were",     re.compile(r"^(?P<s>[A-Z][A-Za-z0-9 .,'-]{2,80})\s+were\s+(?P<o>[a-zA-Z0-9][^.!?]{2,200})[.!?]?$")),
    ("reports",  re.compile(r"^(?P<s>[A-Z][A-Za-z0-9 .,'-]{2,80})\s+reports?\s+(?P<o>[a-zA-Z0-9][^.!?]{2,200})[.!?]?$")),
    ("provides", re.compile(r"^(?P<s>[A-Z][A-Za-z0-9 .,'-]{2,80})\s+provides?\s+(?P<o>[a-zA-Z0-9][^.!?]{2,200})[.!?]?$")),
    ("supports", re.compile(r"^(?P<s>[A-Z][A-Za-z0-9 .,'-]{2,80})\s+supports?\s+(?P<o>[a-zA-Z0-9][^.!?]{2,200})[.!?]?$")),
    ("requires", re.compile(r"^(?P<s>[A-Z][A-Za-z0-9 .,'-]{2,80})\s+requires?\s+(?P<o>[a-zA-Z0-9][^.!?]{2,200})[.!?]?$")),
    ("has",      re.compile(r"^(?P<s>[A-Z][A-Za-z0-9 .,'-]{2,80})\s+has\s+(?P<o>[a-zA-Z0-9][^.!?]{2,200})[.!?]?$")),
    ("uses",     re.compile(r"^(?P<s>[A-Z][A-Za-z0-9 .,'-]{2,80})\s+uses?\s+(?P<o>[a-zA-Z0-9][^.!?]{2,200})[.!?]?$")),
    # Stative verbs in the same family as "has" and "uses". Policy and design
    # documents state durations and limits with these constantly, and a
    # disagreement stated with one of them produced no claim at all, so the
    # grouping downstream never got the chance to see it.
    ("retains",  re.compile(r"^(?P<s>[A-Z][A-Za-z0-9 .,'-]{2,80})\s+retains?\s+(?P<o>[a-zA-Z0-9][^.!?]{2,200})[.!?]?$")),
    ("stores",   re.compile(r"^(?P<s>[A-Z][A-Za-z0-9 .,'-]{2,80})\s+stores?\s+(?P<o>[a-zA-Z0-9][^.!?]{2,200})[.!?]?$")),
    ("allows",   re.compile(r"^(?P<s>[A-Z][A-Za-z0-9 .,'-]{2,80})\s+allows?\s+(?P<o>[a-zA-Z0-9][^.!?]{2,200})[.!?]?$")),
    ("returns",  re.compile(r"^(?P<s>[A-Z][A-Za-z0-9 .,'-]{2,80})\s+returns?\s+(?P<o>[a-zA-Z0-9][^.!?]{2,200})[.!?]?$")),
    ("count",    re.compile(r"^(?P<s>[A-Z][A-Za-z0-9 .,'-]{2,80})\s+(?:contains?|includes?|has)\s+(?P<o>\d[\d,\.]{0,20}\s+[a-zA-Z][^.!?]{0,120})[.!?]?$")),
]

# Block markers stripped before the sentence splitter sees a line.
_ATX_HEADING = re.compile(r"^ {0,3}#{1,6}\s+")
_SETEXT_RULE = re.compile(r"^ {0,3}(?:=+|-{2,}|\*{3,}|_{3,})\s*$")
_LIST_MARKER = re.compile(r"^ {0,3}(?:[-*+]|\d{1,3}[.)])\s+")
_BLOCKQUOTE = re.compile(r"^ {0,3}>+\s?")
_FENCE = re.compile(r"^ {0,3}(?:```|~~~)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)\s]*\)")
_EMPHASIS = re.compile(r"[*_`]{1,3}")
_WS = re.compile(r"\s+")

# A block longer than this is not prose the shallow patterns can read, and
# joining it would only produce one oversized segment the caller discards.
_MAX_BLOCK_CHARS = 4000


def _clean_inline(line: str) -> str:
    """Remove inline markdown that would otherwise break the prose patterns."""
    out = _LINK.sub(r"\1", line)
    out = _EMPHASIS.sub("", out)
    return out


def _blocks(text: str) -> list[str]:
    """Split text into prose blocks with markdown block markers removed.

    A heading is its own block, so it cannot swallow the sentence that follows
    it. A list item is its own block, so two items cannot be glued into one
    ungrammatical segment. Fenced code is dropped: it is source, and the code
    plane, not the document claim extractor, is responsible for it.
    """
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False

    def flush() -> None:
        if current:
            joined = _WS.sub(" ", " ".join(current)).strip()
            if joined and len(joined) <= _MAX_BLOCK_CHARS:
                blocks.append(joined)
            current.clear()

    for raw in text.splitlines():
        if _FENCE.match(raw):
            flush()
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        line = _BLOCKQUOTE.sub("", raw)
        if not line.strip():
            flush()
            continue
        if _SETEXT_RULE.match(line):
            # A horizontal rule, or the underline of a setext heading. Either
            # way it terminates the block above it and carries no prose.
            flush()
            continue
        heading = _ATX_HEADING.match(line)
        if heading:
            flush()
            current.append(_clean_inline(line[heading.end():]).rstrip(" #"))
            flush()
            continue
        bullet = _LIST_MARKER.match(line)
        if bullet:
            flush()
            current.append(_clean_inline(line[bullet.end():]))
            continue
        current.append(_clean_inline(line))
    flush()
    return blocks


def _segments(text: str) -> list[str]:
    """Every candidate sentence, in document order."""
    out: list[str] = []
    for block in _blocks(text):
        out.extend(_SENT.split(block))
    return out


@dataclass
class ExtractedClaim:
    predicate: str
    subject_hint: str | None
    object_text: str
    confidence: float


def extract_claims(text: str) -> list[ExtractedClaim]:
    if not text:
        return []
    out: list[ExtractedClaim] = []
    seen: set[tuple[str, str, str]] = set()
    for sent in _segments(text):
        s = sent.strip()
        if not s or len(s) > 400:
            continue
        for pred, patt in _CLAIM_PATTERNS:
            m = patt.match(s)
            if not m:
                continue
            subj = (m.group("s") or "").strip(" .,")
            obj = (m.group("o") or "").strip(" .,")
            key = (pred, subj.lower(), obj.lower())
            if key in seen:
                continue
            seen.add(key)
            out.append(
                ExtractedClaim(
                    predicate=pred,
                    subject_hint=subj,
                    object_text=obj,
                    confidence=0.4,
                )
            )
            break
    return out

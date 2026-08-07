"""Deterministic text chunker.

Splits a document into chunks by paragraph, capping each chunk at
``chunk_max_chars``. Every chunk has a stable content-derived ID that persists
across ingests.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_PARA_SPLIT = re.compile(r"\n\s*\n+")
_WS_COMPACT = re.compile(r"[ \t\f\v]+")


@dataclass
class RawChunk:
    ord: int
    text: str
    text_sha256: str
    start_offset: int
    end_offset: int


def chunk_paragraphs(
    text: str,
    *,
    paragraphs_per_chunk: int = 4,
    max_chars: int = 4096,
) -> list[RawChunk]:
    if not text:
        return []
    # Preserve offsets by walking the original text.
    parts = list(_iter_paragraphs(text))
    chunks: list[RawChunk] = []
    ord_i = 0
    buffer: list[tuple[str, int, int]] = []

    def _flush() -> None:
        nonlocal ord_i, buffer
        if not buffer:
            return
        joined = "\n\n".join(p for p, _s, _e in buffer)
        start = buffer[0][1]
        end = buffer[-1][2]
        digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()
        chunks.append(
            RawChunk(
                ord=ord_i,
                text=joined,
                text_sha256=digest,
                start_offset=start,
                end_offset=end,
            )
        )
        ord_i += 1
        buffer = []

    for para, s, e in parts:
        para = _WS_COMPACT.sub(" ", para).strip()
        if not para:
            continue
        if len(para) > max_chars:
            # Split oversized paragraphs by max_chars boundaries.
            for i in range(0, len(para), max_chars):
                slice_ = para[i : i + max_chars]
                _flush()
                digest = hashlib.sha256(slice_.encode("utf-8")).hexdigest()
                chunks.append(
                    RawChunk(
                        ord=ord_i,
                        text=slice_,
                        text_sha256=digest,
                        start_offset=s + i,
                        end_offset=s + i + len(slice_),
                    )
                )
                ord_i += 1
            continue
        buffer.append((para, s, e))
        if len(buffer) >= paragraphs_per_chunk or sum(len(p) for p, _, _ in buffer) >= max_chars:
            _flush()
    _flush()
    return chunks


def _iter_paragraphs(text: str):
    idx = 0
    for m in _PARA_SPLIT.finditer(text):
        yield text[idx:m.start()], idx, m.start()
        idx = m.end()
    yield text[idx:], idx, len(text)

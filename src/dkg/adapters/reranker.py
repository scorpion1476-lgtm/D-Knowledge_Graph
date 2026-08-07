"""Cross-encoder reranker adapter (local, permissive, pre-staged).

Wraps a real cross-encoder (fastembed, Apache-2.0, over ONNX Runtime) that scores
a query against each candidate document jointly, which is materially stronger than
reciprocal-rank fusion or bi-encoder similarity for ordering a candidate set.

The model is pre-staged (see scripts/prestage_models.py) and loaded
local-files-only; loading enforces the Hugging Face offline flag so no network
call is made at runtime. When the 'reranker' extra or the model is absent,
``available`` reports false and the hybrid search keeps its fusion ordering.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_RERANKER_MODEL_ID = "Xenova/ms-marco-MiniLM-L-6-v2"

# Process-wide cache of loaded encoders keyed by (spec, cache_dir).
_ENCODER_CACHE: dict[tuple[str, str | None], Any] = {}


def _default_cache_dir() -> str | None:
    env = os.environ.get("DKG_RERANKER_CACHE")
    if env:
        return env
    staged = _ROOT / "models" / "reranker"
    if staged.exists():
        return str(staged)
    return None


class CrossEncoderReranker:
    name = "cross-encoder"

    def __init__(self, model: str | None = None, cache_dir: str | None = None) -> None:
        self._spec = model or os.environ.get("DKG_RERANKER_MODEL") or DEFAULT_RERANKER_MODEL_ID
        self._cache_dir = cache_dir or _default_cache_dir()
        self._encoder: Any | None = None
        self._error: str | None = None

    def _load(self) -> None:
        if self._encoder is not None or self._error is not None:
            return
        cache_key = (self._spec, self._cache_dir)
        cached = _ENCODER_CACHE.get(cache_key)
        if cached is not None:
            self._encoder = cached
            return
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        try:
            from fastembed.rerank.cross_encoder import TextCrossEncoder
        except Exception as e:
            self._error = f"fastembed not installed: {e!r} (install the 'reranker' extra)"
            return
        try:
            self._encoder = TextCrossEncoder(model_name=self._spec, cache_dir=self._cache_dir)
            _ENCODER_CACHE[cache_key] = self._encoder
        except Exception as e:
            self._encoder = None
            self._error = (
                f"reranker model {self._spec!r} could not be loaded offline: {e!r}; "
                "pre-stage it with scripts/prestage_models.py or set DKG_RERANKER_MODEL"
            )

    def available(self) -> tuple[bool, str]:
        self._load()
        if self._encoder is not None:
            return True, f"cross-encoder {self._spec}"
        return False, self._error or "reranker unavailable"

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        """Return a relevance score for each document, aligned to ``documents``."""
        self._load()
        if self._encoder is None:
            raise RuntimeError(self._error or "reranker unavailable")
        if not documents:
            return []
        return [float(s) for s in self._encoder.rerank(query, list(documents))]


def default_reranker() -> CrossEncoderReranker | None:
    """Return the reranker when available, else None (caller keeps fusion order)."""
    r = CrossEncoderReranker()
    ok, _ = r.available()
    return r if ok else None

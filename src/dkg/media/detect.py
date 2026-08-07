"""Image object and content detection via zero-shot CLIP (fastembed, ONNX).

Original integration: embed the image and a set of candidate label texts with the
matched CLIP vision and text towers, score cosine similarity, and return the top
labels above a margin. Both model weights are MIT; the stack is permissive, local,
pre-staged, and torch-free (onnxruntime), and never AGPL. Capability-detected; it
degrades cleanly with an honest reason when fastembed or the models are absent.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_VISION_MODEL = "Qdrant/clip-ViT-B-32-vision"
DEFAULT_TEXT_MODEL = "Qdrant/clip-ViT-B-32-text"

# A general default label vocabulary for zero-shot content and object detection.
# Callers may pass their own label set; this is only the product default.
DEFAULT_LABELS = (
    "a person", "a group of people", "a dog", "a cat", "a bird", "a horse", "a car",
    "a truck", "a bicycle", "a boat", "an airplane", "a train", "a building", "a house",
    "a road", "a tree", "a flower", "grass", "a mountain", "water", "the sky", "clouds",
    "food", "a plate of food", "a cup of coffee", "a chair", "a table", "a book",
    "a laptop computer", "a mobile phone", "a screenshot of software", "a chart or graph",
    "a diagram", "a document with text", "a logo", "a hand", "a human face",
    "an indoor scene", "an outdoor scene", "a close-up photo",
)

# Process-wide cache of loaded encoders keyed by (spec, cache_dir).
_ENCODER_CACHE: dict[tuple[str, str | None], Any] = {}


def _default_cache_dir() -> str | None:
    env = os.environ.get("DKG_DETECT_CACHE")
    if env:
        return env
    staged = _ROOT / "models" / "media-detect"
    if staged.exists():
        return str(staged)
    return None


class ImageDetector:
    name = "clip-zero-shot"

    def __init__(
        self, vision: str | None = None, text: str | None = None, cache_dir: str | None = None
    ) -> None:
        self._vspec = vision or os.environ.get("DKG_DETECT_VISION_MODEL") or DEFAULT_VISION_MODEL
        self._tspec = text or os.environ.get("DKG_DETECT_TEXT_MODEL") or DEFAULT_TEXT_MODEL
        self._cache_dir = cache_dir or _default_cache_dir()
        self._vis: Any | None = None
        self._txt: Any | None = None
        self._error: str | None = None

    def _load(self) -> None:
        if (self._vis is not None and self._txt is not None) or self._error is not None:
            return
        vkey = (self._vspec, self._cache_dir)
        tkey = (self._tspec, self._cache_dir)
        if vkey in _ENCODER_CACHE and tkey in _ENCODER_CACHE:
            self._vis = _ENCODER_CACHE[vkey]
            self._txt = _ENCODER_CACHE[tkey]
            return
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        try:
            from fastembed import ImageEmbedding, TextEmbedding
        except Exception as e:
            self._error = f"fastembed not installed: {e!r} (install the 'media-detect' extra)"
            return
        try:
            self._vis = ImageEmbedding(model_name=self._vspec, cache_dir=self._cache_dir)
            self._txt = TextEmbedding(model_name=self._tspec, cache_dir=self._cache_dir)
            _ENCODER_CACHE[vkey] = self._vis
            _ENCODER_CACHE[tkey] = self._txt
        except Exception as e:
            self._vis = None
            self._txt = None
            self._error = (
                f"CLIP detection models could not be loaded offline: {e!r}; "
                "pre-stage them with scripts/prestage_models.py"
            )

    def available(self) -> tuple[bool, str]:
        self._load()
        if self._vis is not None and self._txt is not None:
            return True, f"zero-shot CLIP ({self._vspec})"
        return False, self._error or "image detector unavailable"

    def detect(
        self,
        image_path: Path,
        *,
        labels: list[str] | None = None,
        top_k: int = 5,
        margin: float = 0.0,
    ) -> list[dict]:
        """Return ranked ``{label, score}`` detections for an image.

        ``score`` is the cosine similarity between the image and the label in the
        joint CLIP space, softmax-free and interpretable as relative confidence.
        """
        self._load()
        if self._vis is None or self._txt is None:
            raise RuntimeError(self._error or "image detector unavailable")
        import numpy as np

        label_list = list(labels) if labels else list(DEFAULT_LABELS)
        img = np.asarray(list(self._vis.embed([str(image_path)]))[0], dtype="float32")
        img = img / (np.linalg.norm(img) or 1.0)
        txt = np.asarray(list(self._txt.embed(label_list)), dtype="float32")
        txt = txt / (np.linalg.norm(txt, axis=1, keepdims=True) + 1e-12)
        sims = txt @ img
        order = np.argsort(-sims)
        out: list[dict] = []
        for idx in order[: max(1, int(top_k))]:
            score = float(sims[int(idx)])
            if score < margin:
                continue
            out.append({"label": label_list[int(idx)], "score": round(score, 4)})
        return out


def default_detector() -> ImageDetector | None:
    """Return the detector when available, else None."""
    d = ImageDetector()
    ok, _ = d.available()
    return d if ok else None

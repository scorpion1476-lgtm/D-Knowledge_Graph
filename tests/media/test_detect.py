"""Image object and content detection via zero-shot CLIP.

Gated on the 'media-detect' extra and pre-staged models; skips honestly otherwise.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from dkg.media.detect import ImageDetector

_OK, _WHY = ImageDetector().available()
requires_detect = pytest.mark.skipif(not _OK, reason=f"image detector unavailable: {_WHY}")

_SC_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(_SC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SC_ROOT))


def _solid(path: Path, rgb):
    from PIL import Image

    Image.new("RGB", (224, 224), rgb).save(path)


def test_detector_unavailable_reports_reason():
    # A nonexistent model must report unavailable rather than raising, so callers
    # degrade cleanly.
    d = ImageDetector(vision="nonexistent/model", text="nonexistent/model", cache_dir="/nonexistent")
    ok, why = d.available()
    assert ok is False
    assert isinstance(why, str) and why


@requires_detect
def test_detect_distinguishes_colors(tmp_path):
    d = ImageDetector()
    _solid(tmp_path / "red.png", (220, 20, 20))
    _solid(tmp_path / "blue.png", (20, 20, 220))
    labels = ["a red photo", "a blue photo", "a green photo"]
    assert d.detect(tmp_path / "red.png", labels=labels, top_k=1)[0]["label"] == "a red photo"
    assert d.detect(tmp_path / "blue.png", labels=labels, top_k=1)[0]["label"] == "a blue photo"


@requires_detect
def test_detect_margin_filters_low_confidence(tmp_path):
    d = ImageDetector()
    _solid(tmp_path / "red.png", (220, 20, 20))
    # A margin above any achievable cosine similarity yields no detections.
    assert d.detect(tmp_path / "red.png", labels=["a red photo"], top_k=3, margin=0.99) == []


@requires_detect
def test_measured_detection_meets_bar():
    import media_enrichment_accuracy as h

    m = h.measure_detect()
    assert m["available"] is True
    assert m["corpus_size"] >= 24
    assert m["top1_accuracy"] >= 0.75, m

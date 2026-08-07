"""OCR accuracy on the retained corpus. Skips when Pillow or tesseract is absent.

This turns the measured OCR accuracy into a passing acceptance: it renders each
retained ground-truth line and asserts the mean character and word error rates
stay below a generous bound. The exact measured numbers are published by
scripts/media_accuracy.py into test-evidence/media_accuracy.json.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("PIL")

from dkg.media.capability import tesseract_path  # noqa: E402

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

import media_accuracy  # noqa: E402


@pytest.mark.skipif(not tesseract_path(), reason="tesseract binary not installed")
def test_ocr_corpus_accuracy_within_bound():
    result = media_accuracy.measure_ocr()
    assert result["measured"] is True
    assert result["samples"] >= 5
    # Clean rendered text should OCR near-perfectly; keep a generous bound so a
    # font or tesseract version change does not cause a flaky failure.
    assert result["mean_cer"] < 0.2, result
    assert result["mean_wer"] < 0.2, result

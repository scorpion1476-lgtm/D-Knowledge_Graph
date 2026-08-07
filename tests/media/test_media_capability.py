"""Media capability detection. Pure-stdlib; always runs."""

from __future__ import annotations

from dkg.media.capability import have_pillow, probe


def test_probe_returns_honest_dict():
    p = probe()
    for key in ("pillow", "tesseract", "ffprobe", "asr_ready", "image_ocr_ready"):
        assert key in p
    assert isinstance(p["pillow"], bool)
    # asr_ready is only true when both an engine and a pre-staged model are present.
    if p["asr_ready"]:
        assert p["asr_engine"] and p["asr_model"]
    # image_ocr_ready implies Pillow and tesseract both present.
    if p["image_ocr_ready"]:
        assert p["pillow"] and p["tesseract"]


def test_have_pillow_is_bool():
    assert isinstance(have_pillow(), bool)

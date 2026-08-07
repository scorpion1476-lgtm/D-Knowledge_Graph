"""Image decode, EXIF, and OCR. Skips when Pillow or tesseract is absent."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PIL")

from dkg.core.errors import UnsupportedFormatError  # noqa: E402
from dkg.media.capability import tesseract_path  # noqa: E402
from dkg.media.images import read_image  # noqa: E402

_FONTS = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Verdana.ttf",
]


def _font(size: int = 34):
    from PIL import ImageFont

    for f in _FONTS:
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def _render(text: str, path: Path, size=(1100, 140)) -> None:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", size, "white")
    ImageDraw.Draw(img).text((15, 45), text, fill="black", font=_font())
    img.save(path)


@pytest.mark.skipif(not tesseract_path(), reason="tesseract binary not installed")
def test_image_ocr_reads_rendered_text(tmp_path):
    gt = "The quick brown fox 1234"
    p = tmp_path / "t.png"
    _render(gt, p)
    rr = read_image(p)
    assert rr.format == "image"
    assert "quick brown fox" in rr.text.lower()
    assert rr.metadata["ocr"]["tool"] == "tesseract"
    assert rr.metadata["original_sha256"]
    assert rr.metadata["width"] > 0 and rr.metadata["height"] > 0


def test_image_decode_and_metadata_without_ocr(tmp_path, monkeypatch):
    # Force the OCR-absent path so decode and metadata are exercised even where
    # tesseract exists.
    import dkg.media.images as im

    monkeypatch.setattr(im, "tesseract_path", lambda: None)
    p = tmp_path / "t.png"
    _render("hello", p)
    rr = read_image(p)
    assert rr.metadata["image_format"] == "png"
    assert rr.metadata["width"] > 0
    assert rr.metadata["ocr"]["tool"] is None
    assert "skipped" in rr.metadata["ocr"]["reason"].lower()


def test_heic_without_tool_degrades(tmp_path, monkeypatch):
    import dkg.media.images as im

    monkeypatch.setattr(im, "heif_tool", lambda: None)
    p = tmp_path / "x.heic"
    p.write_bytes(b"fake heic bytes")
    with pytest.raises(UnsupportedFormatError):
        read_image(p)

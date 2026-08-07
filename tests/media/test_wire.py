"""End-to-end: an image file ingests into the shared model with media provenance.

Skips when Pillow or tesseract is absent.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("PIL")

from dkg.ingest.base import ingest_path  # noqa: E402
from dkg.media.capability import tesseract_path  # noqa: E402

pytestmark = pytest.mark.skipif(not tesseract_path(), reason="tesseract binary not installed")

_FONTS = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]


def _render(text: str, path: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont

    font = None
    for f in _FONTS:
        if Path(f).exists():
            font = ImageFont.truetype(f, 34)
            break
    img = Image.new("RGB", (960, 120), "white")
    ImageDraw.Draw(img).text((15, 40), text, fill="black", font=font or ImageFont.load_default())
    img.save(path)


def test_ingest_image_into_shared_model(db, cfg, tmp_path):
    p = tmp_path / "invoice.png"
    _render("Acme Corporation invoice total 4096", p)

    result = ingest_path(db, p, audit_path=cfg.audit_path)
    assert result["documents_added"] == 1
    assert result["chunks_added"] >= 1

    doc = db.fetchone(
        "SELECT format, metadata_json FROM documents WHERE document_id=?;",
        (result["document_id"],),
    )
    assert doc["format"] == "image"
    meta = json.loads(doc["metadata_json"])
    assert meta["media_type"] == "image"
    assert meta["ocr"]["tool"] == "tesseract"
    assert meta["original_sha256"]

    # OCR text flowed into a chunk (the shared extraction pipeline saw it).
    chunk = db.fetchone(
        "SELECT text FROM chunks WHERE document_id=? ORDER BY ord LIMIT 1;",
        (result["document_id"],),
    )
    low = chunk["text"].lower()
    assert "acme" in low or "invoice" in low

    # provenance recorded for the document
    prov = db.fetchone(
        "SELECT COUNT(*) AS n FROM provenance WHERE subject_id=?;",
        (result["document_id"],),
    )
    assert prov["n"] >= 1

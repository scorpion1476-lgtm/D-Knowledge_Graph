"""Image ingestion: decode and EXIF via Pillow, OCR via tesseract.

Emits a ReadResult whose text is the OCR output and whose metadata carries the
image format, dimensions, EXIF, the original file hash, and the OCR tool and
version. SVG is routed to the stdlib text extractor. HEIC and HEIF are decoded
by an external tool under the carve-out. Degrades cleanly when Pillow or the
external tool is absent.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ..core.errors import IngestError, UnsupportedFormatError
from ..ingest.readers import ReadResult
from .capability import have_pillow, heif_tool, tesseract_path, tesseract_version
from .ocr import ocr_image_file
from .svg import read_svg_text

RASTER_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".tif", ".tiff", ".bmp"}
HEIF_EXTS = {".heic", ".heif"}
SVG_EXTS = {".svg"}
IMAGE_EXTS = RASTER_EXTS | HEIF_EXTS | SVG_EXTS

_MAX_BYTES = 60 * 1024 * 1024
_HEIF_TIMEOUT = 120


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _read_exif(img: Any) -> dict:
    try:
        from PIL.ExifTags import TAGS

        raw = img.getexif()
        if not raw:
            return {}
        out: dict = {}
        for tag_id, value in raw.items():
            name = TAGS.get(tag_id, str(tag_id))
            if isinstance(value, (str, int, float)):
                out[name] = value
            elif isinstance(value, bytes):
                out[name] = value.decode("utf-8", "replace")[:200]
            else:
                out[name] = str(value)[:200]
        return out
    except Exception:
        return {}


def _heif_to_png(tool: str, src: Path, dst: Path) -> None:
    import subprocess

    cmd = [tool, str(src), str(dst)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_HEIF_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        raise IngestError(f"HEIF conversion failed to run: {e}") from e
    if proc.returncode != 0 or not dst.exists():
        raise IngestError(f"HEIF conversion failed (rc={proc.returncode})")


def read_image(path: Path, *, lang: str = "eng") -> ReadResult:
    path = Path(path)
    ext = path.suffix.lower()
    size = path.stat().st_size
    if size > _MAX_BYTES:
        raise IngestError(f"image too large: {size} bytes")

    if ext in SVG_EXTS:
        text, meta = read_svg_text(path)
        meta["original_sha256"] = _sha256(path)
        return ReadResult(text=text, format="image", metadata=meta)

    if not have_pillow():
        raise UnsupportedFormatError(
            "image ingestion requires the 'media-image' extra: pip install d-knowledge-graph[media-image]"
        )

    import tempfile

    from PIL import Image

    with tempfile.TemporaryDirectory() as td:
        if ext in HEIF_EXTS:
            tool = heif_tool()
            if not tool:
                raise UnsupportedFormatError(
                    "HEIC/HEIF ingestion requires an external heif tool (heif-convert, libheif) "
                    "or ImageMagick; none found"
                )
            png_path = Path(td) / "converted.png"
            _heif_to_png(tool, path, png_path)
            img = Image.open(png_path)
        else:
            img = Image.open(path)

        img_format = (img.format or ext.lstrip(".")).lower()
        width, height = img.size
        exif = _read_exif(img)

        ocr_text = ""
        if tesseract_path():
            rgb = img.convert("RGB")
            ocr_png = Path(td) / "ocr.png"
            rgb.save(ocr_png, format="PNG")
            ocr_text = ocr_image_file(ocr_png, lang=lang)
            ocr_meta: dict = {"tool": "tesseract", "tool_version": tesseract_version(), "lang": lang}
        else:
            ocr_meta = {"tool": None, "reason": "tesseract not installed; OCR skipped"}

    meta = {
        "media_type": "image",
        "image_format": img_format,
        "width": width,
        "height": height,
        "original_sha256": _sha256(path),
        "exif": exif,
        "ocr": ocr_meta,
    }
    return ReadResult(text=ocr_text.strip(), format="image", metadata=meta)

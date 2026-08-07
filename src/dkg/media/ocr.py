"""OCR via the external tesseract binary (Apache-2.0).

Original subprocess wrapper: list arguments, no shell, bounded timeout, no
network. The caller provides an image file that tesseract can read (the image
module normalises to PNG first).
"""

from __future__ import annotations

from pathlib import Path

from ..core.errors import IngestError, UnsupportedFormatError
from .capability import tesseract_path

_OCR_TIMEOUT = 120


def ocr_image_file(png_path: Path, *, lang: str = "eng", psm: int = 3) -> str:
    tp = tesseract_path()
    if not tp:
        raise UnsupportedFormatError(
            "OCR requires the tesseract binary (Apache-2.0); install tesseract to enable it"
        )
    import subprocess

    cmd = [tp, str(png_path), "stdout", "-l", lang, "--psm", str(int(psm))]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_OCR_TIMEOUT)
    except subprocess.TimeoutExpired as e:
        raise IngestError(f"OCR timed out after {_OCR_TIMEOUT}s") from e
    except OSError as e:
        raise IngestError(f"OCR failed to run tesseract: {e}") from e
    if proc.returncode != 0:
        raise IngestError(f"tesseract failed (rc={proc.returncode}): {proc.stderr.strip()[:200]}")
    return proc.stdout

"""Media capability detection.

Reports which optional media tools and models are available so the ingestion
layer can degrade cleanly and tests can skip honestly. Checks are lazy; no heavy
optional dependency is imported at module load.
"""

from __future__ import annotations

import os
import shutil
import subprocess

_VERSION_TIMEOUT = 20


def _which(name: str) -> str | None:
    return shutil.which(name)


def have_pillow() -> bool:
    try:
        import PIL  # noqa: F401

        return True
    except ImportError:
        return False


def tesseract_path() -> str | None:
    return _which("tesseract")


def ffprobe_path() -> str | None:
    return _which("ffprobe")


def ffmpeg_path() -> str | None:
    return _which("ffmpeg")


def heif_tool() -> str | None:
    """An external HEIC/HEIF decoder, if present (libheif or ImageMagick)."""
    for name in ("heif-convert", "heif-dec"):
        p = _which(name)
        if p:
            return p
    return _which("magick") or _which("convert")


def asr_engine() -> tuple[str, str] | None:
    """Return (engine_name, locator) for an available ASR engine, or None.

    Order: a whisper.cpp style CLI, then the in-process faster-whisper. A model
    must still be pre-staged (DKG_ASR_MODEL) for either to be usable.
    """
    for name in ("whisper-cli", "whisper-cpp"):
        p = _which(name)
        if p:
            return ("whisper.cpp", p)
    try:
        import faster_whisper  # noqa: F401

        return ("faster-whisper", "faster_whisper")
    except ImportError:
        return None


def asr_model() -> str | None:
    model = os.environ.get("DKG_ASR_MODEL", "").strip()
    return model or None


def keyframe_ready() -> bool:
    """Scene and keyframe detection needs the ffmpeg external binary."""
    return bool(ffmpeg_path())


def keyframe_ocr_ready() -> bool:
    """On-screen keyframe OCR needs ffmpeg (frames) and tesseract (text)."""
    return bool(ffmpeg_path()) and bool(tesseract_path())


def detect_ready() -> tuple[bool, str]:
    """Image object and content detection needs fastembed and pre-staged models."""
    from .detect import ImageDetector

    return ImageDetector().available()


def _first_line(path: str, *args: str) -> str:
    try:
        proc = subprocess.run(
            [path, *args],
            capture_output=True,
            text=True,
            timeout=_VERSION_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    lines = (proc.stdout or proc.stderr or "").splitlines()
    return lines[0].strip() if lines else ""


def tesseract_version() -> str:
    tp = tesseract_path()
    return _first_line(tp, "--version") if tp else ""


def ffprobe_version() -> str:
    fp = ffprobe_path()
    return _first_line(fp, "-version") if fp else ""


def probe() -> dict:
    """An honest snapshot of media capabilities in this environment."""
    eng = asr_engine()
    tp = tesseract_path()
    fp = ffprobe_path()
    return {
        "pillow": have_pillow(),
        "tesseract": tp,
        "ffprobe": fp,
        "ffmpeg": ffmpeg_path(),
        "heif_tool": heif_tool(),
        "asr_engine": eng[0] if eng else None,
        "asr_model": asr_model(),
        "image_ingestion_ready": have_pillow(),
        "image_ocr_ready": have_pillow() and bool(tp),
        "video_metadata_ready": bool(fp),
        "asr_ready": bool(eng) and bool(asr_model()),
        "keyframe_ready": bool(fp),
        "keyframe_ocr_ready": bool(fp) and bool(tp),
    }

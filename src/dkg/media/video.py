"""Video ingestion: ffprobe metadata, subtitles, and an ASR transcript.

Emits a ReadResult whose text is the subtitle track (if a sidecar is present) or
the ASR transcript, both with per-cue timecodes, and whose metadata carries the
container and stream summary, the subtitle source, and the ASR engine and tool
provenance. Requires ffprobe; ASR and subtitles are optional and degrade cleanly.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..ingest.readers import ReadResult
from . import asr as asr_mod
from .capability import asr_engine, ffprobe_path
from .ffprobe import probe_media, summarize
from .subtitles import Cue, cues_to_text, parse_subtitle_file

_SIDECAR_EXTS = (".srt", ".vtt")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _sidecar_cues(path: Path) -> tuple[list[Cue], str | None]:
    for ext in _SIDECAR_EXTS:
        sidecar = path.with_suffix(ext)
        if sidecar.exists() and sidecar.is_file():
            return parse_subtitle_file(sidecar), sidecar.name
    return [], None


def read_video(
    path: Path, *, language: str | None = None, with_keyframes: bool = False
) -> ReadResult:
    path = Path(path)
    summary = summarize(probe_media(path))  # raises UnsupportedFormatError if ffprobe absent

    cues, sidecar_name = _sidecar_cues(path)
    asr_meta: dict = {}
    asr_text = ""

    if not cues:
        ok, reason = asr_mod.available()
        if ok:
            try:
                segments = asr_mod.transcribe(path, language=language)
                asr_text = "\n".join(
                    f"[{_fmt(s.start)} -> {_fmt(s.end)}] {s.text}" for s in segments if s.text
                )
                eng = asr_engine()
                asr_meta = {
                    "engine": eng[0] if eng else None,
                    "segments": len(segments),
                }
            except Exception as e:
                asr_meta = {"available": True, "error": str(e)[:200]}
        else:
            asr_meta = {"available": False, "reason": reason}

    if cues:
        text = cues_to_text(cues, with_timecodes=True)
    else:
        text = asr_text

    keyframe_meta: dict = {"enabled": with_keyframes}
    if with_keyframes:
        text, keyframe_meta = _merge_keyframe_ocr(path, text, keyframe_meta)

    meta = {
        "media_type": "video",
        "original_sha256": _sha256(path),
        "container": summary,
        "subtitles": {
            "source": "sidecar" if sidecar_name else None,
            "sidecar": sidecar_name,
            "cue_count": len(cues),
        },
        "asr": asr_meta,
        "keyframes": keyframe_meta,
        "tools": {"ffprobe": ffprobe_path()},
    }
    return ReadResult(text=text, format="video", metadata=meta)


def _merge_keyframe_ocr(path: Path, text: str, keyframe_meta: dict) -> tuple[str, dict]:
    """Append timecoded on-screen keyframe text to the video text, if possible.

    Degrades cleanly: when ffmpeg or tesseract is absent, the video text is
    returned unchanged with an honest reason recorded in the metadata.
    """
    import tempfile

    from .capability import keyframe_ocr_ready
    from .keyframes import keyframe_ocr_text, ocr_keyframes

    if not keyframe_ocr_ready():
        keyframe_meta = {**keyframe_meta, "available": False, "reason": "ffmpeg or tesseract absent"}
        return text, keyframe_meta
    try:
        with tempfile.TemporaryDirectory(prefix="dkg-keyframes-") as td:
            results = ocr_keyframes(path, Path(td))
        onscreen = keyframe_ocr_text(results)
    except Exception as e:  # keyframe OCR is best-effort enrichment
        keyframe_meta = {**keyframe_meta, "available": True, "error": str(e)[:200]}
        return text, keyframe_meta
    keyframe_meta = {
        **keyframe_meta,
        "available": True,
        "count": len(results),
        "with_text": sum(1 for r in results if r.get("text")),
        "source": "ffmpeg+tesseract",
    }
    if onscreen:
        marker = "[on-screen text]\n"
        text = (text + "\n\n" + marker + onscreen) if text else (marker + onscreen)
    return text, keyframe_meta


def _fmt(sec: float) -> str:
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"

"""Audio transcription (ASR), capability-detected and air-gapped.

Supports a whisper.cpp style CLI or the in-process faster-whisper engine. In both
cases the model must be pre-staged and referenced by DKG_ASR_MODEL; nothing is
downloaded at runtime (faster-whisper runs with local-files-only). If no engine
or model is present, ASR is unavailable and the caller degrades cleanly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..core.errors import IngestError, UnsupportedFormatError
from .capability import asr_engine, asr_model, ffmpeg_path

_ASR_TIMEOUT = 1800
_WAV_TIMEOUT = 600


@dataclass
class Segment:
    start: float
    end: float
    text: str


def available() -> tuple[bool, str]:
    eng = asr_engine()
    if not eng:
        return False, "no ASR engine found (install whisper.cpp or the asr-faster-whisper extra)"
    if not asr_model():
        return False, "no pre-staged ASR model; set DKG_ASR_MODEL to a local model path"
    return True, f"engine={eng[0]}, model={asr_model()}"


def transcribe(path: Path, *, language: str | None = None) -> list[Segment]:
    ok, reason = available()
    if not ok:
        raise UnsupportedFormatError(f"ASR unavailable: {reason}")
    eng = asr_engine()
    assert eng is not None  # guarded by available()
    name, locator = eng
    if name == "faster-whisper":
        return _transcribe_faster_whisper(Path(path), language)
    return _transcribe_whisper_cpp(locator, Path(path), language)


def _transcribe_faster_whisper(path: Path, language: str | None) -> list[Segment]:
    from faster_whisper import WhisperModel

    model_ref = asr_model()
    # local_files_only prevents any runtime download; model_ref is a local path.
    model = WhisperModel(model_ref, device="cpu", compute_type="int8", local_files_only=True)
    segments, _info = model.transcribe(str(path), language=language, vad_filter=False)
    out: list[Segment] = []
    for s in segments:
        out.append(Segment(start=float(s.start), end=float(s.end), text=(s.text or "").strip()))
    return out


def _extract_wav(src: Path, dst: Path) -> None:
    fp = ffmpeg_path()
    if not fp:
        raise UnsupportedFormatError("ffmpeg is needed to extract audio for ASR; none found")
    import subprocess

    cmd = [fp, "-nostdin", "-y", "-i", str(src), "-ar", "16000", "-ac", "1", "-f", "wav", str(dst)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_WAV_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        raise IngestError(f"ffmpeg audio extraction failed to run: {e}") from e
    if proc.returncode != 0 or not dst.exists():
        raise IngestError(f"ffmpeg audio extraction failed (rc={proc.returncode})")


def _transcribe_whisper_cpp(binary: str, path: Path, language: str | None) -> list[Segment]:
    import json
    import subprocess
    import tempfile

    model = asr_model()
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "audio.wav"
        _extract_wav(path, wav)
        out_base = Path(td) / "out"
        cmd = [binary, "-m", str(model), "-f", str(wav), "-oj", "-of", str(out_base)]
        if language:
            cmd += ["-l", language]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_ASR_TIMEOUT)
        except (OSError, subprocess.SubprocessError) as e:
            raise IngestError(f"whisper.cpp failed to run: {e}") from e
        if proc.returncode != 0:
            raise IngestError(f"whisper.cpp failed (rc={proc.returncode}): {proc.stderr[:200]}")
        json_path = out_base.with_suffix(".json")
        if not json_path.exists():
            raise IngestError("whisper.cpp produced no JSON output")
        data = json.loads(json_path.read_text(encoding="utf-8"))
    segs: list[Segment] = []
    for tr in data.get("transcription", []):
        off = tr.get("offsets", {}) or {}
        segs.append(
            Segment(
                start=float(off.get("from", 0)) / 1000.0,
                end=float(off.get("to", 0)) / 1000.0,
                text=(tr.get("text") or "").strip(),
            )
        )
    return segs

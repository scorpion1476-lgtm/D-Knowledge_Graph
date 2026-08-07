"""Scene and keyframe detection, and on-screen keyframe OCR.

ffmpeg is invoked only as an external system binary (the copyleft carve-out):
list arguments, no shell, a bounded timeout, and no network. It is never vendored
or Python-linked. Representative keyframes are extracted at scene boundaries with
timecodes; on-screen text is read from those keyframes by the existing tesseract
wrapper. Both are optional and capability-detected.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from ..core.errors import IngestError, UnsupportedFormatError
from .capability import ffmpeg_path, tesseract_path
from .ocr import ocr_image_file

_FFMPEG_TIMEOUT = 300
_PTS_RE = re.compile(r"pts_time:([0-9]+\.?[0-9]*)")
_MAX_KEYFRAMES_CAP = 500
_DEFAULT_THRESHOLD = 0.3


def detect_scenes_and_keyframes(
    video: Path,
    out_dir: Path,
    *,
    threshold: float = _DEFAULT_THRESHOLD,
    max_keyframes: int = 64,
    timeout: int = _FFMPEG_TIMEOUT,
) -> list[dict]:
    """Extract representative keyframes at scene boundaries with timecodes.

    Always includes the first frame. Returns a list of
    ``{"index", "pts_time", "path"}`` ordered by time. Raises when ffmpeg is
    absent so the caller can degrade cleanly.
    """
    fp = ffmpeg_path()
    if not fp:
        raise UnsupportedFormatError(
            "keyframe and scene detection requires the ffmpeg binary (external, carve-out); "
            "install ffmpeg to enable it"
        )
    video = Path(video)
    out_dir = Path(out_dir)
    if not video.is_file():
        raise IngestError(f"video not found: {video}")
    threshold = max(0.0, min(float(threshold), 1.0))
    max_keyframes = max(1, min(int(max_keyframes), _MAX_KEYFRAMES_CAP))
    out_dir.mkdir(parents=True, exist_ok=True)
    # Clear any keyframes from a prior run in a reused directory, so the file
    # list reflects only this video and never mixes in stale frames.
    for stale in out_dir.glob("keyframe_*.png"):
        stale.unlink(missing_ok=True)

    # First frame plus every frame whose scene score exceeds the threshold.
    # Commas inside the ffmpeg expression are escaped so the top-level comma only
    # separates the select and showinfo filters.
    vf = f"select='eq(n\\,0)+gt(scene\\,{threshold})',showinfo"
    pattern = str(out_dir / "keyframe_%04d.png")
    cmd = [fp, "-hide_banner", "-nostdin", "-i", str(video), "-vf", vf, "-vsync", "vfr", pattern]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise IngestError(f"keyframe detection timed out after {timeout}s") from e
    except OSError as e:
        raise IngestError(f"failed to run ffmpeg: {e}") from e
    if proc.returncode != 0:
        raise IngestError(f"ffmpeg failed (rc={proc.returncode}): {proc.stderr.strip()[:200]}")

    pts_times = [float(m) for m in _PTS_RE.findall(proc.stderr)]
    files = sorted(out_dir.glob("keyframe_*.png"))
    keyframes: list[dict] = []
    for i, path in enumerate(files):
        pts = pts_times[i] if i < len(pts_times) else float(i)
        keyframes.append({"index": i, "pts_time": round(pts, 3), "path": str(path)})
    # Bound the count; remove any extras beyond the cap so the caller is not
    # handed an unbounded number of frames on a pathological input.
    if len(keyframes) > max_keyframes:
        for extra in keyframes[max_keyframes:]:
            Path(extra["path"]).unlink(missing_ok=True)
        keyframes = keyframes[:max_keyframes]
    return keyframes


def ocr_keyframes(
    video: Path,
    out_dir: Path,
    *,
    lang: str = "eng",
    threshold: float = _DEFAULT_THRESHOLD,
    max_keyframes: int = 64,
    psm: int = 6,
) -> list[dict]:
    """Detect keyframes and read on-screen text from each with tesseract.

    Returns a list of ``{"index", "pts_time", "text"}`` with the recognized text
    per keyframe (empty string when a keyframe has no readable text).
    """
    if not tesseract_path():
        raise UnsupportedFormatError(
            "on-screen keyframe OCR requires the tesseract binary (Apache-2.0); "
            "install tesseract to enable it"
        )
    keyframes = detect_scenes_and_keyframes(
        video, out_dir, threshold=threshold, max_keyframes=max_keyframes
    )
    results: list[dict] = []
    for kf in keyframes:
        try:
            text = ocr_image_file(Path(kf["path"]), lang=lang, psm=psm).strip()
        except (IngestError, UnsupportedFormatError):
            text = ""
        results.append({"index": kf["index"], "pts_time": kf["pts_time"], "text": text})
    return results


def _fmt_timecode(sec: float) -> str:
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def keyframe_ocr_text(results: list[dict]) -> str:
    """Render keyframe OCR results as timecoded lines for the shared model."""
    lines = []
    for r in results:
        text = (r.get("text") or "").replace("\n", " ").strip()
        if text:
            lines.append(f"[{_fmt_timecode(r['pts_time'])}] {text}")
    return "\n".join(lines)

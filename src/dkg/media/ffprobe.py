"""Container and stream metadata via the external ffprobe binary.

ffprobe (ffmpeg, LGPL or GPL) is invoked only as an external system binary under
the copyleft carve-out: list arguments, no shell, bounded timeout, no network.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..core.errors import IngestError, UnsupportedFormatError
from .capability import ffprobe_path

_PROBE_TIMEOUT = 60


def probe_media(path: Path) -> dict:
    fp = ffprobe_path()
    if not fp:
        raise UnsupportedFormatError(
            "video metadata requires the external ffprobe binary (ffmpeg, carve-out); none found"
        )
    import subprocess

    cmd = [
        fp,
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-print_format",
        "json",
        str(path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_PROBE_TIMEOUT)
    except subprocess.TimeoutExpired as e:
        raise IngestError("ffprobe timed out") from e
    except OSError as e:
        raise IngestError(f"ffprobe failed to run: {e}") from e
    if proc.returncode != 0:
        raise IngestError(f"ffprobe failed (rc={proc.returncode}): {proc.stderr.strip()[:200]}")
    try:
        data: dict = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise IngestError(f"ffprobe returned invalid JSON: {e}") from e
    return data


def summarize(data: dict) -> dict:
    fmt = data.get("format", {}) or {}
    streams = data.get("streams", []) or []
    return {
        "duration_seconds": float(fmt.get("duration", 0.0) or 0.0),
        "container": fmt.get("format_name", ""),
        "size_bytes": int(fmt.get("size", 0) or 0),
        "stream_count": len(streams),
        "streams": [
            {
                "index": s.get("index"),
                "codec_type": s.get("codec_type"),
                "codec_name": s.get("codec_name"),
            }
            for s in streams
        ],
        "subtitle_stream_indexes": [
            s.get("index") for s in streams if s.get("codec_type") == "subtitle"
        ],
    }

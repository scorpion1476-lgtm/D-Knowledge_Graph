"""ffprobe metadata. Skips when ffprobe or ffmpeg is absent."""

from __future__ import annotations

import subprocess

import pytest

from dkg.media.capability import ffmpeg_path, ffprobe_path
from dkg.media.ffprobe import probe_media, summarize

pytestmark = pytest.mark.skipif(
    not (ffprobe_path() and ffmpeg_path()), reason="ffprobe or ffmpeg not installed"
)


def _make_video(path):
    subprocess.run(
        [
            ffmpeg_path(),
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1:size=128x128:rate=5",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        capture_output=True,
        timeout=60,
        check=True,
    )


def test_probe_metadata(tmp_path):
    v = tmp_path / "v.mp4"
    _make_video(v)
    data = probe_media(v)
    summary = summarize(data)
    assert summary["stream_count"] >= 1
    assert summary["duration_seconds"] > 0
    assert any(s["codec_type"] == "video" for s in summary["streams"])

"""Video ingestion: metadata, sidecar subtitles, ASR meta. Skips without ffprobe."""

from __future__ import annotations

import subprocess

import pytest

from dkg.media.capability import ffmpeg_path, ffprobe_path
from dkg.media.video import read_video

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


def test_video_with_sidecar_subtitles(tmp_path):
    v = tmp_path / "v.mp4"
    _make_video(v)
    (tmp_path / "v.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nCaption one\n", encoding="utf-8"
    )
    rr = read_video(v)
    assert rr.format == "video"
    assert "Caption one" in rr.text
    assert "->" in rr.text  # timecoded
    assert rr.metadata["subtitles"]["source"] == "sidecar"
    assert rr.metadata["container"]["stream_count"] >= 1
    assert rr.metadata["original_sha256"]


def test_video_metadata_and_asr_meta_present(tmp_path):
    v = tmp_path / "v2.mp4"
    _make_video(v)
    rr = read_video(v)  # no sidecar; ASR may or may not be available
    assert rr.metadata["media_type"] == "video"
    assert isinstance(rr.metadata["asr"], dict)
    assert isinstance(rr.text, str)
    assert rr.metadata["container"]["duration_seconds"] > 0

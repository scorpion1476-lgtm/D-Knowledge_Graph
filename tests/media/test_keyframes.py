"""Scene/keyframe detection, on-screen keyframe OCR, and video wiring.

Gated on the external ffmpeg and tesseract binaries AND on Pillow: the fixture
video is drawn frame by frame with Pillow before ffmpeg encodes it, so a host
that has the binaries but not the optional media-image extra must skip rather
than fail. Guarding on the binaries alone let these tests fail on exactly that
combination, which broke the rule that the core passes with no optional
dependency present.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from dkg.media.capability import ffmpeg_path, have_pillow, tesseract_path

requires_tools = pytest.mark.skipif(
    not (ffmpeg_path() and tesseract_path() and have_pillow()),
    reason="ffmpeg, tesseract, or Pillow not installed",
)

_SC_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(_SC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SC_ROOT))


def _small_video(dest: Path, words) -> Path:
    from PIL import Image, ImageDraw

    frames = dest / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    colors = [(200, 30, 30), (30, 30, 200), (30, 160, 30)]
    for i, w in enumerate(words):
        im = Image.new("RGB", (640, 480), colors[i % len(colors)])
        ImageDraw.Draw(im).text((60, 200), w, fill=(255, 255, 255))
        im.save(frames / f"s_{i:02d}.png")
    video = dest / "v.mp4"
    subprocess.run(
        [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y", "-framerate", "1/1.5",
         "-i", str(frames / "s_%02d.png"), "-c:v", "libx264", "-r", "30", "-pix_fmt", "yuv420p", str(video)],
        capture_output=True, timeout=120, check=True,
    )
    return video


@requires_tools
def test_scene_detection_finds_multiple_keyframes(tmp_path):
    from dkg.media.keyframes import detect_scenes_and_keyframes

    video = _small_video(tmp_path, ["ALPHA", "BRAVO", "CHARLIE"])
    kfs = detect_scenes_and_keyframes(video, tmp_path / "keys", threshold=0.3)
    # First frame plus at least one detected cut.
    assert len(kfs) >= 2
    assert kfs[0]["pts_time"] == 0.0
    assert all(Path(k["path"]).exists() for k in kfs)


@requires_tools
def test_keyframe_ocr_recovers_on_screen_text(tmp_path):
    from dkg.media.keyframes import ocr_keyframes

    video = _small_video(tmp_path, ["ALPHA", "BRAVO", "CHARLIE"])
    results = ocr_keyframes(video, tmp_path / "keys", threshold=0.3)
    joined = " ".join(r["text"].upper() for r in results)
    assert "ALPHA" in joined


@requires_tools
def test_read_video_with_keyframes_merges_text(tmp_path):
    from dkg.media.video import read_video

    video = _small_video(tmp_path, ["ALPHA", "BRAVO", "CHARLIE"])
    rr = read_video(video, with_keyframes=True)
    assert rr.metadata["keyframes"]["enabled"] is True
    assert rr.metadata["keyframes"]["available"] is True
    assert "on-screen text" in rr.text
    assert "ALPHA" in rr.text.upper()


@requires_tools
def test_measured_scene_and_ocr_meets_bar():
    import media_enrichment_accuracy as h

    m = h.measure_scene_and_ocr()
    assert m["available"] is True
    assert m["scene_recall"] >= 0.7, m
    assert m["ocr_word_recall"] >= 0.7, m


def test_read_video_default_has_no_keyframe_pass(tmp_path):
    # The default video read path is unchanged (keyframes off), so callers without
    # ffmpeg or tesseract are unaffected. Gated only on ffprobe via read_video.
    pytest.importorskip("PIL")
    if not ffmpeg_path():
        pytest.skip("ffprobe/ffmpeg absent; covered by the media capability tests")
    from dkg.media.video import read_video

    video = _small_video(tmp_path, ["ALPHA", "BRAVO"])
    rr = read_video(video)
    assert rr.metadata["keyframes"]["enabled"] is False
    assert "on-screen text" not in rr.text

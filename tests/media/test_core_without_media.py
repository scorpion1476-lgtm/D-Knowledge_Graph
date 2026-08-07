"""Graceful degradation in the core environment (no optional media deps).

These prove the media extensions are recognised and that an absent optional tool
degrades to a clear, non-fatal error rather than crashing or garbling.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dkg.core.errors import UnsupportedFormatError
from dkg.ingest.readers import sniff_format
from dkg.media.capability import ffprobe_path, have_pillow


def test_sniff_media_extensions():
    assert sniff_format(Path("x.png")) == "image"
    assert sniff_format(Path("x.svg")) == "image"
    assert sniff_format(Path("x.mp4")) == "video"
    assert sniff_format(Path("x.mkv")) == "video"


@pytest.mark.skipif(have_pillow(), reason="Pillow present; this checks the absent-Pillow degrade path")
def test_image_without_pillow_degrades(tmp_path):
    from dkg.ingest.readers import read_file

    p = tmp_path / "x.png"
    p.write_bytes(b"not a real png")
    with pytest.raises(UnsupportedFormatError):
        read_file(p)


@pytest.mark.skipif(ffprobe_path() is not None, reason="ffprobe present; this checks the absent-ffprobe degrade path")
def test_video_without_ffprobe_degrades(tmp_path):
    from dkg.ingest.readers import read_file

    p = tmp_path / "x.mp4"
    p.write_bytes(b"not a real video")
    with pytest.raises(UnsupportedFormatError):
        read_file(p)

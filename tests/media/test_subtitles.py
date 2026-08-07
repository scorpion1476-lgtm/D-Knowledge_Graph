"""SRT and WebVTT parsing. Pure-stdlib; always runs."""

from __future__ import annotations

from dkg.media.subtitles import cues_to_text, parse_srt, parse_subtitle_file, parse_vtt

SRT = (
    "1\n00:00:01,000 --> 00:00:03,500\nHello world\n\n"
    "2\n00:00:04,000 --> 00:00:05,000\nSecond line\n"
)
VTT = (
    "WEBVTT\n\n00:00:01.000 --> 00:00:03.500\nHello world\n\n"
    "00:00:04.000 --> 00:00:05.000\n<v Bob>Second line</v>\n"
)


def test_parse_srt():
    cues = parse_srt(SRT)
    assert len(cues) == 2
    assert cues[0].start == 1.0
    assert abs(cues[0].end - 3.5) < 1e-6
    assert cues[0].text == "Hello world"
    assert cues[1].text == "Second line"


def test_parse_vtt_strips_tags():
    cues = parse_vtt(VTT)
    assert len(cues) == 2
    assert cues[0].text == "Hello world"
    assert cues[1].text == "Second line"


def test_cues_to_text_has_timecodes():
    txt = cues_to_text(parse_srt(SRT), with_timecodes=True)
    assert "->" in txt
    assert "Hello world" in txt


def test_parse_subtitle_file_by_extension(tmp_path):
    srt = tmp_path / "s.srt"
    srt.write_text(SRT, encoding="utf-8")
    vtt = tmp_path / "s.vtt"
    vtt.write_text(VTT, encoding="utf-8")
    assert len(parse_subtitle_file(srt)) == 2
    assert len(parse_subtitle_file(vtt)) == 2

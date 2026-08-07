"""Pure-Python SRT and WebVTT subtitle parsers.

Returns timecoded cues. No third-party dependency; the caller provides the file
or text. Used for sidecar subtitles and for subtitle streams that ffmpeg has
extracted to a sidecar file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_TS = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,3})")
_TAG = re.compile(r"<[^>]+>")


@dataclass
class Cue:
    start: float  # seconds
    end: float  # seconds
    text: str


def _to_seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def _fmt(sec: float) -> str:
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def _blocks(text: str) -> list[list[str]]:
    out: list[list[str]] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if lines:
            out.append(lines)
    return out


def parse_srt(text: str) -> list[Cue]:
    cues: list[Cue] = []
    for lines in _blocks(text):
        idx = 1 if lines[0].strip().isdigit() else 0
        if idx >= len(lines):
            continue
        stamps = _TS.findall(lines[idx])
        if len(stamps) < 2:
            continue
        body = " ".join(lines[idx + 1 :]).strip()
        if body:
            cues.append(Cue(_to_seconds(*stamps[0]), _to_seconds(*stamps[1]), body))
    return cues


def parse_vtt(text: str) -> list[Cue]:
    body = re.sub(r"^﻿?WEBVTT.*?(\n\n|\r\n\r\n)", "", text, count=1, flags=re.DOTALL)
    cues: list[Cue] = []
    for lines in _blocks(body):
        timing_i = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if timing_i is None:
            continue
        stamps = _TS.findall(lines[timing_i])
        if len(stamps) < 2:
            continue
        cue_text = _TAG.sub("", " ".join(lines[timing_i + 1 :])).strip()
        if cue_text:
            cues.append(Cue(_to_seconds(*stamps[0]), _to_seconds(*stamps[1]), cue_text))
    return cues


def parse_subtitle_file(path: Path) -> list[Cue]:
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() == ".vtt":
        return parse_vtt(text)
    return parse_srt(text)


def cues_to_text(cues: list[Cue], *, with_timecodes: bool = True) -> str:
    if with_timecodes:
        return "\n".join(f"[{_fmt(c.start)} -> {_fmt(c.end)}] {c.text}" for c in cues)
    return "\n".join(c.text for c in cues)

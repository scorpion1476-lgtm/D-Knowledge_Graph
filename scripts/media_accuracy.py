#!/usr/bin/env python3
"""Measure OCR and ASR accuracy on the retained corpora.

OCR: renders each ground-truth line with Pillow using a system font, runs
tesseract, and measures the mean character error rate (CER) and word error rate
(WER). ASR: if an engine and a pre-staged model are present, synthesises speech
for each ground-truth line (build-time fixture generation) and measures the mean
WER; otherwise it records honestly that ASR was not measured and why.

Writes test-evidence/media_accuracy.json. No forced green.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "media" / "corpus"
OUT = ROOT / "test-evidence" / "media_accuracy.json"
_FONTS = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Verdana.ttf",
]


def _lev(a: Sequence, b: Sequence) -> int:
    m, n = len(a), len(b)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, n + 1):
            cur = dp[j]
            dp[j] = min(dp[j] + 1, dp[j - 1] + 1, prev + (0 if a[i - 1] == b[j - 1] else 1))
            prev = cur
    return dp[n]


def _cer(ref: str, hyp: str) -> float:
    return _lev(list(ref), list(hyp)) / max(1, len(ref))


def _wer(ref: str, hyp: str) -> float:
    r, h = ref.split(), hyp.split()
    return _lev(r, h) / max(1, len(r))


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def _lines(name: str) -> list[str]:
    return [ln.strip() for ln in (CORPUS / name).read_text(encoding="utf-8").splitlines() if ln.strip()]


def _font(size: int):
    from PIL import ImageFont

    for f in _FONTS:
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def measure_ocr() -> dict:
    lines = _lines("ocr_ground_truth.txt")
    from dkg.media.capability import have_pillow, tesseract_path, tesseract_version

    if not have_pillow():
        return {"measured": False, "reason": "Pillow absent", "samples": len(lines)}
    if not tesseract_path():
        return {"measured": False, "reason": "tesseract absent", "samples": len(lines)}

    from PIL import Image, ImageDraw

    from dkg.media.ocr import ocr_image_file

    font = _font(34)
    cers, wers = [], []
    with tempfile.TemporaryDirectory() as td:
        for i, gt in enumerate(lines):
            img = Image.new("RGB", (1300, 120), "white")
            ImageDraw.Draw(img).text((15, 40), gt, fill="black", font=font)
            p = Path(td) / f"s{i}.png"
            img.save(p)
            hyp = ocr_image_file(p, psm=7).strip()
            cers.append(_cer(gt, hyp))
            wers.append(_wer(gt, hyp))
    n = len(lines)
    return {
        "measured": True,
        "samples": n,
        "mean_cer": round(sum(cers) / n, 4),
        "mean_wer": round(sum(wers) / n, 4),
        "tool": "tesseract",
        "tool_version": tesseract_version(),
    }


def measure_asr() -> dict:
    lines = _lines("asr_ground_truth.txt")
    from dkg.media.asr import available, transcribe
    from dkg.media.capability import ffmpeg_path

    ok, reason = available()
    if not ok:
        return {"measured": False, "reason": reason, "samples": len(lines)}
    say = shutil.which("say")
    ff = ffmpeg_path()
    if not say or not ff:
        return {
            "measured": False,
            "reason": "no local text-to-speech (say) or ffmpeg to synthesise the audio corpus",
            "samples": len(lines),
        }
    wers = []
    with tempfile.TemporaryDirectory() as td:
        for i, gt in enumerate(lines):
            aiff = Path(td) / f"a{i}.aiff"
            wav = Path(td) / f"a{i}.wav"
            subprocess.run([say, "-o", str(aiff), gt], check=True, timeout=60)
            subprocess.run(
                [ff, "-nostdin", "-y", "-i", str(aiff), "-ar", "16000", "-ac", "1", str(wav)],
                capture_output=True,
                check=True,
                timeout=180,
            )
            segs = transcribe(wav)
            hyp = " ".join(s.text for s in segs)
            wers.append(_wer(_norm(gt), _norm(hyp)))
    n = len(lines)
    return {"measured": True, "samples": n, "mean_wer": round(sum(wers) / n, 4)}


def run() -> dict:
    return {"generated_at": "2026-08-02", "ocr": measure_ocr(), "asr": measure_asr()}


def main() -> int:
    result = run()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

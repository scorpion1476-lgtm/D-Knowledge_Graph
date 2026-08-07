#!/usr/bin/env python3
"""Measure media-enrichment accuracy on retained, generated corpora.

Three capabilities, each on a fixed, reproducible corpus generated deterministically
(no bundled third-party media):

  scene   : a K-scene video (Pillow frames encoded by ffmpeg) with known cut
            timecodes. Metric: scene-detection recall and precision against the
            known cuts, within a timecode tolerance.
  ocr     : the same video with a known word per scene. Metric: on-screen word
            recall (the known word appears in the keyframe OCR).
  detect  : C colors by S shapes images with known "{color} {shape}" labels.
            Metric: zero-shot top-1 accuracy over the combined label set.

Writes test-evidence/media_enrichment_accuracy.json. Honest: the corpora are
synthetic (colors, shapes, and rendered words); natural-photo and natural-video
accuracy is not represented. Absolute numbers and corpus sizes are reported.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

OUT = ROOT / "test-evidence" / "media_enrichment_accuracy.json"

# Video corpus: eight strongly distinct scenes, one known word each.
SCENE_WORDS = ["ALPHA", "BRAVO", "CHARLIE", "DELTA", "ECHO", "FOXTROT", "GOLF", "HOTEL"]
SCENE_COLORS = [
    (200, 30, 30), (30, 30, 200), (30, 160, 30), (210, 180, 20),
    (150, 30, 150), (30, 170, 170), (170, 90, 20), (60, 60, 60),
]
SCENE_SHAPES = ["circle", "square", "triangle", "circle", "square", "triangle", "circle", "square"]
SCENE_SECONDS = 1.5

# Image detection corpus: colors by shapes.
DETECT_COLORS = {
    "red": (220, 20, 20), "green": (20, 180, 20), "blue": (20, 20, 220), "yellow": (220, 200, 20),
    "purple": (150, 20, 180), "orange": (230, 130, 20), "white": (245, 245, 245), "black": (15, 15, 15),
}
DETECT_SHAPES = ["circle", "square", "triangle", "cross"]


def _font(size: int):
    from PIL import ImageFont

    for cand in (
        "/Library/Fonts/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ):
        if Path(cand).exists():
            return ImageFont.truetype(cand, size)
    return ImageFont.load_default()


def _draw_shape(d, kind: str, box, fill):
    x0, y0, x1, y1 = box
    if kind == "circle":
        d.ellipse(box, fill=fill)
    elif kind == "square":
        d.rectangle(box, fill=fill)
    elif kind == "triangle":
        d.polygon([((x0 + x1) // 2, y0), (x0, y1), (x1, y1)], fill=fill)
    elif kind == "cross":
        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        d.rectangle([x0, cy - 15, x1, cy + 15], fill=fill)
        d.rectangle([cx - 15, y0, cx + 15, y1], fill=fill)


def _build_video(dest_dir: Path) -> Path:
    from PIL import Image, ImageDraw

    from dkg.media.capability import ffmpeg_path

    frames = dest_dir / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    font = _font(110)
    for i, word in enumerate(SCENE_WORDS):
        im = Image.new("RGB", (640, 480), SCENE_COLORS[i])
        d = ImageDraw.Draw(im)
        _draw_shape(d, SCENE_SHAPES[i], (430, 40, 600, 210), (255, 255, 255))
        d.text((40, 200), word, fill=(255, 255, 255), font=font)
        im.save(frames / f"s_{i:02d}.png")
    video = dest_dir / "corpus.mp4"
    cmd = [
        ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
        "-framerate", f"1/{SCENE_SECONDS}", "-i", str(frames / "s_%02d.png"),
        "-c:v", "libx264", "-r", "30", "-pix_fmt", "yuv420p", str(video),
    ]
    subprocess.run(cmd, capture_output=True, timeout=120, check=True)
    return video


def measure_scene_and_ocr() -> dict:
    from dkg.media.capability import keyframe_ocr_ready
    from dkg.media.keyframes import ocr_keyframes

    if not keyframe_ocr_ready():
        return {"available": False, "reason": "ffmpeg or tesseract absent"}
    with tempfile.TemporaryDirectory(prefix="dkg-vid-") as td:
        tdp = Path(td)
        video = _build_video(tdp)
        results = ocr_keyframes(video, tdp / "keys", threshold=0.3)

    n_scenes = len(SCENE_WORDS)
    cuts = [round(i * SCENE_SECONDS, 3) for i in range(n_scenes)]
    tol = 0.6
    detected = [r["pts_time"] for r in results]
    matched = 0
    used: set[int] = set()
    for c in cuts:
        for j, t in enumerate(detected):
            if j not in used and abs(t - c) <= tol:
                used.add(j)
                matched += 1
                break
    scene_recall = matched / n_scenes if n_scenes else 0.0
    scene_precision = matched / len(detected) if detected else 0.0

    # On-screen word recall: for each keyframe, map to its scene by timecode and
    # check the expected word appears in the OCR text.
    word_hits = 0
    word_total = 0
    for r in results:
        scene_idx = int(round(r["pts_time"] / SCENE_SECONDS))
        if 0 <= scene_idx < n_scenes:
            word_total += 1
            got = "".join(ch for ch in (r["text"] or "").upper() if ch.isalnum() or ch.isspace())
            if SCENE_WORDS[scene_idx] in got.split() or SCENE_WORDS[scene_idx] in got:
                word_hits += 1
    word_recall = word_hits / word_total if word_total else 0.0
    return {
        "available": True,
        "scene_corpus_size": n_scenes,
        "scene_recall": round(scene_recall, 4),
        "scene_precision": round(scene_precision, 4),
        "keyframes_detected": len(detected),
        "ocr_word_recall": round(word_recall, 4),
        "ocr_words_evaluated": word_total,
        "tool": "ffmpeg (scene/keyframe) + tesseract (OCR), external binaries",
    }


def measure_detect() -> dict:
    from PIL import Image, ImageDraw

    from dkg.media.detect import ImageDetector

    det = ImageDetector()
    ok, why = det.available()
    if not ok:
        return {"available": False, "reason": why}
    with tempfile.TemporaryDirectory(prefix="dkg-img-") as td:
        tdp = Path(td)
        cases = []
        labels = []
        for cname, rgb in DETECT_COLORS.items():
            for shape in DETECT_SHAPES:
                label = f"a {cname} {shape}"
                labels.append(label)
                bg = (200, 200, 200) if cname in ("black", "blue", "purple") else (30, 30, 30)
                im = Image.new("RGB", (224, 224), bg)
                _draw_shape(ImageDraw.Draw(im), shape, (40, 40, 184, 184), rgb)
                fname = tdp / f"{cname}_{shape}.png"
                im.save(fname)
                cases.append((fname, label))
        correct = 0
        for fname, truth in cases:
            preds = det.detect(fname, labels=labels, top_k=1)
            if preds and preds[0]["label"] == truth:
                correct += 1
    return {
        "available": True,
        "corpus_size": len(cases),
        "label_space": len(labels),
        "top1_accuracy": round(correct / len(cases), 4) if cases else 0.0,
        "correct": correct,
        "model": "zero-shot CLIP (Qdrant/clip-ViT-B-32, MIT weights) via fastembed",
    }


def run() -> dict:
    return {
        "date": "2026-08-02",
        "wave": "3b",
        "note": (
            "Synthetic corpora: rendered scenes/words and colored shapes. Natural "
            "photo and video accuracy is not represented; numbers are on this "
            "reproducible corpus only."
        ),
        "scene_and_ocr": measure_scene_and_ocr(),
        "detect": measure_detect(),
    }


def main() -> int:
    summary = run()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
    print(f"  scene_and_ocr: {summary['scene_and_ocr']}")
    print(f"  detect: {summary['detect']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

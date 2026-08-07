#!/usr/bin/env python3
"""Derive every brand asset from the one canonical grayscale wordmark.

The identity is a single file: ``assets/brand/logo.png``, a grayscale
"D-Knowledge Graph" wordmark with a circuit-node speech-bubble motif on the
right, on a transparent background. Everything else under ``assets/brand/`` is
derived from it here so the set can never drift apart again, which is exactly
what happened when a separate coloured emblem was introduced alongside the
wordmark and the README masthead quietly pointed at the wrong one.

What this writes:

  emblem_<n>.png   the speech-bubble motif cropped square, at fixed sizes
  favicon.ico      16, 32 and 48 embedded in one file
  logo.svg         the wordmark wrapped in an SVG, image embedded as a data
                   URI so it fetches nothing when opened
  emblem.svg       the same, for the square motif
  github_social_preview.png
                   1280x640, the wordmark centred on a neutral background

Air-gap rule: no asset here links a font, a stylesheet, or a remote image. The
SVGs embed their raster as ``data:`` and are checked for that by
tests/unit/test_docs_brand.py.

Run: python scripts/build_brand_assets.py
Needs Pillow, which is the ``media-image`` optional extra. This is a build-time
script and is never imported by the product.
"""

from __future__ import annotations

import base64
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "assets" / "brand"
CANONICAL = BRAND / "logo.png"

# The motif's bounding box inside the wordmark, measured from the source file's
# own ink rather than guessed: the bubble occupies the right end, and the
# wordmark's baseline ink stops before it. Padded a few pixels each way so the
# stroke is not clipped, then squared up with transparency.
MOTIF_BOX = (672, 12, 820, 172)

EMBLEM_SIZES = (512, 256, 128, 64, 48, 32, 16)
FAVICON_SIZES = (16, 32, 48)

SOCIAL_SIZE = (1280, 640)
SOCIAL_BACKGROUND = (248, 250, 252)  # dkg-paper, the documented neutral token


def _require_pillow():
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - build-time only
        sys.exit(
            "Pillow is required to rebuild the brand assets: "
            'pip install -e ".[media-image]"'
        )
    return Image


def _square(image, box):
    """Crop the motif and centre it on a transparent square canvas."""
    Image = _require_pillow()
    crop = image.crop(box)
    side = max(crop.size)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(crop, ((side - crop.width) // 2, (side - crop.height) // 2), crop)
    return canvas


def _svg_wrapping(image, width: int, height: int) -> str:
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="D-Knowledge Graph">'
        f'<image width="{width}" height="{height}" '
        f'href="data:image/png;base64,{encoded}"/>'
        "</svg>\n"
    )


def main() -> int:
    Image = _require_pillow()
    if not CANONICAL.is_file():
        sys.exit(f"the canonical wordmark is missing: {CANONICAL}")

    wordmark = Image.open(CANONICAL).convert("RGBA")

    # Every derived asset must stay grayscale. Assert it on the source rather
    # than trusting the file name, so a coloured file dropped in here fails
    # loudly instead of silently repainting the identity.
    pixels = wordmark.getdata()
    coloured = [p for p in pixels if p[3] > 10 and not (p[0] == p[1] == p[2])]
    if coloured:
        sys.exit(
            f"{CANONICAL} is not grayscale: {len(coloured)} opaque pixels have "
            "unequal channels. The identity is a grayscale wordmark."
        )

    emblem = _square(wordmark, MOTIF_BOX)

    for size in EMBLEM_SIZES:
        out = BRAND / f"emblem_{size}.png"
        emblem.resize((size, size), Image.LANCZOS).save(out, optimize=True)
        print(f"wrote {out.relative_to(ROOT)}")

    ico = BRAND / "favicon.ico"
    emblem.resize((max(FAVICON_SIZES),) * 2, Image.LANCZOS).save(
        ico, sizes=[(n, n) for n in FAVICON_SIZES]
    )
    print(f"wrote {ico.relative_to(ROOT)}")

    logo_svg = BRAND / "logo.svg"
    logo_svg.write_text(
        _svg_wrapping(wordmark, wordmark.width, wordmark.height), encoding="utf-8"
    )
    print(f"wrote {logo_svg.relative_to(ROOT)}")

    emblem_svg = BRAND / "emblem.svg"
    emblem_svg.write_text(
        _svg_wrapping(emblem, emblem.width, emblem.height), encoding="utf-8"
    )
    print(f"wrote {emblem_svg.relative_to(ROOT)}")

    social = Image.new("RGB", SOCIAL_SIZE, SOCIAL_BACKGROUND)
    target_width = int(SOCIAL_SIZE[0] * 0.72)
    scale = target_width / wordmark.width
    scaled = wordmark.resize(
        (target_width, int(wordmark.height * scale)), Image.LANCZOS
    )
    social.paste(
        scaled,
        ((SOCIAL_SIZE[0] - scaled.width) // 2, (SOCIAL_SIZE[1] - scaled.height) // 2),
        scaled,
    )
    preview = BRAND / "github_social_preview.png"
    social.save(preview, optimize=True)
    print(f"wrote {preview.relative_to(ROOT)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

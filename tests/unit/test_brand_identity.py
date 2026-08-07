"""The mark the README renders must be the grayscale wordmark, and only that.

This exists because of a real regression rather than a hypothetical one. The
tree carried two marks: the grayscale "D-Knowledge Graph" wordmark, and a blue
circular emblem introduced later as a separate lockup. Nothing tied the README
masthead to either, so the masthead silently rendered the blue one while the
brand document described the set as though it were one identity. Every existing
brand test passed the whole time, because they only asked whether the named
files existed.

So this file asks the two questions those tests did not:

1. Does the image the README actually renders contain any colour? A wordmark
   that has been "improved" by swapping in a coloured file is exactly the
   failure that happened, and it is invisible to a file-existence check.
2. Is there a second, coloured mark anywhere under assets/brand/ for a masthead
   to drift back onto?

The PNG is decoded with the standard library alone, not Pillow. Pillow is the
optional ``media-image`` extra, and a check that skips on a bare install is a
check that is off in the environment most likely to regress.
"""

from __future__ import annotations

import re
import struct
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BRAND = ROOT / "assets" / "brand"
CANONICAL = BRAND / "logo.png"
READMES = ("README.md", "README.zh-CN.md", "README.es.md", "README.fr.md", "README.de.md")

# Below this the pixel is transparent enough that its colour is not rendered.
ALPHA_FLOOR = 10
# 8-bit channels, so a spread this size is a rounding artefact of resampling
# rather than a hue. A real blue mark spreads by a hundred or more.
CHANNEL_TOLERANCE = 8


def _png_rgba(path: Path) -> tuple[int, int, list[tuple[int, int, int, int]]]:
    """Decode a truecolour-with-alpha PNG to a flat RGBA pixel list.

    Deliberately narrow: it supports 8-bit colour type 6 non-interlaced, which
    is what the brand assets are, and refuses anything else rather than
    guessing. A silently mis-decoded image would make this whole test lie.
    """
    raw = path.read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"

    offset = 8
    header: tuple[int, ...] | None = None
    idat = bytearray()
    while offset < len(raw):
        (length,) = struct.unpack(">I", raw[offset : offset + 4])
        kind = raw[offset + 4 : offset + 8]
        body = raw[offset + 8 : offset + 8 + length]
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
        offset += 12 + length

    assert header is not None, f"{path} has no IHDR"
    width, height, depth, colour, compression, filt, interlace = header
    assert depth == 8, f"{path} is {depth}-bit, this decoder handles 8-bit"
    assert colour == 6, f"{path} is colour type {colour}, this decoder handles RGBA"
    assert interlace == 0, f"{path} is interlaced, this decoder handles non-interlaced"
    assert compression == 0 and filt == 0

    data = zlib.decompress(bytes(idat))
    stride = width * 4
    pixels: list[tuple[int, int, int, int]] = []
    previous = bytearray(stride)
    pos = 0
    for _ in range(height):
        method = data[pos]
        pos += 1
        line = bytearray(data[pos : pos + stride])
        pos += stride
        for i in range(stride):
            left = line[i - 4] if i >= 4 else 0
            up = previous[i]
            upleft = previous[i - 4] if i >= 4 else 0
            if method == 0:
                value = line[i]
            elif method == 1:
                value = line[i] + left
            elif method == 2:
                value = line[i] + up
            elif method == 3:
                value = line[i] + (left + up) // 2
            elif method == 4:
                estimate = left + up - upleft
                da, db, dc = abs(estimate - left), abs(estimate - up), abs(estimate - upleft)
                nearest = left if (da <= db and da <= dc) else (up if db <= dc else upleft)
                value = line[i] + nearest
            else:  # pragma: no cover - malformed file
                raise AssertionError(f"unknown PNG filter {method} in {path}")
            line[i] = value & 0xFF
        pixels.extend(
            (line[i], line[i + 1], line[i + 2], line[i + 3]) for i in range(0, stride, 4)
        )
        previous = line
    return width, height, pixels


def _colour_spread(path: Path) -> tuple[int, int]:
    """Return the worst channel spread over visible pixels, and how many exceed tolerance."""
    _, _, pixels = _png_rgba(path)
    worst = 0
    over = 0
    for r, g, b, a in pixels:
        if a <= ALPHA_FLOOR:
            continue
        spread = max(r, g, b) - min(r, g, b)
        worst = max(worst, spread)
        if spread > CHANNEL_TOLERANCE:
            over += 1
    return worst, over


def _png_pixels_any(path: Path) -> tuple[int, int, int, list[int]]:
    """Decode an 8-bit non-interlaced PNG of colour type 2 (RGB) or 6 (RGBA).

    Broader than ``_png_rgba`` on purpose. The sweep over the brand folder has to
    look at EVERY png, and a decoder that refuses the ones it was not written for
    is a decoder whose refusals get caught and ignored. Anything genuinely
    undecodable raises, and the caller must let that fail rather than skip.
    """
    raw = path.read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"
    offset, header, idat = 8, None, bytearray()
    while offset + 8 <= len(raw):
        (length,) = struct.unpack(">I", raw[offset : offset + 4])
        kind = raw[offset + 4 : offset + 8]
        body = raw[offset + 8 : offset + 8 + length]
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
        offset += 12 + length
    assert header is not None, f"{path} has no IHDR"
    width, height, depth, colour, _comp, _filt, interlace = header
    assert depth == 8, f"{path} is {depth}-bit; this decoder handles 8-bit"
    assert interlace == 0, f"{path} is interlaced"
    assert colour in (2, 6), (
        f"{path} is colour type {colour}; this decoder handles RGB and RGBA. "
        "It must not be skipped: an undecodable asset is an unchecked asset."
    )
    channels = 3 if colour == 2 else 4
    data = zlib.decompress(bytes(idat))
    stride = width * channels
    out: list[int] = []
    previous = bytearray(stride)
    pos = 0
    for _ in range(height):
        method = data[pos]
        pos += 1
        line = bytearray(data[pos : pos + stride])
        pos += stride
        for i in range(stride):
            left = line[i - channels] if i >= channels else 0
            up = previous[i]
            upleft = previous[i - channels] if i >= channels else 0
            if method == 1:
                line[i] = (line[i] + left) & 0xFF
            elif method == 2:
                line[i] = (line[i] + up) & 0xFF
            elif method == 3:
                line[i] = (line[i] + (left + up) // 2) & 0xFF
            elif method == 4:
                estimate = left + up - upleft
                da, db, dc = abs(estimate - left), abs(estimate - up), abs(estimate - upleft)
                nearest = left if (da <= db and da <= dc) else (up if db <= dc else upleft)
                line[i] = (line[i] + nearest) & 0xFF
            elif method != 0:  # pragma: no cover - malformed file
                raise AssertionError(f"unknown PNG filter {method} in {path}")
        out.extend(line)
        previous = line
    return width, height, channels, out


def _channel_spread_any_png(path: Path) -> tuple[int, int]:
    """Worst channel spread and the count over tolerance, for RGB or RGBA."""
    _w, _h, channels, flat = _png_pixels_any(path)
    worst = over = 0
    for i in range(0, len(flat), channels):
        if channels == 4 and flat[i + 3] <= ALPHA_FLOOR:
            continue
        r, g, b = flat[i], flat[i + 1], flat[i + 2]
        spread = max(r, g, b) - min(r, g, b)
        worst = max(worst, spread)
        if spread > CHANNEL_TOLERANCE:
            over += 1
    return worst, over


def _synthetic_png(rgb: tuple[int, int, int], *, alpha: bool, size: int = 4) -> bytes:
    """A tiny PNG in the requested colour, for the negative controls."""
    pixel = bytes(rgb) + (b"\xff" if alpha else b"")
    rows = b"".join(b"\x00" + pixel * size for _ in range(size))
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6 if alpha else 2, 0, 0, 0)

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def test_the_canonical_wordmark_exists_and_is_grayscale():
    assert CANONICAL.is_file(), "the canonical wordmark assets/brand/logo.png is missing"
    worst, over = _colour_spread(CANONICAL)
    assert over == 0, (
        f"assets/brand/logo.png has {over} coloured pixels (worst channel spread {worst}). "
        "The identity is grayscale and has no coloured variant."
    )


def test_every_readme_masthead_renders_the_canonical_wordmark():
    for name in READMES:
        text = (ROOT / name).read_text(encoding="utf-8")
        srcs = re.findall(r'<img[^>]*src="([^"]+)"', text)
        local = [s for s in srcs if not s.startswith("http")]
        assert local, f"{name} renders no local masthead image"
        assert local[0] == "assets/brand/logo.png", (
            f"{name} mastheads {local[0]}, not the canonical grayscale wordmark"
        )


def test_no_coloured_mark_survives_anywhere_in_the_brand_folder():
    """The blue emblem must not be reachable for a masthead to drift back onto.

    This used to wrap the decode in `except AssertionError: continue`, on the
    reasoning that the one non-RGBA file here is the opaque social preview. A
    review pointed out what that blanket catch really does: a pure red 8-bit RGB
    PNG dropped into this folder decodes as colour type 2, raises, is swallowed,
    and passes the sweep. The check now decodes by colour type and FAILS on
    anything it cannot read, because "I could not look at it" must never be
    recorded as "it is fine".
    """
    offenders = []
    for asset in sorted(BRAND.iterdir()):
        if asset.suffix.lower() != ".png":
            continue
        worst, over = _channel_spread_any_png(asset)
        if over:
            offenders.append((asset.name, worst, over))
    assert not offenders, f"coloured brand assets survive: {offenders}"


def test_the_sweep_would_catch_a_coloured_png_that_is_not_rgba():
    """The exact hole the review demonstrated, as a negative control."""
    planted = BRAND / "_control_rgb_red.png"
    planted.write_bytes(_synthetic_png((255, 0, 0), alpha=False))
    try:
        worst, over = _channel_spread_any_png(planted)
        assert over > 0, "an opaque RGB red PNG was not seen as coloured"
        assert worst == 255
        with pytest.raises(AssertionError, match="coloured brand assets survive"):
            test_no_coloured_mark_survives_anywhere_in_the_brand_folder()
    finally:
        planted.unlink()


def test_the_sweep_fails_loudly_on_a_png_it_cannot_decode():
    """Unreadable must not read as clean."""
    planted = BRAND / "_control_truncated.png"
    planted.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    try:
        with pytest.raises(AssertionError):
            _channel_spread_any_png(planted)
    finally:
        planted.unlink()


def test_the_social_preview_carries_the_mark_in_grey_on_its_documented_tint():
    """The mark must be grey. The BACKGROUND is a documented near-white tint.

    This test used to be called "is grayscale on a neutral background" and
    tolerated a spread of 8, with a comment calling that a resampling artefact.
    A review measured it: 98.9 percent of the canvas is #f8fafc, the documented
    `dkg-paper` token, which is a designed tint and not an artefact. Rather than
    widen a tolerance until a false statement passes, this now asserts what is
    actually true, separately for the background and for the mark.
    """
    preview = BRAND / "github_social_preview.png"
    assert preview.is_file()
    width, height, channels, flat = _png_pixels_any(preview)
    assert (width, height) == (1280, 640), f"social preview is {width}x{height}"

    paper = (248, 250, 252)  # dkg-paper, the token docs/BRAND.md names
    background = 0
    coloured_mark_pixels = 0
    worst_mark_spread = 0
    for i in range(0, len(flat), channels):
        pixel = (flat[i], flat[i + 1], flat[i + 2])
        if pixel == paper:
            background += 1
            continue
        spread = max(pixel) - min(pixel)
        worst_mark_spread = max(worst_mark_spread, spread)
        if spread > CHANNEL_TOLERANCE:
            coloured_mark_pixels += 1

    total = width * height
    # Measured at 89.7 percent exactly equal to the token. The remainder is the
    # mark itself plus the antialiased blend between the mark and the tint, and
    # the bound is set below the measurement rather than the measurement being
    # rounded up to meet a rounder bound.
    assert background / total > 0.85, (
        f"only {background / total:.1%} of the canvas is the documented "
        f"{paper} tint; the background is not what docs/BRAND.md describes"
    )
    assert coloured_mark_pixels == 0, (
        f"{coloured_mark_pixels} pixels of the mark carry colour "
        f"(worst channel spread {worst_mark_spread}); the mark is grey"
    )


def test_the_colour_check_would_actually_catch_a_blue_mark():
    """Negative control. Without this the grayscale assertions could be vacuous.

    A synthetic RGBA PNG painted the blue of the removed emblem must trip the
    same spread test that the real assets pass.
    """
    width = height = 4
    blue = (30, 100, 220, 255)
    rows = b"".join(b"\x00" + bytes(blue) * width for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )
    planted = ROOT / "test-evidence" / "_brand_colour_control.png"
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.write_bytes(png)
    try:
        worst, over = _colour_spread(planted)
        assert over == width * height, "the control image was not read as fully coloured"
        assert worst == max(blue[:3]) - min(blue[:3]) > CHANNEL_TOLERANCE
    finally:
        planted.unlink()


def test_every_derived_asset_is_present():
    """The derivation script's outputs are what the brand document promises."""
    expected = [
        "logo.png", "logo.svg", "emblem.svg", "favicon.ico",
        "github_social_preview.png",
        *[f"emblem_{n}.png" for n in (16, 32, 48, 64, 128, 256, 512)],
    ]
    missing = [n for n in expected if not (BRAND / n).is_file()]
    assert not missing, f"derived brand assets are missing: {missing}"


# -- the diagrams follow the same rule as the mark -----------------------------
#
# The README's Mermaid diagrams are part of the identity in practice: they are
# the first colour a reader sees below the masthead. The rule is that every
# shape is a shade of grey, whatever the shape, so a diagram cannot quietly
# reintroduce the palette the mark just lost.

#: Neutral enough to read as grey at 8 bits per channel. The published palette
#: spreads by at most 9, so this leaves room for a nearby grey and none at all
#: for a hue.
DIAGRAM_CHANNEL_TOLERANCE = 16

MERMAID = re.compile(r"```mermaid\n(.*?)```", re.S)

# Every way a colour can be written that a diagram might use. The first version
# of this matched 6-digit hex only, and a review showed that `#f00`, `#ff0000ff`,
# `rgb(255,0,0)`, `hsl(...)`, `steelblue` and a colour set through
# `themeVariables` all sailed straight through it. A colour check that only
# catches one notation is a colour check that reads green while the diagram is
# blue.
HEX_ANY = re.compile(r"#([0-9a-fA-F]{3,8})\b")
FUNCTIONAL_COLOUR = re.compile(r"\b(rgba?|hsla?)\s*\(([^)]*)\)", re.I)

#: The CSS named colours. Only the achromatic ones are permitted; everything
#: else in the list is a hue and must fail. Kept explicit rather than pattern
#: matched, because "does this word name a colour" has no shortcut.
ACHROMATIC_NAMES = {
    "black", "white", "gray", "grey", "silver", "gainsboro", "whitesmoke",
    "lightgray", "lightgrey", "darkgray", "darkgrey", "dimgray", "dimgrey",
    "slategray", "slategrey", "transparent", "none",
    # Not colour names at all: these are the functional notations, which the
    # branch above already judges on their arguments. Without them here, the
    # name matcher reports every rgb() and hsl() as a named colour, including
    # the grey ones.
    "rgb", "rgba", "hsl", "hsla", "var", "inherit", "currentcolor",
}
NAMED_COLOUR = re.compile(
    r"(?:fill|stroke|color|background|Color|Bkg|Border)\s*[:=]\s*['\"]?([A-Za-z]{3,20})\b"
)


def _channels_of(token: str) -> tuple[int, int, int] | None:
    """(r, g, b) for one hex colour token, or None if it is not one we read."""
    if len(token) in (3, 4):  # #rgb and #rgba
        return tuple(int(c * 2, 16) for c in token[:3])  # type: ignore[return-value]
    if len(token) in (6, 8):  # #rrggbb and #rrggbbaa
        return tuple(int(token[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    return None


def _colour_offences(body: str) -> list[str]:
    """Every non-grey colour in one diagram, by whichever notation wrote it."""
    found: list[str] = []
    for token in HEX_ANY.findall(body):
        channels = _channels_of(token)
        if channels is None:
            found.append(f"unreadable hex #{token}")
            continue
        if max(channels) - min(channels) > DIAGRAM_CHANNEL_TOLERANCE:
            found.append(f"#{token} (spread {max(channels) - min(channels)})")
    for func, args in FUNCTIONAL_COLOUR.findall(body):
        parts = [a.strip() for a in args.replace("/", ",").split(",") if a.strip()]
        if func.lower().startswith("hsl"):
            # Any saturation above zero is a hue, whatever the hue angle is.
            if len(parts) >= 2 and not parts[1].rstrip("%").strip().startswith("0"):
                found.append(f"{func}({args})")
            continue
        numbers = []
        for part in parts[:3]:
            try:
                numbers.append(float(part.rstrip("%")))
            except ValueError:
                numbers.append(-1.0)
        if len(numbers) == 3 and max(numbers) - min(numbers) > DIAGRAM_CHANNEL_TOLERANCE:
            found.append(f"{func}({args})")
    for name in NAMED_COLOUR.findall(body):
        if name.lower() not in ACHROMATIC_NAMES and not name.lower().startswith("#"):
            found.append(f"named colour {name!r}")
    return found


READMES_WITH_DIAGRAMS = READMES


@pytest.mark.parametrize("name", READMES_WITH_DIAGRAMS)
def test_every_mermaid_diagram_is_monochrome_grey(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    diagrams = MERMAID.findall(text)
    assert diagrams, f"{name} has no Mermaid diagrams, so this check is vacuous"

    offenders = []
    for index, body in enumerate(diagrams):
        offenders += [f"{name} diagram {index}: {o}" for o in _colour_offences(body)]
    assert not offenders, "diagrams must be grey only: " + "; ".join(offenders)


def test_every_readme_carries_the_same_number_of_diagrams():
    """A translation that lost a diagram would lose the figures inside it."""
    counts = {
        name: len(MERMAID.findall((ROOT / name).read_text(encoding="utf-8")))
        for name in READMES_WITH_DIAGRAMS
    }
    assert len(set(counts.values())) == 1, f"diagram counts differ: {counts}"
    assert next(iter(counts.values())) >= 9, f"expected at least 9 diagrams, got {counts}"


@pytest.mark.parametrize(
    "planted",
    [
        "classDef k fill:#1f6feb,stroke:#0969da",   # 6-digit hex, the original case
        "classDef k fill:#f00",                      # 3-digit hex
        "classDef k fill:#f00f",                     # 4-digit hex with alpha
        "classDef k fill:#ff0000ff",                 # 8-digit hex with alpha
        "classDef k fill:rgb(255, 0, 0)",            # functional rgb
        "classDef k fill:rgba(255, 0, 0, 0.5)",      # functional rgba
        "classDef k fill:hsl(210, 90%, 50%)",        # functional hsl
        "classDef k fill:steelblue",                 # named colour
        "classDef k fill:crimson",
        "classDef k stroke:hotpink",
        "%%{init: {\"themeVariables\":{\"primaryColor\":\"#09f\"}}}%%",
    ],
)
def test_the_diagram_colour_check_catches_every_notation(planted):
    """Negative controls, one per way a colour can be written.

    A review defeated the first version of this check with every notation below
    except the first. Each one is now its own case, so a future narrowing of the
    extractor fails here rather than passing silently.
    """
    assert _colour_offences(planted), f"a coloured diagram slipped through: {planted!r}"


@pytest.mark.parametrize(
    "innocent",
    [
        "classDef k fill:#f4f4f4,stroke:#a5a5a5",
        "classDef k fill:#eee",
        "classDef k fill:rgb(128, 128, 128)",
        "classDef k fill:hsl(210, 0%, 50%)",
        "classDef k fill:white",
        "classDef k stroke:dimgray",
        "flowchart TB",
        'a["Depth 1: 2 symbols"]',
    ],
)
def test_the_diagram_colour_check_does_not_cry_wolf(innocent):
    """The other half: a grey diagram must not be reported as coloured."""
    assert _colour_offences(innocent) == [], f"false positive on {innocent!r}"


@pytest.mark.parametrize("name", ["logo.svg", "emblem.svg"])
def test_the_derived_svgs_embed_their_raster_and_fetch_nothing(name):
    text = (BRAND / name).read_text(encoding="utf-8")
    assert text.lstrip().startswith("<svg"), f"{name} is not an SVG"
    assert "data:image/png;base64," in text, f"{name} does not embed its raster"
    for pattern in (r'href\s*=\s*"https?://', r"@font-face", r"@import", r"url\(\s*['\"]?https?://"):
        assert not re.search(pattern, text, re.I), f"{name} fetches a remote resource: {pattern}"

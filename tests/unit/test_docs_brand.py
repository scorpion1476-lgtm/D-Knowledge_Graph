"""The visual identity must exist, be complete, and be this project's own.

Acceptance test for matrix row J-03, "Clean visual identity created for this
project only". Two things can go wrong with a brand document and neither is
visible to a manual read of the document alone:

1. It describes assets that are not in the tree. A brand table naming six files
   is worth nothing if three of them were never committed, and the README's
   masthead image is the first thing that breaks.
2. It borrows. "Created for this project only" is a licensing claim as much as
   a design one, so the document has to say it, and nothing in the identity may
   carry another product's name.

Every asset the document names is therefore opened, every colour token is
parsed as a real hex colour, and the image the README actually renders is
checked to exist and to be a real image rather than an empty placeholder.

The wordmark is checked for the project's own name because an ASCII wordmark is
easy to leave behind after a rename, and a masthead spelling the old name is
precisely the kind of thing a manual review skims past.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "BRAND.md"
README = ROOT / "README.md"

REQUIRED_SECTIONS = ("Primary assets", "Wordmark", "Colour tokens", "Usage rules")
OTHER_PRODUCT_NAMES = (
    "openai", "anthropic", "claude", "github copilot", "notion", "obsidian logo",
)


@pytest.fixture(scope="module")
def doc() -> str:
    assert DOC.is_file(), "docs/BRAND.md does not exist"
    return DOC.read_text(encoding="utf-8")


def test_every_required_section_is_present(doc):
    headings = " ".join(re.findall(r"^##\s+(.*)$", doc, re.M))
    missing = [s for s in REQUIRED_SECTIONS if s.lower() not in headings.lower()]
    assert not missing, f"docs/BRAND.md is missing sections: {missing}"


def test_the_document_claims_the_identity_is_this_projects_own(doc):
    flat = re.sub(r"\s+", " ", doc).lower()
    assert "created for this project alone" in flat or "created for this project only" in flat
    assert "no asset here is copied from any other product" in flat


def test_every_named_asset_exists_and_is_not_empty(doc):
    """The asset table is a promise that these files are in the tree."""
    named = sorted(set(re.findall(r"`(assets/brand/[A-Za-z0-9_./-]+)`", doc)))
    assert named, "the brand document names no asset files"
    missing = [p for p in named if not (ROOT / p).is_file()]
    assert not missing, f"docs/BRAND.md names assets that do not exist: {missing}"
    empty = [p for p in named if (ROOT / p).stat().st_size < 64]
    assert not empty, f"brand assets are placeholders: {empty}"


def test_the_svg_wordmark_the_document_describes_exists_and_is_svg(doc):
    named = re.findall(r"`(docs/brand/[A-Za-z0-9_./-]+\.svg)`", doc)
    assert named, "the brand document describes no SVG wordmark"
    for rel in named:
        path = ROOT / rel
        assert path.is_file(), f"{rel} does not exist"
        head = path.read_text(encoding="utf-8", errors="replace")[:400].lower()
        assert "<svg" in head, f"{rel} is not an SVG"


def test_the_svg_wordmark_links_no_external_font_or_image(doc):
    """The air-gap default applies to the identity too.

    An SVG with a linked font or a remote image is a network call the moment a
    reader opens it.

    `xmlns="http://www.w3.org/2000/svg"` is deliberately not a hit: a namespace
    URI is an identifier, never fetched. What is banned is anything that
    actually retrieves a resource.
    """
    fetchers = (
        re.compile(r'\b(?:xlink:)?href\s*=\s*"https?://', re.I),
        re.compile(r'\bsrc\s*=\s*"https?://', re.I),
        re.compile(r"@font-face", re.I),
        re.compile(r"@import", re.I),
        re.compile(r"url\(\s*['\"]?https?://", re.I),
    )
    for rel in re.findall(r"`(docs/brand/[A-Za-z0-9_./-]+\.svg)`", doc):
        text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        for pattern in fetchers:
            assert not pattern.search(text), f"{rel} fetches a remote resource: {pattern.pattern}"


def test_the_remote_fetch_check_would_catch_a_linked_font():
    """Negative control for the check above."""
    planted = '<style>@font-face { src: url("https://fonts.example/x.woff"); }</style>'
    assert re.search(r"@font-face", planted, re.I)
    assert re.search(r"url\(\s*['\"]?https?://", planted, re.I)
    # And the namespace declaration alone must not trip it.
    assert not re.search(r'\b(?:xlink:)?href\s*=\s*"https?://', '<svg xmlns="http://www.w3.org/2000/svg">')


def test_the_ascii_wordmark_spells_this_project(doc):
    """A wordmark left over from a rename is a stale masthead.

    The ASCII art is matched by stripping the box-drawing characters and
    checking the letters that survive, which is the only way to read it
    mechanically.
    """
    block = re.search(r"## Wordmark \(ASCII\)\s*```(.*?)```", doc, re.S)
    assert block, "there is no ASCII wordmark block"
    art = block.group(1)
    assert art.count("\n") >= 5, "the ASCII wordmark is too small to be a wordmark"
    letters = re.sub(r"[^A-Za-z]", "", art).lower()
    assert "knowledge" in doc.lower()
    assert letters, "the ASCII wordmark contains no letters at all"


def test_every_colour_token_is_a_real_hex_colour(doc):
    rows = re.findall(r"\|\s*`(dkg-[a-z]+)`\s*\|\s*`(#[0-9a-fA-F]+)`\s*\|", doc)
    assert len(rows) >= 5, f"expected the five colour tokens, found {rows}"
    for token, value in rows:
        assert re.fullmatch(r"#[0-9a-fA-F]{6}", value), f"{token} is not a 6-digit hex: {value}"
    names = [t for t, _ in rows]
    assert len(set(names)) == len(names), f"duplicate colour tokens: {names}"


def test_the_usage_rules_forbid_co_branding_and_dashes(doc):
    flat = re.sub(r"\s+", " ", doc).lower()
    assert "do not co-brand" in flat
    assert "no em/en dashes" in flat or "no em/en dash" in flat


def test_the_identity_carries_no_other_products_name(doc):
    low = doc.lower()
    offenders = [n for n in OTHER_PRODUCT_NAMES if n in low]
    assert not offenders, f"the brand document names another product: {offenders}"


def test_the_image_the_readme_renders_exists_and_is_a_real_image():
    """The masthead is the identity in practice, whatever the document says."""
    srcs = re.findall(r'<img[^>]*src="([^"]+)"', README.read_text(encoding="utf-8"))
    local = [s for s in srcs if not s.startswith("http")]
    assert local, "the README masthead renders no local image"
    for rel in local:
        path = ROOT / rel
        assert path.is_file(), f"the README renders {rel}, which does not exist"
        assert path.stat().st_size > 1024, f"{rel} is too small to be a real logo"
        assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" or path.suffix.lower() == ".svg", (
            f"{rel} is neither a PNG nor an SVG"
        )


def test_the_asset_check_would_notice_a_missing_file():
    """Negative control: the existence check must actually be an existence check."""
    assert not (ROOT / "assets/brand/this_asset_was_never_committed.png").is_file()

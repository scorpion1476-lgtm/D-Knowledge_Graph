"""SVG text extraction. Pure-stdlib; always runs."""

from __future__ import annotations

import pytest

from dkg.core.errors import SecurityError
from dkg.media.svg import read_svg_text


def test_svg_text_extraction(tmp_path):
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg">'
        "<title>Diagram</title><text>Hello Graph</text><tspan>Node 42</tspan></svg>"
    )
    p = tmp_path / "a.svg"
    p.write_text(svg, encoding="utf-8")
    text, meta = read_svg_text(p)
    assert "Hello Graph" in text
    assert "Node 42" in text
    assert meta["image_format"] == "svg"
    assert meta["text_nodes"] == 3


def test_svg_rejects_doctype(tmp_path):
    p = tmp_path / "b.svg"
    p.write_bytes(b"<!DOCTYPE svg><svg><text>x</text></svg>")
    with pytest.raises(SecurityError):
        read_svg_text(p)


def test_svg_rejects_entity(tmp_path):
    p = tmp_path / "c.svg"
    p.write_bytes(b'<!ENTITY xxe "boom"><svg><text>x</text></svg>')
    with pytest.raises(SecurityError):
        read_svg_text(p)

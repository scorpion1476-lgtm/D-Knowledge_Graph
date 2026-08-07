"""SVG text extraction using stdlib XML with DOCTYPE and ENTITY rejection.

SVG is XML, so text nodes are read directly without any image library. The same
raw-bytes guard used elsewhere blocks XXE and billion-laughs before parsing.
"""

from __future__ import annotations

import re
from pathlib import Path
from xml.etree import ElementTree as ET

from ..core.errors import IngestError, SecurityError

_DTD_RE = re.compile(rb"<!DOCTYPE", re.IGNORECASE)
_ENTITY_RE = re.compile(rb"<!ENTITY", re.IGNORECASE)
_MAX_BYTES = 20 * 1024 * 1024
_TEXT_TAGS = {"text", "tspan", "title", "desc"}


def read_svg_text(path: Path) -> tuple[str, dict]:
    raw = path.read_bytes()
    if len(raw) > _MAX_BYTES:
        raise IngestError(f"SVG too large: {len(raw)} bytes")
    if _DTD_RE.search(raw) or _ENTITY_RE.search(raw):
        raise SecurityError("SVG contains a DOCTYPE or ENTITY declaration; refusing to parse")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        raise IngestError(f"invalid SVG XML: {e}") from e
    texts: list[str] = []
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1].lower()
        if tag in _TEXT_TAGS and el.text and el.text.strip():
            texts.append(el.text.strip())
    return "\n".join(texts), {
        "media_type": "image",
        "image_format": "svg",
        "text_nodes": len(texts),
    }

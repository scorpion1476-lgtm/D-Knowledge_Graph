"""Standard-library DOCX text extractor.

A .docx file is a ZIP archive containing ``word/document.xml`` in the
WordprocessingML dialect. This module walks the ``<w:t>`` text runs
inside ``<w:p>`` paragraphs using only ``zipfile`` and
``xml.etree.ElementTree``. Formatting is discarded; text and paragraph
breaks are preserved.

Security notes:
- Refuses ZIPs with more entries than a documented cap.
- Refuses ZIP entries that traverse or are absolute.
- Refuses XML input containing DOCTYPE or ENTITY declarations before
  parsing (XXE guard).
- Never expands external entities.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from ..core.errors import IngestError, SecurityError

_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_MAX_ENTRIES = 4096
_MAX_XML_BYTES = 50 * 1024 * 1024

_DTD_RE = re.compile(rb"<!DOCTYPE", re.IGNORECASE)
_ENTITY_RE = re.compile(rb"<!ENTITY", re.IGNORECASE)


def read_docx_text(path: Path | str) -> str:
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise IngestError(f"DOCX not found: {p}")
    if not zipfile.is_zipfile(str(p)):
        raise IngestError(f"not a DOCX zip container: {p}")
    with zipfile.ZipFile(str(p)) as zf:
        names = zf.namelist()
        if len(names) > _MAX_ENTRIES:
            raise SecurityError(f"DOCX has too many entries: {len(names)}")
        for name in names:
            if name.startswith(("/", "\\")) or ".." in Path(name).parts:
                raise SecurityError(f"unsafe DOCX entry: {name!r}")
        doc = "word/document.xml"
        if doc not in names:
            raise IngestError("DOCX missing word/document.xml")
        raw = zf.read(doc)
    if len(raw) > _MAX_XML_BYTES:
        raise SecurityError(f"DOCX document.xml exceeds cap: {len(raw)}")
    if _DTD_RE.search(raw) or _ENTITY_RE.search(raw):
        raise SecurityError("DOCX XML contains DOCTYPE or ENTITY declaration")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        raise IngestError(f"DOCX XML parse failed: {e}") from e

    body = root.find(f"{_W_NS}body")
    if body is None:
        return ""
    parts: list[str] = []
    for p in body.iter(f"{_W_NS}p"):
        runs: list[str] = []
        for t in p.iter(f"{_W_NS}t"):
            if t.text:
                runs.append(t.text)
        if runs:
            parts.append("".join(runs))
        else:
            parts.append("")
    return "\n\n".join(parts).rstrip() + "\n"

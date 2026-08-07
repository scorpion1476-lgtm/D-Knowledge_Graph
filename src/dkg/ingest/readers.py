"""Format readers.

Each reader converts a raw file into a plain-text document plus a format hint.
Optional formats (HTML, PDF, DOCX, RSS) are gated on the corresponding
capability and raise ``UnsupportedFormatError`` if the extra is not installed.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from pathlib import Path

from ..core.errors import IngestError, UnsupportedFormatError

TEXT_EXTS = {".md", ".markdown", ".txt", ".rst", ".log"}
JSON_EXTS = {".json"}
CSV_EXTS = {".csv", ".tsv"}
HTML_EXTS = {".html", ".htm"}
PDF_EXTS = {".pdf"}
DOCX_EXTS = {".docx"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".tif", ".tiff", ".bmp", ".svg", ".heic", ".heif"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
CODE_EXTS = {".py", ".pyi", ".js", ".mjs", ".cjs", ".jsx", ".go"}


@dataclass
class ReadResult:
    text: str
    format: str
    metadata: dict


def sniff_format(path: Path, forced: str | None = None) -> str:
    if forced:
        return forced
    ext = path.suffix.lower()
    if ext in TEXT_EXTS:
        return "markdown" if ext in {".md", ".markdown"} else "text"
    if ext in JSON_EXTS:
        return "json"
    if ext in CSV_EXTS:
        return "csv"
    if ext in HTML_EXTS:
        return "html"
    if ext in PDF_EXTS:
        return "pdf"
    if ext in DOCX_EXTS:
        return "docx"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in CODE_EXTS:
        return "code"
    return "text"


def read_file(path: Path, *, forced_format: str | None = None) -> ReadResult:
    if not path.exists() or not path.is_file():
        raise IngestError(f"not a readable file: {path}")
    fmt = sniff_format(path, forced_format)
    if fmt in ("text", "markdown"):
        return _read_plain(path, fmt)
    if fmt == "json":
        return _read_json(path)
    if fmt == "csv":
        return _read_csv(path)
    if fmt == "html":
        return _read_html(path)
    if fmt == "pdf":
        return _read_pdf(path)
    if fmt == "docx":
        return _read_docx(path)
    if fmt == "image":
        return _read_image(path)
    if fmt == "video":
        return _read_video(path)
    if fmt == "code":
        return _read_code(path)
    raise UnsupportedFormatError(f"format not supported: {fmt}")


def _read_code(path: Path) -> ReadResult:
    # Lazy import so the core never loads tree-sitter unless a code file is
    # ingested. A single code file is stored as a code document with its symbols
    # in metadata; the full code graph is built by the code plane (code-ingest).
    from ..code.parser import language_for, parse_source

    text = path.read_text(encoding="utf-8", errors="replace")
    parsed = parse_source(path, text)
    symbols = [
        {"kind": s.kind, "name": s.name, "qualified": s.qualified, "start_line": s.start_line}
        for s in parsed.symbols
    ]
    return ReadResult(
        text=text,
        format=f"code:{language_for(path)}",
        metadata={
            "plane": "code",
            "language": language_for(path),
            "symbols": symbols,
            "symbol_count": len(symbols),
            "note": "single-file code document; run code-ingest for the full code graph",
        },
    )


def _read_image(path: Path) -> ReadResult:
    # Lazy import so the core never loads Pillow or the media package unless an
    # image file is actually ingested.
    from ..media.images import read_image

    return read_image(path)


def _read_video(path: Path) -> ReadResult:
    import os

    from ..media.video import read_video

    # On-screen keyframe OCR is opt-in (it runs ffmpeg and tesseract). Enable it
    # for ingestion by setting DKG_MEDIA_KEYFRAMES=1; it degrades cleanly when the
    # external tools are absent.
    with_keyframes = os.environ.get("DKG_MEDIA_KEYFRAMES", "").strip() in ("1", "true", "yes")
    return read_video(path, with_keyframes=with_keyframes)


def _read_plain(path: Path, fmt: str) -> ReadResult:
    text = path.read_text(encoding="utf-8", errors="replace")
    return ReadResult(text=text, format=fmt, metadata={"encoding": "utf-8"})


def _read_json(path: Path) -> ReadResult:
    raw = path.read_text(encoding="utf-8", errors="replace")
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        raise IngestError(f"invalid JSON: {e}") from e
    text = json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False)
    return ReadResult(text=text, format="json", metadata={"encoding": "utf-8"})


def _read_csv(path: Path) -> ReadResult:
    raw = path.read_text(encoding="utf-8", errors="replace")
    delimiter = "," if path.suffix.lower() == ".csv" else "\t"
    reader = csv.reader(io.StringIO(raw), delimiter=delimiter)
    lines: list[str] = []
    for row in reader:
        lines.append(" | ".join(row))
    text = "\n".join(lines)
    return ReadResult(text=text, format="csv", metadata={"delimiter": delimiter})


def _read_html(path: Path) -> ReadResult:
    try:
        from bs4 import BeautifulSoup  # type: ignore[import-not-found]
    except ImportError as e:
        raise UnsupportedFormatError(
            "HTML ingestion requires the 'html' extra: pip install d-knowledge-graph[html]"
        ) from e
    raw = path.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(raw, "lxml") if _has_lxml() else BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    title = (soup.title.string.strip() if soup.title and soup.title.string else "")
    text = soup.get_text("\n").strip()
    return ReadResult(text=text, format="html", metadata={"title": title})


def _has_lxml() -> bool:
    try:
        import lxml  # noqa: F401
        return True
    except ImportError:
        return False


def _read_pdf(path: Path) -> ReadResult:
    try:
        import pypdf  # type: ignore[import-not-found]
    except ImportError as e:
        raise UnsupportedFormatError(
            "PDF ingestion requires the 'pdf' extra: pip install d-knowledge-graph[pdf]"
        ) from e
    reader = pypdf.PdfReader(str(path))
    pages = []
    for i, page in enumerate(reader.pages):
        try:
            pages.append(page.extract_text() or "")
        except Exception as e:
            raise IngestError(f"failed to extract PDF page {i}: {e}") from e
    text = "\n\n".join(pages)
    return ReadResult(text=text, format="pdf", metadata={"pages": len(pages)})


def _read_docx(path: Path) -> ReadResult:
    from .docx_stdlib import read_docx_text

    text = read_docx_text(path)
    return ReadResult(text=text, format="docx", metadata={"parser": "stdlib"})


def read_string(text: str, *, fmt: str = "text") -> ReadResult:
    return ReadResult(text=text, format=fmt, metadata={"origin": "inline"})

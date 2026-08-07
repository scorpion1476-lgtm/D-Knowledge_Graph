"""Notebook parsing for the source-code plane.

A notebook is a JSON document whose code cells hold source in one language. Both
formats handled here are read with the standard library only, with no network
and no notebook runtime: the file is parsed as JSON, the code cells are
concatenated in document order, and the result is handed to the ordinary code
parser for the notebook's language. Symbols therefore look exactly like symbols
from a plain source file, so search, blast radius, and execution flow work over
notebooks without knowing they are notebooks.

Two formats are supported:

- Jupyter (``.ipynb``): cells live under ``cells``, a code cell has
  ``cell_type == "code"``, and the language comes from
  ``metadata.kernelspec.language`` or ``metadata.language_info.name``.
- Databricks source notebooks (``.py``, ``.scala``, ``.sql``, ``.r`` files whose
  first line is the Databricks notebook marker comment): cells are separated by
  a magic command separator comment. These are plain source files, so they are
  detected by their marker rather than by extension, and a cell whose magic
  names a different language is skipped rather than parsed as the wrong one.

Line numbers are reported against the concatenated code, not against the
notebook file, and the cell boundary map is returned so a caller can map back.
That is stated wherever notebook symbols surface rather than left implicit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.errors import IngestError, UnsupportedFormatError

NOTEBOOK_EXTENSIONS = (".ipynb",)

# The first line of a Databricks source notebook, and the separator between its
# cells. Both are ordinary comments in the notebook's own language.
_DATABRICKS_MARKERS = (
    "# Databricks notebook source",
    "-- Databricks notebook source",
    "// Databricks notebook source",
)
_DATABRICKS_SEPARATORS = (
    "# COMMAND ----------",
    "-- COMMAND ----------",
    "// COMMAND ----------",
)
_DATABRICKS_MAGIC = ("# MAGIC", "-- MAGIC", "// MAGIC")

# Kernel language name to the project's language name.
_LANGUAGE_ALIASES = {
    "python": "python",
    "python3": "python",
    "ipython": "python",
    "r": "r",
    "ir": "r",
    "julia": "julia",
    "scala": "scala",
    "sql": "sql",
    "javascript": "javascript",
    "typescript": "typescript",
    "bash": "bash",
    "sh": "bash",
    "ruby": "ruby",
    "rust": "rust",
    "go": "go",
    "c#": "csharp",
    "csharp": "csharp",
    "powershell": "powershell",
}

_DATABRICKS_EXT_LANGUAGE = {
    ".py": "python",
    ".scala": "scala",
    ".sql": "sql",
    ".r": "r",
}

_MAX_BYTES = 8 * 1024 * 1024


@dataclass
class NotebookCode:
    """Code lifted out of a notebook, ready for the ordinary code parser."""

    language: str
    source: str
    # (first line, last line, cell index) per code cell, one-based and inclusive,
    # against `source`, so a symbol's line can be mapped back to its cell.
    cell_lines: list[tuple[int, int, int]]
    format: str
    skipped_cells: int = 0


def is_notebook(path: str | Path) -> bool:
    return Path(path).suffix.lower() in NOTEBOOK_EXTENSIONS


def is_databricks_notebook(path: str | Path, text: str | None = None) -> bool:
    """True when a plain source file carries the Databricks notebook marker."""
    if Path(path).suffix.lower() not in _DATABRICKS_EXT_LANGUAGE:
        return False
    if text is None:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                first = fh.readline()
        except OSError:
            return False
    else:
        first = text.splitlines()[0] if text.splitlines() else ""
    return first.strip() in _DATABRICKS_MARKERS


def _normalise_language(raw: str) -> str | None:
    return _LANGUAGE_ALIASES.get((raw or "").strip().lower())


def _cell_source(cell: dict) -> str:
    source: Any = cell.get("source", "")
    if isinstance(source, list):
        return "".join(str(part) for part in source)
    return str(source)


def read_jupyter(path: str | Path, text: str | None = None) -> NotebookCode:
    """Concatenate the code cells of a Jupyter notebook."""
    path = str(path)
    if text is None:
        raw = Path(path).read_bytes()
        if len(raw) > _MAX_BYTES:
            raise IngestError(f"notebook too large: {len(raw)} bytes")
        text = raw.decode("utf-8", "replace")
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as e:
        raise IngestError(f"{path} is not valid notebook JSON: {e}") from e
    if not isinstance(doc, dict):
        raise IngestError(f"{path}: notebook root must be a JSON object")
    metadata = doc.get("metadata") or {}
    kernel = (metadata.get("kernelspec") or {}).get("language", "")
    info = (metadata.get("language_info") or {}).get("name", "")
    language = _normalise_language(str(kernel)) or _normalise_language(str(info))
    if language is None:
        raise UnsupportedFormatError(
            f"{path}: notebook kernel language {kernel or info or 'unknown'!r} has no code parser"
        )
    cells = doc.get("cells")
    if not isinstance(cells, list):
        raise IngestError(f"{path}: notebook has no cells array")
    parts: list[str] = []
    spans: list[tuple[int, int, int]] = []
    skipped = 0
    line = 1
    for index, cell in enumerate(cells):
        if not isinstance(cell, dict) or cell.get("cell_type") != "code":
            continue
        source = _cell_source(cell)
        if not source.strip():
            skipped += 1
            continue
        body = source if source.endswith("\n") else source + "\n"
        count = body.count("\n")
        parts.append(body)
        spans.append((line, line + count - 1, index))
        line += count
    return NotebookCode(
        language=language,
        source="".join(parts),
        cell_lines=spans,
        format="jupyter",
        skipped_cells=skipped,
    )


def read_databricks(path: str | Path, text: str | None = None) -> NotebookCode:
    """Concatenate the code cells of a Databricks source notebook."""
    path = str(path)
    ext = Path(path).suffix.lower()
    language = _DATABRICKS_EXT_LANGUAGE.get(ext)
    if language is None:
        raise UnsupportedFormatError(f"{path}: not a Databricks source notebook extension")
    if text is None:
        raw = Path(path).read_bytes()
        if len(raw) > _MAX_BYTES:
            raise IngestError(f"notebook too large: {len(raw)} bytes")
        text = raw.decode("utf-8", "replace")
    cells: list[list[str]] = [[]]
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if stripped in _DATABRICKS_MARKERS:
            continue
        if stripped in _DATABRICKS_SEPARATORS:
            cells.append([])
            continue
        cells[-1].append(raw_line)
    parts: list[str] = []
    spans: list[tuple[int, int, int]] = []
    skipped = 0
    line = 1
    for index, cell in enumerate(cells):
        body_lines = [ln for ln in cell if ln.strip()]
        if not body_lines:
            continue
        # A MAGIC cell is written in another language (%sql, %md, %sh). Parsing
        # it as the notebook's language would invent symbols, so it is skipped
        # and counted rather than guessed at.
        if any(ln.strip().startswith(_DATABRICKS_MAGIC) for ln in body_lines):
            skipped += 1
            continue
        body = "\n".join(cell).rstrip("\n") + "\n"
        count = body.count("\n")
        parts.append(body)
        spans.append((line, line + count - 1, index))
        line += count
    return NotebookCode(
        language=language,
        source="".join(parts),
        cell_lines=spans,
        format="databricks",
        skipped_cells=skipped,
    )


def read_notebook(path: str | Path, text: str | None = None) -> NotebookCode:
    """Read either notebook format, chosen by extension then by marker."""
    if is_notebook(path):
        return read_jupyter(path, text)
    if is_databricks_notebook(path, text):
        return read_databricks(path, text)
    raise UnsupportedFormatError(f"{path} is not a notebook")


def cell_for_line(notebook: NotebookCode, line: int) -> int | None:
    """The notebook cell index a concatenated-source line came from."""
    for start, end, index in notebook.cell_lines:
        if start <= line <= end:
            return index
    return None

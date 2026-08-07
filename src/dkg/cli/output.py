"""Human-readable and machine-readable output helpers."""

from __future__ import annotations

import json
import sys
from typing import Any


def print_json(obj: Any, *, indent: int | None = 2, out=None) -> None:
    stream = out or sys.stdout
    stream.write(json.dumps(obj, indent=indent, sort_keys=True, ensure_ascii=False) + "\n")


def print_kv(rows: list[tuple[str, Any]], out=None) -> None:
    stream = out or sys.stdout
    width = max((len(k) for k, _ in rows), default=0)
    for k, v in rows:
        stream.write(f"{k.ljust(width)}  {v}\n")


def print_table(headers: list[str], rows: list[list[Any]], out=None) -> None:
    stream = out or sys.stdout
    widths = [len(h) for h in headers]
    for r in rows:
        for i, v in enumerate(r):
            widths[i] = max(widths[i], len(str(v)))
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    stream.write(line + "\n")
    stream.write("  ".join("-" * widths[i] for i in range(len(headers))) + "\n")
    for r in rows:
        stream.write("  ".join(str(v).ljust(widths[i]) for i, v in enumerate(r)) + "\n")

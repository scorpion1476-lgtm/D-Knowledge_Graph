"""Source-code plane: parse code, build a code graph on the shared substrate.

Parsing uses Tree-sitter (optional 'code' extra) in-process with no network. Code
entities and edges live in the shared SQLite tables, not a parallel store. Every
capability is capability-detected; the core runs without the code extra.
"""

from __future__ import annotations

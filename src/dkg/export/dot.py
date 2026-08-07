"""Graphviz DOT export.

Emits a directed graph that any Graphviz tool renders. All identifiers and label
text are quoted and escaped so no node name can break the syntax.
"""

from __future__ import annotations

from pathlib import Path

from ..core.db import Database
from .graphdata import DEFAULT_MAX_NODES, colour_for, load_graph


def _dot_quote(text: str) -> str:
    # DOT double-quoted strings escape the double quote and backslash; newlines
    # are turned into the literal escape so the output stays single-line safe.
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def export_dot(db: Database, out: Path, *, max_nodes: int = DEFAULT_MAX_NODES) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    g = load_graph(db, max_nodes=max_nodes)
    lines: list[str] = ["digraph dkg {", "  rankdir=LR;", '  node [shape=box, style="rounded,filled", fontname="sans-serif"];']
    if g.truncated:
        lines.append(f"  // truncated to {max_nodes} nodes")
    for n in g.nodes:
        lines.append(
            f"  {_dot_quote(n['id'])} [label={_dot_quote(n['label'])}, "
            f"fillcolor={_dot_quote(colour_for(n['kind']))}, tooltip={_dot_quote(n['kind'])}];"
        )
    for e in g.edges:
        lines.append(
            f"  {_dot_quote(e['source'])} -> {_dot_quote(e['target'])} "
            f"[label={_dot_quote(e['predicate'])}];"
        )
    lines.append("}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out

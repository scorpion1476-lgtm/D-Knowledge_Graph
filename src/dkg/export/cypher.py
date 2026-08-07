"""Cypher export for property-graph databases.

Emits idempotent MERGE statements: one per node and one per relationship. String
literals are single-quoted with the quote and backslash escaped, and the
relationship type is derived from the predicate as a safe upper-case token, so no
value can inject Cypher syntax.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..core.db import Database
from .graphdata import DEFAULT_MAX_NODES, load_graph

_REL_SAFE = re.compile(r"[^A-Za-z0-9_]")


def _cypher_str(text: str) -> str:
    return "'" + str(text).replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n") + "'"


def _rel_type(predicate: str) -> str:
    token = _REL_SAFE.sub("_", str(predicate) or "REL").upper().strip("_")
    if not token or not (token[0].isalpha() or token[0] == "_"):
        token = "REL_" + token
    return token


def export_cypher(db: Database, out: Path, *, max_nodes: int = DEFAULT_MAX_NODES) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    g = load_graph(db, max_nodes=max_nodes)
    lines: list[str] = ["// D-Knowledge_Graph Cypher export"]
    if g.truncated:
        lines.append(f"// truncated to {max_nodes} nodes")
    for n in g.nodes:
        lines.append(
            f"MERGE (n:Entity {{id: {_cypher_str(n['id'])}}}) "
            f"SET n.label = {_cypher_str(n['label'])}, n.kind = {_cypher_str(n['kind'])};"
        )
    for e in g.edges:
        rel = _rel_type(e["predicate"])
        weight = "" if e["weight"] is None else f" SET r.weight = {e['weight']}"
        lines.append(
            f"MATCH (a:Entity {{id: {_cypher_str(e['source'])}}}), "
            f"(b:Entity {{id: {_cypher_str(e['target'])}}}) "
            f"MERGE (a)-[r:{rel}]->(b){weight};"
        )
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out

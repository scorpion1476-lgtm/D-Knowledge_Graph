"""Obsidian vault export.

Writes one Markdown note per entity into a directory, with outbound edges as
``[[wikilinks]]`` grouped by predicate. Note filenames are sanitised so they are
filesystem-safe and stable, and a link map records the original id to filename
mapping. The output directory is the ``out`` path for this format.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..core.db import Database
from .graphdata import DEFAULT_MAX_NODES, load_graph

_UNSAFE = re.compile(r'[\\/:*?"<>|#\^\[\]]+')


def _note_name(label: str, entity_id: str) -> str:
    base = _UNSAFE.sub("_", str(label)).strip().strip(".") or "entity"
    base = base[:80]
    # Disambiguate with a short stable suffix of the id so distinct entities that
    # share a display label never collide on one note.
    suffix = re.sub(r"[^A-Za-z0-9]", "", str(entity_id))[-8:]
    return f"{base}__{suffix}" if suffix else base


def export_obsidian(db: Database, out: Path, *, max_nodes: int = DEFAULT_MAX_NODES) -> Path:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    g = load_graph(db, max_nodes=max_nodes)
    names = {n["id"]: _note_name(n["label"], n["id"]) for n in g.nodes}
    kinds = {n["id"]: n["kind"] for n in g.nodes}
    labels = {n["id"]: n["label"] for n in g.nodes}

    out_edges: dict[str, list[dict]] = {n["id"]: [] for n in g.nodes}
    for e in g.edges:
        if e["source"] in out_edges and e["target"] in names:
            out_edges[e["source"]].append(e)

    for n in g.nodes:
        nid = n["id"]
        lines = [
            "---",
            f"kind: {kinds[nid]}",
            f"id: {nid}",
            "---",
            f"# {labels[nid]}",
            "",
        ]
        edges = out_edges[nid]
        if edges:
            by_pred: dict[str, list[str]] = {}
            for e in edges:
                by_pred.setdefault(e["predicate"] or "related", []).append(names[e["target"]])
            for pred in sorted(by_pred):
                lines.append(f"## {pred}")
                for target_name in by_pred[pred]:
                    lines.append(f"- [[{target_name}]]")
                lines.append("")
        else:
            lines.append("_No outbound edges._")
            lines.append("")
        (out / f"{names[nid]}.md").write_text("\n".join(lines), encoding="utf-8")

    index = {
        "notes": len(g.nodes),
        "edges": sum(len(v) for v in out_edges.values()),
        "truncated": g.truncated,
        "map": names,
    }
    (out / "_index.json").write_text(json.dumps(index, indent=2, sort_keys=True), encoding="utf-8")
    return out

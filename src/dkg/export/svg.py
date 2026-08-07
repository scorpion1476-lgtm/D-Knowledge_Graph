"""Standalone SVG export.

Draws the graph with the shared deterministic force-directed layout into a
self-contained SVG document: no script, no external reference, no network. All
label text is XML-escaped.
"""

from __future__ import annotations

from pathlib import Path

from ..core.db import Database
from .graphdata import DEFAULT_MAX_NODES, GraphData, colour_for, esc_xml, layout_positions, load_graph

_WIDTH = 960
_HEIGHT = 640


def render_svg(g: GraphData, *, width: int = _WIDTH, height: int = _HEIGHT) -> str:
    pos = layout_positions(g.nodes, g.edges, width=width, height=height)
    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="sans-serif">',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        'markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#94a3b8"/></marker></defs>',
    ]
    for e in g.edges:
        if e["source"] not in pos or e["target"] not in pos:
            continue
        x1, y1 = pos[e["source"]]
        x2, y2 = pos[e["target"]]
        parts.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#cbd5e1" '
            f'stroke-width="1" marker-end="url(#arrow)"/>'
        )
    for n in g.nodes:
        if n["id"] not in pos:
            continue
        x, y = pos[n["id"]]
        parts.append(f'<circle cx="{x}" cy="{y}" r="7" fill="{colour_for(n["kind"])}" stroke="#1e293b" stroke-width="0.5"/>')
        parts.append(
            f'<text x="{x + 10}" y="{y + 4}" font-size="11" fill="#0f172a">{esc_xml(n["label"])}</text>'
        )
    if g.truncated:
        parts.append(f'<text x="12" y="20" font-size="12" fill="#64748b">graph truncated to {len(g.nodes)} nodes</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def export_svg(db: Database, out: Path, *, max_nodes: int = DEFAULT_MAX_NODES) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    g = load_graph(db, max_nodes=max_nodes)
    out.write_text(render_svg(g), encoding="utf-8")
    return out

"""Shared graph loading, deterministic layout, and escaping for exports.

Used by the DOT, Cypher, SVG, Obsidian, and the offline HTML visualization
exporters so they read one consistent node and edge set and, where they draw,
place nodes with one deterministic force-directed layout. No network, no
third-party dependency.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

from ..core.db import Database

# A bounded default so exports and the viewer stay responsive on large graphs.
DEFAULT_MAX_NODES = 2000

# Deterministic palette by entity kind for the drawn exports.
KIND_COLOURS = {
    "code:module": "#6b7280",
    "code:class": "#2563eb",
    "code:function": "#059669",
    "code:method": "#0891b2",
    "code:type": "#7c3aed",
    "code:test": "#d97706",
    "entity": "#334155",
}
DEFAULT_COLOUR = "#334155"

# Palette used to colour a node by the community it was detected in. Every
# entry is checked against the viewer background for the published
# accessibility level; see CONTRAST_PAIRS in viz.py and the test that asserts
# each pair meets its threshold. Do not add a colour here without adding it
# there, or the automated contrast check will refuse the file.
COMMUNITY_COLOURS: tuple[str, ...] = (
    "#1d4ed8",
    "#047857",
    "#6d28d9",
    "#a16207",
    "#0e7490",
    "#be185d",
    "#374151",
    "#4d7c0f",
    "#c2410c",
    "#0f766e",
    "#7e22ce",
    "#9f1239",
)

# At most this many legend groups. Communities beyond the cap are folded into
# one honestly labelled "other" group rather than silently dropped, so the
# legend stays readable on a graph with hundreds of small communities.
MAX_LEGEND_GROUPS = len(COMMUNITY_COLOURS)

# Node radius bounds for degree scaling. The area, not the radius, grows with
# degree (radius scales with the square root), which is the perceptually honest
# encoding: a node with four times the degree looks four times as large.
MIN_NODE_RADIUS = 5.0
MAX_NODE_RADIUS = 16.0

# Shape by symbol kind. Shape is a non-colour encoding, so kind stays legible
# to a reader who cannot distinguish the community colours.
KIND_SHAPES = {
    "code:module": "square",
    "code:class": "diamond",
    "code:function": "circle",
    "code:method": "triangle",
    "code:type": "hexagon",
    "code:test": "pentagon",
    "entity": "circle",
}
DEFAULT_SHAPE = "circle"

# sides, starting angle in radians. A vertex-up polygon starts at -pi/2.
_POLYGONS: dict[str, tuple[int, float]] = {
    "square": (4, math.pi / 4),
    "diamond": (4, -math.pi / 2),
    "triangle": (3, -math.pi / 2),
    "pentagon": (5, -math.pi / 2),
    "hexagon": (6, 0.0),
}


@dataclass
class GraphData:
    nodes: list[dict]
    edges: list[dict]
    truncated: bool


def colour_for(kind: str) -> str:
    return KIND_COLOURS.get(kind, DEFAULT_COLOUR)


def shape_for(kind: str) -> str:
    return KIND_SHAPES.get(kind, DEFAULT_SHAPE)


def community_colour(index: int) -> str:
    return COMMUNITY_COLOURS[index % len(COMMUNITY_COLOURS)]


def shape_path(shape: str, x: float, y: float, r: float) -> str:
    """SVG path data for one node mark, centred on (x, y) with radius r.

    Computed here, in Python, so the drawn mark and the legend key come from a
    single implementation and the emitted file is byte-identical for a given
    graph. The viewer's script moves a mark with a transform and never
    recomputes this path, so there is no second implementation to drift.
    """
    if shape == "circle":
        return (
            f"M{round(x - r, 2)},{round(y, 2)}"
            f"a{round(r, 2)},{round(r, 2)} 0 1,0 {round(2 * r, 2)},0"
            f"a{round(r, 2)},{round(r, 2)} 0 1,0 {round(-2 * r, 2)},0"
        )
    sides, start = _POLYGONS.get(shape, _POLYGONS["diamond"])
    parts = []
    for i in range(sides):
        angle = start + i * 2 * math.pi / sides
        px = round(x + r * math.cos(angle), 2)
        py = round(y + r * math.sin(angle), 2)
        parts.append(f"{'M' if i == 0 else 'L'}{px},{py}")
    return "".join(parts) + "Z"


def degree_map(nodes: list[dict], edges: list[dict]) -> dict[str, int]:
    """Undirected degree per node, counting every incident edge including self loops."""
    deg = {n["id"]: 0 for n in nodes}
    for e in edges:
        s, t = e["source"], e["target"]
        if s in deg:
            deg[s] += 1
        if t in deg and t != s:
            deg[t] += 1
    return deg


def radius_for_degree(degree: int, max_degree: int) -> float:
    """Node radius scaled by degree, bounded and rounded so output is stable."""
    if max_degree <= 0:
        return MIN_NODE_RADIUS
    share = math.sqrt(max(0, degree) / max_degree)
    return round(MIN_NODE_RADIUS + (MAX_NODE_RADIUS - MIN_NODE_RADIUS) * share, 2)


def community_groups(nodes: list[dict], edges: list[dict]) -> tuple[dict[str, int], list[dict]]:
    """Assign every node to a legend group and describe the groups.

    The partition comes from the shared core's base detector, which is
    deterministic for a given graph. Detected communities are then ranked by
    size (ties broken by the lexicographically smallest member id) so the group
    index is a stable function of the graph rather than of detector internals.

    The indices are labels for this one file. They are produced independently
    per run and must never be compared against the indices in another run or
    another export; compare co-membership instead.
    """
    from ..graph.community import detect_communities

    ids = [n["id"] for n in nodes]
    if not ids:
        return {}, []
    present = set(ids)
    weighted = [
        (e["source"], e["target"], float(e.get("weight") or 1.0) or 1.0)
        for e in edges
        if e["source"] in present and e["target"] in present
    ]
    detected = detect_communities(ids, weighted).get("assignment", {})

    members: dict[int, list[str]] = {}
    for nid in ids:
        members.setdefault(int(detected.get(nid, 0)), []).append(nid)
    # Largest first; a tie keeps the community whose smallest member sorts first.
    ranked = sorted(members.items(), key=lambda kv: (-len(kv[1]), min(kv[1])))

    named = ranked if len(ranked) <= MAX_LEGEND_GROUPS else ranked[: MAX_LEGEND_GROUPS - 1]
    folded = [] if len(ranked) <= MAX_LEGEND_GROUPS else ranked[MAX_LEGEND_GROUPS - 1 :]

    assignment: dict[str, int] = {}
    groups: list[dict] = []
    for index, (_detected_index, member_ids) in enumerate(named):
        for nid in member_ids:
            assignment[nid] = index
        groups.append(
            {
                "index": index,
                "label": f"Community {index + 1}",
                "colour": community_colour(index),
                "count": len(member_ids),
                "folded_communities": 0,
            }
        )
    if folded:
        index = len(named)
        count = 0
        for _detected_index, member_ids in folded:
            for nid in member_ids:
                assignment[nid] = index
            count += len(member_ids)
        groups.append(
            {
                "index": index,
                "label": f"Other ({len(folded)} smaller communities)",
                "colour": community_colour(index),
                "count": count,
                "folded_communities": len(folded),
            }
        )
    return assignment, groups


def load_graph(db: Database, *, tenant_id: str | None = None, max_nodes: int = DEFAULT_MAX_NODES) -> GraphData:
    """Load nodes and the edges whose endpoints are both present.

    Returns at most ``max_nodes`` nodes; ``truncated`` records whether the cap
    was hit so callers can label the output honestly.
    """
    if tenant_id is None:
        node_rows = db.fetchall(
            "SELECT entity_id, kind, display, canonical FROM entities ORDER BY entity_id LIMIT ?;",
            (max_nodes + 1,),
        )
    else:
        node_rows = db.fetchall(
            "SELECT entity_id, kind, display, canonical FROM entities WHERE tenant_id=? ORDER BY entity_id LIMIT ?;",
            (tenant_id, max_nodes + 1),
        )
    truncated = len(node_rows) > max_nodes
    node_rows = node_rows[:max_nodes]
    nodes = [
        {
            "id": r["entity_id"],
            "label": r["display"] or r["canonical"] or r["entity_id"],
            "kind": r["kind"] or "entity",
        }
        for r in node_rows
    ]
    ids = {n["id"] for n in nodes}
    edge_rows = db.fetchall(
        "SELECT subject_id, object_id, predicate, weight FROM relationships ORDER BY relationship_id;"
    )
    edges = [
        {
            "source": r["subject_id"],
            "target": r["object_id"],
            "predicate": r["predicate"] or "",
            "weight": float(r["weight"]) if r["weight"] is not None else None,
        }
        for r in edge_rows
        if r["subject_id"] in ids and r["object_id"] in ids
    ]
    return GraphData(nodes=nodes, edges=edges, truncated=truncated)


def _hash01(text: str, seed: int) -> float:
    digest = hashlib.sha256(f"{seed}:{text}".encode()).digest()
    return int.from_bytes(digest[:4], "big") / 2**32


def layout_positions(
    nodes: list[dict],
    edges: list[dict],
    *,
    width: int = 960,
    height: int = 640,
    seed: int = 1,
) -> dict[str, tuple[float, float]]:
    """Deterministic force-directed layout in pure Python.

    Seeded initial placement plus a bounded number of repulsion and attraction
    iterations. Same input always yields the same coordinates, so the static SVG
    and the HTML viewer draw identically and tests are reproducible.
    """
    n = len(nodes)
    if n == 0:
        return {}
    ids = [nd["id"] for nd in nodes]
    pos: dict[str, list[float]] = {}
    for nid in ids:
        angle = 2 * math.pi * _hash01(nid, seed)
        radius = 0.35 * min(width, height) * (0.4 + 0.6 * _hash01(nid, seed + 7))
        pos[nid] = [width / 2 + radius * math.cos(angle), height / 2 + radius * math.sin(angle)]
    if n == 1:
        return {ids[0]: (width / 2, height / 2)}
    k = math.sqrt((width * height) / n)
    adj = [(e["source"], e["target"]) for e in edges if e["source"] in pos and e["target"] in pos]
    # Fewer iterations for larger graphs keeps layout time bounded.
    iterations = 300 if n <= 120 else max(60, 30000 // n)
    temp = width / 8.0
    for _ in range(iterations):
        disp = {i: [0.0, 0.0] for i in ids}
        for a in range(n):
            ia = ids[a]
            pax, pay = pos[ia]
            for b in range(a + 1, n):
                ib = ids[b]
                dx = pax - pos[ib][0]
                dy = pay - pos[ib][1]
                dist = math.hypot(dx, dy) or 0.01
                force = (k * k) / dist
                ux, uy = dx / dist, dy / dist
                disp[ia][0] += ux * force
                disp[ia][1] += uy * force
                disp[ib][0] -= ux * force
                disp[ib][1] -= uy * force
        for s, t in adj:
            dx = pos[s][0] - pos[t][0]
            dy = pos[s][1] - pos[t][1]
            dist = math.hypot(dx, dy) or 0.01
            force = (dist * dist) / k
            ux, uy = dx / dist, dy / dist
            disp[s][0] -= ux * force
            disp[s][1] -= uy * force
            disp[t][0] += ux * force
            disp[t][1] += uy * force
        for i in ids:
            dx, dy = disp[i]
            d = math.hypot(dx, dy) or 0.01
            pos[i][0] += (dx / d) * min(d, temp)
            pos[i][1] += (dy / d) * min(d, temp)
            pos[i][0] = min(width - 24, max(24, pos[i][0]))
            pos[i][1] = min(height - 24, max(24, pos[i][1]))
        temp *= 0.95
    return {i: (round(pos[i][0], 2), round(pos[i][1], 2)) for i in ids}


def esc_xml(text: str) -> str:
    """Escape text for XML/SVG text and attribute content."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def esc_html(text: str) -> str:
    return esc_xml(text)

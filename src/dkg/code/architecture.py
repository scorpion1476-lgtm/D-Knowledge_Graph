"""Auto-generated architecture overview with coupling warnings.

The code graph is a symbol-level object, which is the right resolution for
impact analysis and the wrong resolution for understanding a system. This module
lifts it to components (the directory a file lives in), aggregates the edges
between them, and reports the shapes that usually deserve an architect's
attention: dependency cycles, components everything depends on, components that
depend on everything, and components whose symbols do not cluster together.

Everything is derived from measured graph structure with documented thresholds
taken from the graph's own distribution, never from constants tuned to a
particular repository. Warnings are advisory: the underlying edges are
structural and over-approximate, so a warning is a place to look, not a defect.

A Mermaid rendering is included because a component graph is much easier to read
as a picture, and Mermaid renders natively in Markdown without any external
asset, which keeps the air-gap default intact.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable
from pathlib import PurePosixPath

from ..core.db import Database
from .analysis import DEFAULT_MAX_NODES, STRUCTURAL_PREDICATES, CodeGraphView, load_code_graph

ROOT_COMPONENT = "(root)"
_MAX_DIAGRAM_NODES = 40


def architecture_map(
    db: Database,
    *,
    tenant_id: str = "local",
    predicates: Iterable[str] | None = None,
    resolution: float = 1.0,
    max_nodes: int = DEFAULT_MAX_NODES,
    limit: int = 40,
) -> dict:
    """Build the component-level architecture overview."""
    view = load_code_graph(db, tenant_id=tenant_id, max_nodes=max_nodes)
    return map_from_view(view, predicates=predicates, resolution=resolution, limit=limit)


def map_from_view(
    view: CodeGraphView,
    *,
    predicates: Iterable[str] | None = None,
    resolution: float = 1.0,
    limit: int = 40,
) -> dict:
    """The analysis proper, separated so a caller holding a view can reuse it."""
    preds = tuple(predicates) if predicates is not None else STRUCTURAL_PREDICATES
    limit = max(1, int(limit))

    component_of = {nid: _component_for(view.path_of(nid)) for nid in view.node_ids()}
    members: dict[str, list[str]] = defaultdict(list)
    for nid in view.node_ids():
        members[component_of[nid]].append(nid)

    communities = view.communities(preds, resolution=resolution) if not view.is_empty else {}

    # Aggregate symbol edges up to component edges, keeping the internal ones
    # separate: internal cohesion and external coupling answer different
    # questions and must not be summed together.
    internal: dict[str, int] = defaultdict(int)
    between: dict[tuple[str, str], int] = defaultdict(int)
    cross_language: dict[tuple[str, str], int] = defaultdict(int)
    for edge in view.edges_for(preds):
        src = component_of.get(edge.subject_id)
        dst = component_of.get(edge.object_id)
        if src is None or dst is None:
            continue
        if src == dst:
            internal[src] += 1
            continue
        between[(src, dst)] += 1
        if view.language_of(edge.subject_id) and view.language_of(edge.object_id):
            if view.language_of(edge.subject_id) != view.language_of(edge.object_id):
                cross_language[(src, dst)] += 1

    fan_out: dict[str, int] = defaultdict(int)
    fan_in: dict[str, int] = defaultdict(int)
    for (src, dst), count in between.items():
        fan_out[src] += count
        fan_in[dst] += count

    components: list[dict] = []
    for name in sorted(members):
        ids = members[name]
        langs = sorted({view.language_of(i) for i in ids if view.language_of(i)})
        comms = sorted({communities[i] for i in ids if i in communities})
        components.append(
            {
                "component": name,
                "symbols": len(ids),
                "languages": langs,
                "internal_edges": internal.get(name, 0),
                "fan_in": fan_in.get(name, 0),
                "fan_out": fan_out.get(name, 0),
                # A component whose symbols are scattered over many communities
                # is not clustering as a unit, whatever the directory says.
                "communities": len(comms),
                "cohesion": _cohesion(len(ids), internal.get(name, 0)),
            }
        )

    edges = [
        {
            "from": src,
            "to": dst,
            "count": count,
            "cross_language": cross_language.get((src, dst), 0),
        }
        for (src, dst), count in sorted(between.items())
    ]

    cycles = _component_cycles(between)
    warnings = _warnings(components, edges, cycles, limit)

    return {
        "components": components[:limit],
        "edges": edges[:limit],
        "cycles": cycles[:limit],
        "warnings": warnings[:limit],
        "totals": {
            "components": len(components),
            "component_edges": len(edges),
            "symbols": len(view),
            "cycles": len(cycles),
            "warnings": len(warnings),
            "languages": view.languages(),
        },
        "lists_capped": {
            "limit": limit,
            "components_omitted": max(0, len(components) - limit),
            "edges_omitted": max(0, len(edges) - limit),
            "warnings_omitted": max(0, len(warnings) - limit),
        },
        "truncated": view.truncated,
        "why": {
            "component": "the directory containing a symbol's file",
            "predicates": list(preds),
            "resolution": resolution,
            "thresholds": "fan-in and fan-out cuts are the 90th percentile of the observed distribution",
            "note": (
                "Structural and advisory. Component edges are aggregated from "
                "name-resolved symbol edges, which over-approximate, so a "
                "warning marks a place to look rather than a defect."
            ),
        },
    }


# -- helpers ----------------------------------------------------------------


def _component_for(path: str) -> str:
    if not path:
        return ROOT_COMPONENT
    parent = PurePosixPath(path).parent
    text = str(parent)
    return ROOT_COMPONENT if text in ("", ".") else text


def _cohesion(symbols: int, internal_edges: int) -> float:
    """Internal edges over the pairs a component of this size could hold.

    Normalizing by the possible pair count keeps the number comparable between a
    three-symbol component and a three-hundred-symbol one, which a raw edge
    count is not.
    """
    if symbols < 2:
        return 0.0
    possible = symbols * (symbols - 1) / 2.0
    return round(min(1.0, internal_edges / possible), 6)


def _percentile(values: list[int], fraction: float) -> int:
    """Nearest-rank percentile.

    A count has to be a value some component actually exhibits, otherwise a
    threshold of 4.5 produces a warning no reader can check against a real
    component.
    """
    if not values:
        return 0
    ordered = sorted(values)
    # math.ceil, matching coupling.py and gaps.py. int(round(x + 0.5)) hits
    # banker's rounding and disagreed with them at exactly n = 10.
    rank = max(1, min(len(ordered), math.ceil(fraction * len(ordered))))
    return ordered[rank - 1]


def _component_cycles(between: dict[tuple[str, str], int]) -> list[dict]:
    """Strongly connected component groups of size two or more, via Tarjan.

    Iterative, not recursive: a component graph is small, but the same code path
    should not become a stack hazard if someone points this at a monorepo.
    """
    adj: dict[str, list[str]] = defaultdict(list)
    nodes: set[str] = set()
    for src, dst in between:
        adj[src].append(dst)
        nodes.add(src)
        nodes.add(dst)
    for key in adj:
        adj[key].sort()

    index_of: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    counter = 0
    groups: list[list[str]] = []

    for root in sorted(nodes):
        if root in index_of:
            continue
        work: list[tuple[str, int]] = [(root, 0)]
        while work:
            node, child_i = work[-1]
            if child_i == 0:
                index_of[node] = low[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)
            recursed = False
            children = adj.get(node, [])
            while child_i < len(children):
                nxt = children[child_i]
                child_i += 1
                if nxt not in index_of:
                    work[-1] = (node, child_i)
                    work.append((nxt, 0))
                    recursed = True
                    break
                if nxt in on_stack:
                    low[node] = min(low[node], index_of[nxt])
            if recursed:
                continue
            work[-1] = (node, child_i)
            if child_i >= len(children):
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == index_of[node]:
                    group: list[str] = []
                    while True:
                        member = stack.pop()
                        on_stack.discard(member)
                        group.append(member)
                        if member == node:
                            break
                    if len(group) > 1:
                        groups.append(sorted(group))

    # A component that depends on itself through another component is a cycle
    # too; Tarjan finds those as part of the same group.
    result = [
        {"components": group, "size": len(group), "edges": _cycle_edges(group, between)}
        for group in sorted(groups, key=lambda g: (-len(g), g))
    ]
    return result


def _cycle_edges(group: list[str], between: dict[tuple[str, str], int]) -> list[dict]:
    inside = set(group)
    return [
        {"from": src, "to": dst, "count": count}
        for (src, dst), count in sorted(between.items())
        if src in inside and dst in inside
    ]


def _warnings(components: list[dict], edges: list[dict], cycles: list[dict], limit: int) -> list[dict]:
    out: list[dict] = []

    for cycle in cycles:
        out.append(
            {
                "kind": "dependency_cycle",
                "severity": "high",
                "subject": " <-> ".join(cycle["components"]),
                "detail": (
                    f"{cycle['size']} components depend on each other in a cycle, so none "
                    "of them can be understood, tested, or released on its own."
                ),
                "evidence": {"components": cycle["components"], "edges": cycle["edges"]},
            }
        )

    fan_in_values = [int(c["fan_in"]) for c in components]
    fan_out_values = [int(c["fan_out"]) for c in components]
    fan_in_cut = _percentile(fan_in_values, 0.9)
    fan_out_cut = _percentile(fan_out_values, 0.9)

    for component in components:
        name = component["component"]
        if component["fan_in"] and component["fan_in"] >= fan_in_cut and component["fan_in"] > 1:
            out.append(
                {
                    "kind": "high_fan_in",
                    "severity": "medium",
                    "subject": name,
                    "detail": (
                        f"{component['fan_in']} incoming dependencies, at or above the 90th "
                        f"percentile ({fan_in_cut}). Changing this component reaches a long way."
                    ),
                    "evidence": {"fan_in": component["fan_in"], "threshold": fan_in_cut},
                }
            )
        if component["fan_out"] and component["fan_out"] >= fan_out_cut and component["fan_out"] > 1:
            out.append(
                {
                    "kind": "high_fan_out",
                    "severity": "medium",
                    "subject": name,
                    "detail": (
                        f"{component['fan_out']} outgoing dependencies, at or above the 90th "
                        f"percentile ({fan_out_cut}). This component knows about a lot of others."
                    ),
                    "evidence": {"fan_out": component["fan_out"], "threshold": fan_out_cut},
                }
            )
        # More clusters than a handful of symbols can justify means the
        # directory is a filing decision, not a design boundary.
        if component["symbols"] >= 4 and component["communities"] > 1 and component["cohesion"] == 0.0:
            out.append(
                {
                    "kind": "low_cohesion",
                    "severity": "low",
                    "subject": name,
                    "detail": (
                        f"{component['symbols']} symbols spread over {component['communities']} "
                        "clusters with no internal edges. The directory may be grouping "
                        "unrelated things."
                    ),
                    "evidence": {
                        "symbols": component["symbols"],
                        "communities": component["communities"],
                        "internal_edges": component["internal_edges"],
                    },
                }
            )

    for edge in edges:
        if edge["cross_language"]:
            out.append(
                {
                    "kind": "cross_language_edge",
                    "severity": "low",
                    "subject": f"{edge['from']} -> {edge['to']}",
                    "detail": (
                        f"{edge['cross_language']} of {edge['count']} edges cross a language "
                        "boundary. In this graph those come from name-based resolution, so "
                        "check whether they are real."
                    ),
                    "evidence": {"count": edge["count"], "cross_language": edge["cross_language"]},
                }
            )

    order = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda w: (order[str(w["severity"])], str(w["kind"]), str(w["subject"])))
    return out


# -- rendering --------------------------------------------------------------


def render_markdown(result: dict) -> str:
    """Render the map as Markdown with a native Mermaid component diagram.

    Mermaid is used rather than a generated image because it renders inline in
    Markdown viewers with no external file, script, or font, which keeps the
    air-gap default intact.
    """
    totals = result.get("totals", {})
    lines: list[str] = [
        "# Architecture overview",
        "",
        f"{totals.get('components', 0)} components, {totals.get('symbols', 0)} symbols, "
        f"{totals.get('component_edges', 0)} component edges, "
        f"{totals.get('warnings', 0)} coupling warnings.",
        "",
        "Structural and advisory: component edges are aggregated from name-resolved "
        "symbol edges, which over-approximate.",
        "",
    ]

    diagram = render_mermaid(result)
    if diagram:
        lines += ["## Component graph", "", "```mermaid", diagram, "```", ""]

    components = result.get("components") or []
    if components:
        lines += [
            "## Components",
            "",
            "| Component | Symbols | Languages | Internal edges | Fan in | Fan out | Cohesion |",
            "| --- | ---: | --- | ---: | ---: | ---: | ---: |",
        ]
        for c in components:
            langs = ", ".join(c["languages"]) or "n/a"
            lines.append(
                f"| `{c['component']}` | {c['symbols']} | {langs} | {c['internal_edges']} "
                f"| {c['fan_in']} | {c['fan_out']} | {c['cohesion']} |"
            )
        lines.append("")

    warnings = result.get("warnings") or []
    lines += ["## Coupling warnings", ""]
    if not warnings:
        lines += ["None found at the current thresholds.", ""]
    else:
        lines += ["| Severity | Kind | Subject | Detail |", "| --- | --- | --- | --- |"]
        for w in warnings:
            lines.append(f"| {w['severity']} | {w['kind']} | `{w['subject']}` | {w['detail']} |")
        lines.append("")
    return "\n".join(lines)


#: The generated component graph uses the same greyscale palette as the
#: diagrams in the READMEs, and states it globally through themeVariables so a
#: shape with no class of its own still renders grey rather than the renderer's
#: default hue. Every value has equal red, green, and blue channels. The values
#: are constants, so the rendered diagram stays byte-identical across runs.
_MERMAID_GREY_THEME = (
    '%%{init: {"theme":"base","themeVariables":{'
    '"primaryColor":"#e4e4e4","primaryTextColor":"#404040",'
    '"primaryBorderColor":"#a5a5a5","lineColor":"#757575",'
    '"secondaryColor":"#d4d4d4","tertiaryColor":"#f4f4f4",'
    '"clusterBkg":"#f4f4f4","clusterBorder":"#a5a5a5",'
    '"edgeLabelBackground":"#f4f4f4","textColor":"#404040"}}}%%'
)
_MERMAID_COMPONENT_STYLE = "fill:#e4e4e4,stroke:#a5a5a5,color:#404040"
#: A component in a cycle keeps the heavier border it always had, and takes the
#: darker grey fill so the emphasis survives on a greyscale-only palette.
_MERMAID_CYCLE_STYLE = "fill:#d4d4d4,stroke:#757575,stroke-width:2px,color:#282828"


def render_mermaid(result: dict) -> str:
    """A Mermaid flowchart of the component graph, or an empty string if none."""
    components = result.get("components") or []
    edges = result.get("edges") or []
    if not components:
        return ""
    shown = components[:_MAX_DIAGRAM_NODES]
    ids = {c["component"]: f"c{i}" for i, c in enumerate(shown)}
    in_cycle: set[str] = set()
    for cycle in result.get("cycles") or []:
        in_cycle.update(cycle.get("components") or [])

    lines = [_MERMAID_GREY_THEME, "flowchart LR"]
    for c in shown:
        node_id = ids[c["component"]]
        label = _mermaid_label(f"{c['component']} ({c['symbols']})")
        lines.append(f"    {node_id}[\"{label}\"]")
    for edge in edges:
        src, dst = edge["from"], edge["to"]
        if src in ids and dst in ids:
            lines.append(f"    {ids[src]} -->|{edge['count']}| {ids[dst]}")

    # Every node is assigned a grey class, and the theme above sets the same
    # greys globally, so nothing falls back to the renderer's default hue.
    # Each node gets exactly one class, in the component order already fixed
    # above, so the diagram stays byte-identical across runs.
    lines.append(f"    classDef component {_MERMAID_COMPONENT_STYLE};")
    lines.append(f"    classDef cycle {_MERMAID_CYCLE_STYLE};")
    plain = [ids[c["component"]] for c in shown if c["component"] not in in_cycle]
    cyclic = [ids[c["component"]] for c in shown if c["component"] in in_cycle]
    if plain:
        lines.append(f"    class {','.join(plain)} component;")
    if cyclic:
        lines.append(f"    class {','.join(cyclic)} cycle;")
    return "\n".join(lines)


def _mermaid_label(text: str) -> str:
    """Neutralise the characters that would break out of a Mermaid label."""
    return (
        text.replace("\\", "/")
        .replace('"', "'")
        .replace("[", "(")
        .replace("]", ")")
        .replace("{", "(")
        .replace("}", ")")
        .replace("|", "/")
        .replace("\n", " ")
    )

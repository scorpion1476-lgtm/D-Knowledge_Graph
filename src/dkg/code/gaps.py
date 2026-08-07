"""Structural knowledge-gap analysis over the shared code graph.

Three questions the graph shape can answer without reading a line of source:
which definitions are attached to nothing, which definitions carry inbound call
pressure with no test edge pointing at them, and which regions of the partition
hold few members joined by few internal edges.

Everything here is ADVISORY and STRUCTURAL. A gap is the absence of an EDGE in
the graph, not a proven property of the running program. An "untested hotspot"
says no ``code:tested_by`` edge reaches the symbol; it does not say the symbol is
untested at runtime, because a test the parser could not connect (a table-driven
suite, an indirect call, a test in a language with no parser installed) leaves no
edge behind. An "isolated" symbol is either genuinely unreferenced or a reference
the name-based resolver could not close. Both readings are reported, and neither
is asserted.

Every cut is derived from the graph's own observed distribution by a nearest-rank
percentile, never from a constant tuned to one corpus, so the analysis carries
over to a repository of any size. The percentile itself is the only fixed choice
and it is a distribution position, not a magic count: the upper quartile for
inbound call pressure (well above typical for this graph) and the median for
community size and density (at or below the middle of this graph's own spread).
Both are reported in ``thresholds`` so a reader can check the arithmetic.

Read-only: the database is never written. Output is deterministic; every list is
sorted by an explicit key with canonical name as the final tie-break, and floats
are rounded to a fixed number of places so repeated runs compare equal.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from ..core.db import Database
from .analysis import (
    DEFAULT_MAX_NODES,
    STRUCTURAL_PREDICATES,
    TESTED_BY_PREDICATE,
    CodeGraphView,
    load_code_graph,
)

# Call pressure is a property of the call edge specifically, so the hotspot
# category always reads ``code:calls`` even when the caller narrows or widens the
# ``predicates`` selection that governs isolation and community structure.
CALL_PREDICATE = "code:calls"

# Distribution positions, not tuned counts. See the module docstring.
HOTSPOT_PERCENTILE = 75
COMMUNITY_PERCENTILE = 50

# A one-node community has no internal pair, so its density is undefined rather
# than zero, and it is already reported under ``isolated`` when it is a symbol.
# Ranking singletons as "thin" would restate that finding and drown the real
# ones, so the community category looks only at communities of two or more.
MIN_COMMUNITY_SIZE = 2

# Fixed rounding keeps float output stable across runs and platforms.
_PLACES = 4
_MAX_LIMIT = 1000


def knowledge_gaps(
    db: Database,
    *,
    tenant_id: str = "local",
    predicates: Iterable[str] | None = None,
    limit: int = 20,
    resolution: float = 1.0,
    max_nodes: int = DEFAULT_MAX_NODES,
) -> dict:
    """Report where the code graph shows thin or missing knowledge.

    Returns three categories (``isolated``, ``untested_hotspots``,
    ``thin_communities``), each capped at ``limit`` entries, alongside honest
    coverage figures in ``summary``, the graph-derived cuts in ``thresholds``,
    and the standing caveats in ``why``. An empty graph, a graph with no test
    symbol, and a graph with no edge at all all return normally; none of the
    ratios divide by a count that can be zero.
    """
    limit = max(1, min(int(limit), _MAX_LIMIT))
    resolution = float(resolution)
    # An empty selection would leave every node isolated by construction, which
    # is an artifact of the argument rather than a finding, so it falls back.
    preds = tuple(sorted(set(predicates))) if predicates is not None else STRUCTURAL_PREDICATES
    if not preds:
        preds = STRUCTURAL_PREDICATES

    view = load_code_graph(db, tenant_id=tenant_id, max_nodes=max_nodes)
    symbols = view.symbol_ids()

    isolated_ids = _isolated_symbols(view, symbols, preds)
    hotspots, inbound_cut, observed_inbound = _untested_hotspots(view, symbols, limit)
    communities, size_cut, density_cut, community_count, analyzed = _thin_communities(view, preds, resolution, limit)

    # A ``code:tested_by`` edge runs from the tested symbol to the test symbol,
    # so the subject side is the covered definition.
    symbol_set = set(symbols)
    tested_ids = {e.subject_id for e in view.edges_for((TESTED_BY_PREDICATE,))} & symbol_set
    test_ids = {eid for eid in symbols if view.nodes[eid].is_test}
    testable = len(symbol_set - test_ids)

    return {
        "isolated": [_symbol_row(view, eid) for eid in isolated_ids[:limit]],
        "untested_hotspots": [row for _score, _name, row in hotspots[:limit]],
        "thin_communities": [row for _dens, _size, _name, row in communities[:limit]],
        "summary": {
            "total_nodes": len(view),
            "total_symbols": len(symbols),
            "test_symbols": len(test_ids),
            "testable_symbols": testable,
            "tested_symbols": len(tested_ids),
            # Tests cannot test themselves, so the denominator is the testable
            # population, not every symbol. Zero testable symbols reports 0.0
            # rather than raising or implying full coverage.
            "tested_symbol_fraction": round(len(tested_ids) / testable, _PLACES) if testable else 0.0,
            "isolated_count": len(isolated_ids),
            "untested_hotspot_count": len(hotspots),
            "community_count": community_count,
            "communities_analyzed": analyzed,
            "thin_community_count": len(communities),
        },
        "thresholds": {
            "inbound_percentile": HOTSPOT_PERCENTILE,
            "inbound_calls_min": inbound_cut,
            "inbound_observed_symbols": observed_inbound,
            "community_percentile": COMMUNITY_PERCENTILE,
            "community_size_max": size_cut,
            "community_density_max": round(density_cut, _PLACES),
            "min_community_size": MIN_COMMUNITY_SIZE,
            "note": (
                "cuts are nearest-rank percentiles of this graph's own observed distributions, "
                "so they are relative to this repository and not absolute quality bars"
            ),
        },
        "truncated": view.truncated,
        "why": {
            "predicates": list(preds),
            "call_predicate": CALL_PREDICATE,
            "tested_by_predicate": TESTED_BY_PREDICATE,
            "resolution": resolution,
            "limit": limit,
            "community_method": "modularity optimization",
            "isolated": (
                "a definition with no reference edge in either direction: either genuinely "
                "unreferenced or a reference the name-based resolver could not close"
            ),
            "untested_hotspots": (
                "inbound call pressure at or above the derived cut with no code:tested_by edge; "
                "this reports the absence of a test edge in the graph, not that the symbol is "
                "untested at runtime"
            ),
            "thin_communities": (
                "communities at or below this graph's median on both size and internal density; "
                "the ranking is relative to the graph, not an absolute standard"
            ),
            "note": (
                "structural and advisory, derived from graph shape alone; over-approximate and "
                "under-approximate in both directions, so treat every row as a prompt to look, "
                "never as a verdict"
            ),
        },
    }


def _percentile(values: Sequence[float], pct: float) -> float:
    """Nearest-rank percentile: the cut is always a value the graph actually has.

    Interpolating between two observations would invent a threshold no node or
    community exhibits, and for integer counts it would also yield a fractional
    cut that cannot be explained back to a reader. Nearest rank keeps every
    threshold traceable to a real observation. An empty distribution has no rank
    to take, so it returns 0.0 and the caller applies its own floor.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = math.ceil(pct / 100.0 * len(ordered)) - 1
    return float(ordered[max(0, min(rank, len(ordered) - 1))])


def _symbol_row(view: CodeGraphView, entity_id: str) -> dict:
    node = view.nodes[entity_id]
    return {
        "canonical": node.canonical,
        "display": node.display,
        "kind": node.kind,
        "path": node.path,
        "language": node.language,
    }


def _isolated_symbols(view: CodeGraphView, symbols: Sequence[str], preds: tuple[str, ...]) -> list[str]:
    """Definitions with no reference edge in either direction, by canonical name.

    Module nodes are excluded by ``symbol_ids``: a file with no import edge is
    unattached by construction and would swamp the real finding. Containment
    (``code:defines``) is likewise outside the structural selection, so being
    defined inside a file does not count as being referenced.
    """
    undirected = view.undirected_adjacency(preds)
    isolated = [eid for eid in symbols if not undirected.get(eid)]
    return sorted(isolated, key=lambda eid: (view.nodes[eid].canonical, eid))


def _untested_hotspots(
    view: CodeGraphView, symbols: Sequence[str], limit: int
) -> tuple[list[tuple[int, str, dict]], int, int]:
    """Symbols carrying derived-high inbound call pressure with no test edge.

    The cut is the upper quartile of the inbound-call counts actually observed in
    this graph, taken over the symbols that have at least one caller. Symbols
    with no caller are left out of the distribution on purpose: in most
    repositories they are the majority, and including that mass of zeros would
    drag any percentile down to zero and make every called symbol a hotspot. The
    cut never falls below one, because a symbol nothing calls carries no pressure
    to be a hotspot about.

    A symbol that is itself a test is never a candidate. Testing a test is not
    the gap being reported, and in this graph a test node cannot even accumulate
    inbound call edges, so the guard is deliberate rather than incidental.
    """
    inbound = view.in_adjacency((CALL_PREDICATE,))
    candidates = [eid for eid in symbols if not view.nodes[eid].is_test]
    observed = [len(inbound.get(eid, ())) for eid in candidates]
    positive = [n for n in observed if n > 0]
    cut = max(1, int(_percentile(positive, HOTSPOT_PERCENTILE)))

    tested_ids = {e.subject_id for e in view.edges_for((TESTED_BY_PREDICATE,))}
    rows: list[tuple[int, str, dict]] = []
    for eid in candidates:
        callers = inbound.get(eid, ())
        if eid in tested_ids or len(callers) < cut:
            continue
        row = _symbol_row(view, eid)
        row["inbound_calls"] = len(callers)
        # Callers are capped so one very central symbol cannot dominate the
        # payload; ``inbound_calls`` still carries the true count.
        ordered_callers = sorted(callers, key=lambda c: (view.label(c), c))
        row["callers"] = [view.label(c) for c in ordered_callers[:limit]]
        rows.append((len(callers), view.nodes[eid].canonical, row))
    # Heaviest pressure first, canonical name breaking ties.
    rows.sort(key=lambda item: (-item[0], item[1]))
    return rows, cut, len(positive)


def _thin_communities(
    view: CodeGraphView, preds: tuple[str, ...], resolution: float, limit: int
) -> tuple[list[tuple[float, int, str, dict]], int, float, int, int]:
    """Communities that are both small and weakly joined inside themselves.

    The partition comes from the shared view's modularity optimization over the
    selected predicates. For each community of at least ``MIN_COMMUNITY_SIZE``
    members, internal density is the count of distinct internal undirected pairs
    over the pairs that a fully connected community of that size could hold, so
    it is comparable across sizes.

    Both cuts are the median of what this graph shows: a community is thin when
    it sits at or below the median on size AND at or below the median on internal
    density. Using the graph's own middle rather than a fixed number keeps the
    analysis meaningful on a repository of any shape, at the cost of being
    relative; with a single qualifying community that community is trivially at
    its own median, which the ``why`` block states plainly.
    """
    assignment = view.communities(preds, resolution=resolution)
    members: dict[int, list[str]] = {}
    for eid in view.node_ids():
        cid = assignment.get(eid)
        if cid is None:
            continue
        members.setdefault(cid, []).append(eid)

    # ``weighted_undirected_edges`` already merges parallel edges and drops
    # self-loops, so counting pairs whose ends share a community counts each
    # internal edge exactly once.
    internal: dict[int, int] = {}
    for u, v, _w in view.weighted_undirected_edges(preds):
        cu, cv = assignment.get(u), assignment.get(v)
        if cu is not None and cu == cv:
            internal[cu] = internal.get(cu, 0) + 1

    eligible: list[tuple[int, int, int, float]] = []  # (community, size, edges, density)
    for cid, ids in members.items():
        size = len(ids)
        if size < MIN_COMMUNITY_SIZE:
            continue
        edge_count = internal.get(cid, 0)
        possible = size * (size - 1) / 2
        eligible.append((cid, size, edge_count, edge_count / possible))

    size_cut = int(_percentile([size for _c, size, _e, _d in eligible], COMMUNITY_PERCENTILE))
    density_cut = _percentile([d for _c, _s, _e, d in eligible], COMMUNITY_PERCENTILE)

    rows: list[tuple[float, int, str, dict]] = []
    for cid, size, edge_count, density in eligible:
        if size > size_cut or density > density_cut:
            continue
        names = sorted(view.nodes[eid].canonical for eid in members[cid])
        rows.append(
            (
                round(density, _PLACES),
                size,
                names[0],
                {
                    "community": cid,
                    "size": size,
                    "internal_edges": edge_count,
                    "density": round(density, _PLACES),
                    "members": names[:limit],
                    "members_truncated": len(names) > limit,
                },
            )
        )
    # Sparsest first, then smallest, then the alphabetically first member, which
    # is unique across communities and so makes the order total.
    rows.sort(key=lambda item: (item[0], item[1], item[2]))
    return rows, size_cut, density_cut, len(members), len(eligible)

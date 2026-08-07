"""Candidate dead code: definitions nothing in the graph reaches.

A symbol is a CANDIDATE when two things hold at once: no reference edge points at
it, and nothing marks it as a place execution can start. Both halves matter. The
first alone would flag every route handler and every scheduled job, because
nothing in the source calls those; the framework does, from outside.

This is ADVISORY and it over-flags by construction. The graph's reference edges
are name-based and structural, so an unreferenced symbol is either genuinely
unused or a reference the resolver could not close. The four ways that happens
are named in the output rather than left for the reader to remember, because a
list headed "dead code" that quietly includes a public API is worse than no list.

Read-only. Deterministic: every list has an explicit sort key with the canonical
name as the final tie-break.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..core.db import Database
from .analysis import (
    DEFAULT_MAX_NODES,
    MODULE_KIND,
    STRUCTURAL_PREDICATES,
    CodeGraphView,
    load_code_graph,
)
from .model import FRAMEWORK_PREDICATES, edge_predicate

# An inbound edge on one of these means something in the repository references
# the symbol. Containment (``code:defines``) is deliberately excluded: a method
# always has a parent, and counting that as a reference would make every nested
# definition permanently reachable.
REFERENCE_PREDICATES = STRUCTURAL_PREDICATES

# An inbound edge on one of these means a framework reaches the symbol from
# outside the source: a route dispatches to it, a scheduler invokes it, a
# template is rendered by it. Nothing in the code calls these, which is exactly
# why they must not be reported dead.
FRAMEWORK_INBOUND = tuple(edge_predicate(p) for p in FRAMEWORK_PREDICATES)

# Node kinds that ARE entry points rather than merely being reached from one.
ENTRY_POINT_KINDS = ("code:test", "code:route", "code:entrypoint")

# Documented entry-point names. Deliberately minimal: a longer list of plausible
# names (handler, run, execute, start) would suppress real findings on a guess
# about intent. These two are the names a runtime actually looks for.
ENTRY_POINT_NAMES = ("main", "__main__")

# Why a candidate may not be dead. Named in every result.
FALSE_POSITIVE_SOURCES = (
    {
        "source": "dynamic dispatch",
        "detail": (
            "a call made through a variable, a table, or a computed attribute is "
            "not a name the parser saw, so no edge was created"
        ),
    },
    {
        "source": "reflection",
        "detail": (
            "a symbol looked up by string at runtime (getattr, a plugin registry, "
            "a serialiser resolving a class name) leaves no reference in the source"
        ),
    },
    {
        "source": "framework registration",
        "detail": (
            "a handler a framework discovers by convention or decorator is "
            "invoked from outside the repository; only the registrations this "
            "build recognises produce an inbound framework edge"
        ),
    },
    {
        "source": "exported public interface",
        "detail": (
            "a symbol published for callers outside this repository has no "
            "in-repository caller by design and is not dead"
        ),
    },
)

_MAX_LIMIT = 1000


def dead_code_candidates(
    db: Database,
    *,
    tenant_id: str = "local",
    predicates: Iterable[str] | None = None,
    include_modules: bool = False,
    limit: int = 50,
    max_nodes: int = DEFAULT_MAX_NODES,
) -> dict:
    """Definitions with no inbound reference edge and no entry-point evidence.

    ``include_modules`` widens the scan to file-level module nodes, which are
    almost always unreferenced in a repository whose entry point is a script, so
    it is off by default rather than filling the list with files.
    """
    view = load_code_graph(db, tenant_id=tenant_id, max_nodes=max_nodes)
    return candidates_from_view(
        view, predicates=predicates, include_modules=include_modules, limit=limit
    )


def candidates_from_view(
    view: CodeGraphView,
    *,
    predicates: Iterable[str] | None = None,
    include_modules: bool = False,
    limit: int = 50,
) -> dict:
    """The analysis itself, over an already-loaded view."""
    limit = max(1, min(int(limit), _MAX_LIMIT))
    selection = tuple(predicates) if predicates is not None else REFERENCE_PREDICATES

    inbound_refs = view.in_adjacency(selection)
    inbound_framework = view.in_adjacency(FRAMEWORK_INBOUND)

    considered = [
        nid
        for nid in view.node_ids()
        if include_modules or view.nodes[nid].kind != MODULE_KIND
    ]

    candidates: list[dict] = []
    entry_points: list[dict] = []
    for nid in considered:
        node = view.nodes[nid]
        if inbound_refs.get(nid):
            continue
        reason = _entry_point_reason(node, bool(inbound_framework.get(nid)))
        if reason is not None:
            entry_points.append(
                {"canonical": node.canonical, "kind": node.kind, "entry_point_reason": reason}
            )
            continue
        candidates.append(
            {
                "canonical": node.canonical,
                "display": node.display,
                "kind": node.kind,
                "path": node.path,
                "language": node.language,
                "start_line": node.start_line,
                "lines": node.line_count,
                # Outbound degree is not evidence of being reached, but it does
                # tell a reader how much would go with it.
                "outbound_references": len(view.out_adjacency(selection).get(nid, ())),
            }
        )

    candidates.sort(key=lambda c: (-c["lines"], c["canonical"]))
    entry_points.sort(key=lambda e: e["canonical"])

    total_considered = len(considered)
    return {
        "candidates": candidates[:limit],
        "candidate_count": len(candidates),
        "returned": min(len(candidates), limit),
        "limit": limit,
        "entry_points_excluded": entry_points[:limit],
        "entry_points_excluded_count": len(entry_points),
        "summary": {
            "considered": total_considered,
            "with_inbound_reference": total_considered - len(candidates) - len(entry_points),
            "modules_included": include_modules,
            "predicates": list(selection),
        },
        "truncated": view.truncated,
        "false_positive_sources": [dict(f) for f in FALSE_POSITIVE_SOURCES],
        "why": {
            "advisory": (
                "ADVISORY. A candidate is a symbol with no inbound reference edge "
                "and no entry-point evidence in THIS graph. That is the absence of "
                "an edge, not proof the symbol is unused: the resolver is "
                "name-based and structural, so every source listed in "
                "false_positive_sources produces a symbol that looks dead and is "
                "not. Confirm before deleting anything."
            ),
            "reference_predicates": list(selection),
            "containment_excluded": (
                "code:defines is not counted as a reference; a nested definition "
                "always has a parent and counting that would make it unreachable "
                "from this analysis forever"
            ),
            "entry_point_evidence": {
                "kinds": list(ENTRY_POINT_KINDS),
                "names": list(ENTRY_POINT_NAMES),
                "framework_predicates": list(FRAMEWORK_INBOUND),
            },
            "truncated": (
                "the graph view hit its node or edge cap, so a symbol may look "
                "unreferenced only because the edge that references it was not read"
                if view.truncated
                else "the whole graph was read; no cap was hit"
            ),
        },
    }


def _entry_point_reason(node, has_framework_inbound: bool) -> str | None:
    """Why this symbol is a place execution can start, or None if it is not."""
    if node.kind in ENTRY_POINT_KINDS:
        return f"node kind {node.kind} is an entry point"
    if has_framework_inbound:
        return "a framework edge reaches it from outside the source"
    if node.display in ENTRY_POINT_NAMES:
        return f"named {node.display}, which a runtime looks for"
    return None

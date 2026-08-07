"""Find oversized definitions by line count, without reading any file.

The parser already recorded each definition's line span, so "which functions are
longer than 80 lines" is a graph query rather than a walk over the source. The
threshold is the CALLER's, not a derived cut: what counts as oversized is a team
judgement and this module refuses to make it for them. The graph's own observed
distribution is reported alongside the answer, by nearest-rank percentile, so a
caller choosing a threshold can see where their number sits in this repository
instead of guessing.

Read-only and deterministic: results are sorted longest first with the canonical
name as the tie-break.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from ..core.db import Database
from .analysis import (
    DEFAULT_MAX_NODES,
    SYMBOL_KINDS,
    CodeGraphView,
    load_code_graph,
)

_MAX_LIMIT = 1000

# Positions reported so a caller can place their threshold in this graph. These
# are distribution positions, not tuned constants, and none of them filters
# anything: the caller's threshold is the only cut applied.
REPORTED_PERCENTILES = (50, 75, 90, 99)


def large_symbols(
    db: Database,
    *,
    min_lines: int,
    kinds: Iterable[str] | None = None,
    path_prefix: str | None = None,
    tenant_id: str = "local",
    limit: int = 50,
    max_nodes: int = DEFAULT_MAX_NODES,
) -> dict:
    """Symbols whose recorded line span is at least ``min_lines``.

    ``kinds`` filters by symbol kind, with or without the ``code:`` prefix so a
    caller can pass either form. ``path_prefix`` narrows to one subtree.
    """
    view = load_code_graph(db, tenant_id=tenant_id, max_nodes=max_nodes)
    return large_symbols_from_view(
        view, min_lines=min_lines, kinds=kinds, path_prefix=path_prefix, limit=limit
    )


def large_symbols_from_view(
    view: CodeGraphView,
    *,
    min_lines: int,
    kinds: Iterable[str] | None = None,
    path_prefix: str | None = None,
    limit: int = 50,
) -> dict:
    """The query itself, over an already-loaded view."""
    min_lines = max(0, int(min_lines))
    limit = max(1, min(int(limit), _MAX_LIMIT))
    wanted_kinds = _normalise_kinds(kinds)
    prefix = (path_prefix or "").strip()

    scanned = 0
    unknown_span = 0
    lengths: list[int] = []
    matches: list[dict] = []
    for nid in view.node_ids():
        node = view.nodes[nid]
        if node.kind not in SYMBOL_KINDS:
            continue
        if wanted_kinds is not None and node.kind not in wanted_kinds:
            continue
        if prefix and not node.path.startswith(prefix):
            continue
        scanned += 1
        lines = node.line_count
        if lines == 0:
            # The parser recorded no span. Counting it as zero-length would put
            # it at the bottom of a distribution it is not part of.
            unknown_span += 1
            continue
        lengths.append(lines)
        if lines >= min_lines:
            matches.append(
                {
                    "canonical": node.canonical,
                    "display": node.display,
                    "kind": node.kind,
                    "path": node.path,
                    "language": node.language,
                    "start_line": node.start_line,
                    "end_line": node.end_line,
                    "lines": lines,
                }
            )

    matches.sort(key=lambda m: (-m["lines"], m["canonical"]))
    lengths.sort()

    return {
        "symbols": matches[:limit],
        "match_count": len(matches),
        "returned": min(len(matches), limit),
        "limit": limit,
        "filters": {
            "min_lines": min_lines,
            "kinds": sorted(wanted_kinds) if wanted_kinds is not None else None,
            "path_prefix": prefix or None,
        },
        "distribution": {
            "measured_symbols": len(lengths),
            "unknown_span": unknown_span,
            "scanned": scanned,
            "percentiles": {
                str(p): _percentile(lengths, p) for p in REPORTED_PERCENTILES
            },
            "max_lines": lengths[-1] if lengths else 0,
        },
        "truncated": view.truncated,
        "why": {
            "threshold_source": (
                "the caller's min_lines, applied as given. What counts as "
                "oversized is a team judgement, so no cut is derived here; the "
                "percentiles of this graph's own distribution are reported "
                "alongside by nearest rank so the number can be placed."
            ),
            "measurement": (
                "lines are end_line minus start_line plus one, as the parser "
                "recorded the definition's span. A symbol whose parser recorded no "
                "span is counted under unknown_span and excluded from both the "
                "matches and the distribution rather than being ranked as zero."
            ),
            "advisory": (
                "length is a smell, not a defect. A long generated table and a "
                "long tangled function have the same line count."
            ),
        },
    }


def _normalise_kinds(kinds: Iterable[str] | None) -> set[str] | None:
    """Accept ``function`` or ``code:function``; reject nothing silently."""
    if kinds is None:
        return None
    out: set[str] = set()
    for raw in kinds:
        name = str(raw).strip()
        if not name:
            continue
        out.add(name if name.startswith("code:") else f"code:{name}")
    return out or None


def _percentile(ordered: Sequence[int], percentile: int) -> int:
    """Nearest-rank percentile of an already-sorted sequence.

    Nearest rank rather than an interpolating percentile, so the reported cut is
    a length some symbol in this graph actually has.
    """
    if not ordered:
        return 0
    rank = max(1, math.ceil(percentile / 100 * len(ordered)))
    return int(ordered[min(rank, len(ordered)) - 1])

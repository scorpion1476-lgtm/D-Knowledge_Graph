"""Refactoring suggestions derived from community structure and coupling.

The review questions this project already generates ask about the code. This
module proposes a change to it: move this symbol, split this community, merge
these two, decouple this edge. That is a stronger claim, so each suggestion
carries three things the questions did not have to:

  the symbols involved      named, not described
  the measurement           the numbers that produced the suggestion
  why it may be wrong       the specific reason THIS suggestion could be bad

Every suggestion is worded as a suggestion, because that is what it is. The
partition it rests on is one run of a modularity optimizer over a name-based,
over-approximate graph. A community is a cluster of edges, not a module boundary
someone designed, and the two disagree often enough that acting on one of these
without reading the code would be a mistake.

The four distribution cuts are derived from this graph's own observed spread by
nearest-rank percentile. Three further cuts are fixed rather than derived, and
are published next to the derived ones with the reason: a majority is half, two
edges is what makes traffic plural, and a neighbourhood of fewer than three says
nothing about where a symbol belongs. Those are properties of the question, not
of the repository, so deriving them would be wrong rather than merely
unnecessary. Every cut applied appears in ``thresholds``, derived or not.

Output is deterministic: each list has an explicit sort key ending in the
canonical name. Read-only.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from ..core.db import Database
from .analysis import DEFAULT_MAX_NODES, STRUCTURAL_PREDICATES, load_code_graph

# Distribution positions, not tuned counts.
#
# A move is proposed only when a symbol's pull towards another community is in
# the upper quartile of the pulls observed here, so a graph where everything is
# mildly cross-linked does not produce a suggestion for every node.
MOVE_PULL_PERCENTILE = 75
# A split is proposed for communities in the top decile of size whose internal
# density is at or below this graph's median, because a large community that is
# densely joined is a cohesive subsystem rather than a bag.
SPLIT_SIZE_PERCENTILE = 90
SPLIT_DENSITY_PERCENTILE = 50
# A merge is proposed when the edges between two communities are in the upper
# decile of observed inter-community traffic.
MERGE_TRAFFIC_PERCENTILE = 90

# A symbol must have at least this many neighbours before its neighbourhood says
# anything about where it belongs. One neighbour in another community is not a
# pull, it is an edge.
MIN_NEIGHBOURS_FOR_MOVE = 3

# Two more cuts that are NOT derived from the distribution, and are reported
# alongside the ones that are, because a threshold nobody can see is a threshold
# nobody can check.
#
# Neither is a value tuned to a corpus, which is why deriving them would be
# wrong rather than merely unnecessary. MOVE_MAJORITY_MIN is the definition of a
# majority: at or below half, the neighbourhood is split rather than pulling
# anywhere, and proposing a move would be a coin toss whatever the distribution
# looks like. MERGE_MIN_CROSSING is what makes "edges between two communities"
# plural: one crossing edge is a reference, not traffic. Both are properties of
# the question, not of the repository, so they stay fixed and stay published.
MOVE_MAJORITY_MIN = 0.5
MERGE_MIN_CROSSING = 2

_PLACES = 4
_MAX_LIMIT = 500

KIND_MOVE = "move"
KIND_SPLIT = "split"
KIND_MERGE = "merge"
KIND_DECOUPLE = "decouple"

# Why each kind of suggestion may be wrong. Stated per kind rather than as one
# blanket caveat, because the failure modes genuinely differ.
RISKS = {
    KIND_MOVE: (
        "the community partition is a clustering of edges, not a design. A "
        "symbol can sit with the code it is called BY rather than the code it "
        "calls, deliberately, and this measurement cannot tell that apart from "
        "misplacement. Moving it may also break an import cycle rule or a "
        "published module path that callers outside this repository depend on."
    ),
    KIND_SPLIT: (
        "a large community with low internal density may be a deliberate façade "
        "or a package of independent utilities, which is exactly the shape this "
        "measurement flags. Splitting a genuinely cohesive subsystem because its "
        "internal calls happen to be sparse would add indirection for nothing."
    ),
    KIND_MERGE: (
        "heavy traffic between two communities can be a correct layered "
        "relationship, where one is meant to call the other a lot. Merging would "
        "erase the boundary that makes the dependency direction reviewable."
    ),
    KIND_DECOUPLE: (
        "an edge that crosses a community and a language boundary is often a "
        "deliberate adapter, which is the thing that SHOULD cross. The signal "
        "cannot distinguish an adapter from an accidental reach-through."
    ),
}


def _percentile(ordered: Sequence[float], percentile: int) -> float:
    if not ordered:
        return 0.0
    rank = max(1, math.ceil(percentile / 100 * len(ordered)))
    return float(ordered[min(rank, len(ordered)) - 1])


def refactor_suggestions(
    db: Database,
    *,
    tenant_id: str = "local",
    predicates: Iterable[str] | None = None,
    limit: int = 20,
    per_kind: int = 5,
    resolution: float = 1.0,
    max_nodes: int = DEFAULT_MAX_NODES,
) -> dict:
    """Propose moves, splits, merges, and decouplings, each with its evidence."""
    view = load_code_graph(db, tenant_id=tenant_id, max_nodes=max_nodes)
    preds = tuple(STRUCTURAL_PREDICATES) if predicates is None else tuple(sorted(set(predicates)))
    limit = max(1, min(int(limit), _MAX_LIMIT))
    per_kind = max(1, min(int(per_kind), _MAX_LIMIT))

    communities = view.communities(preds, resolution=resolution)
    neighbours = view.undirected_adjacency(preds)
    members: dict[int, list[str]] = {}
    for node_id, community in communities.items():
        members.setdefault(community, []).append(node_id)

    moves, move_cut = _moves(view, communities, neighbours, members, per_kind)
    splits, split_cuts = _splits(view, communities, neighbours, members, per_kind)
    merges, merge_cut = _merges(view, communities, neighbours, members, per_kind)
    decouples = _decouples(db, tenant_id, preds, resolution, max_nodes, per_kind)

    suggestions = moves + splits + merges + decouples
    suggestions.sort(key=lambda s: (-s["strength"], s["kind"], s["title"]))

    return {
        "suggestions": suggestions[:limit],
        "suggestion_count": len(suggestions),
        "returned": min(len(suggestions), limit),
        "by_kind": {
            KIND_MOVE: len(moves),
            KIND_SPLIT: len(splits),
            KIND_MERGE: len(merges),
            KIND_DECOUPLE: len(decouples),
        },
        "totals": {
            "nodes": len(view),
            "communities": len(members),
            "predicates": list(preds),
            "resolution": resolution,
        },
        "thresholds": {
            "move_pull_cut": move_cut,
            "move_pull_percentile": MOVE_PULL_PERCENTILE,
            "min_neighbours_for_move": MIN_NEIGHBOURS_FOR_MOVE,
            "move_majority_min": MOVE_MAJORITY_MIN,
            "merge_min_crossing": MERGE_MIN_CROSSING,
            "split_size_cut": split_cuts["size"],
            "split_density_cut": split_cuts["density"],
            "split_percentiles": {
                "size": SPLIT_SIZE_PERCENTILE,
                "density": SPLIT_DENSITY_PERCENTILE,
            },
            "merge_traffic_cut": merge_cut,
            "merge_traffic_percentile": MERGE_TRAFFIC_PERCENTILE,
            "derivation": (
                "move_pull_cut, split_size_cut, split_density_cut, and "
                "merge_traffic_cut are each the nearest-rank percentile of the "
                "corresponding distribution in THIS graph, so each is a value "
                "some node, community, or community pair here actually has. "
                "min_neighbours_for_move, move_majority_min, and "
                "merge_min_crossing are NOT derived and are fixed on purpose: "
                "they are properties of the question rather than of the "
                "repository (a majority is half, plural traffic is two edges, "
                "and a neighbourhood of fewer than three says nothing about "
                "where a symbol belongs). They are published here so a reader "
                "can see every cut that was applied, derived or not."
            ),
        },
        "truncated": view.truncated,
        "why": {
            "advisory": (
                "SUGGESTIONS, not findings. Each rests on one run of a "
                "modularity optimizer over a name-based, over-approximate graph. "
                "A community is a cluster of edges, not a module boundary anyone "
                "designed. Read the code before acting on any of these."
            ),
            "community_indices": (
                "community indices are arbitrary labels produced independently "
                "per run; never compare them across runs, compare co-membership"
            ),
            "per_kind_risks": dict(RISKS),
        },
    }


def _moves(view, communities, neighbours, members, per_kind) -> tuple[list[dict], float]:
    """Symbols whose neighbourhood pulls them towards another community."""
    pulls: list[tuple[float, str, int, int, int]] = []
    for node_id in view.node_ids():
        near = neighbours.get(node_id, set())
        if len(near) < MIN_NEIGHBOURS_FOR_MOVE:
            continue
        own = communities.get(node_id)
        counts: dict[int, int] = {}
        for other in near:
            counts[communities.get(other)] = counts.get(communities.get(other), 0) + 1
        foreign = {c: n for c, n in counts.items() if c != own}
        if not foreign:
            continue
        best_community = max(sorted(foreign), key=lambda c: (foreign[c], -c))
        pull = foreign[best_community] / len(near)
        pulls.append((pull, node_id, own, best_community, counts.get(own, 0)))

    cut = round(_percentile(sorted(p[0] for p in pulls), MOVE_PULL_PERCENTILE), _PLACES)
    out: list[dict] = []
    for pull, node_id, own, target, own_count in pulls:
        if pull < cut or pull <= MOVE_MAJORITY_MIN:
            # At or below half, the neighbourhood is not pulling anywhere: it is
            # split, and a move would be a coin toss.
            continue
        node = view.nodes[node_id]
        neighbour_names = sorted(
            view.label(n) for n in neighbours.get(node_id, set()) if communities.get(n) == target
        )
        out.append(
            {
                "kind": KIND_MOVE,
                "title": f"Consider moving {node.canonical} towards the community its neighbours are in",
                "suggestion": (
                    f"Most of what {node.canonical} is connected to sits in a "
                    f"different community from the one it was assigned. Consider "
                    f"whether it belongs alongside them."
                ),
                "symbols": [node.canonical],
                "related_symbols": neighbour_names[:10],
                "measurement": {
                    "neighbours": len(neighbours.get(node_id, set())),
                    "neighbours_in_own_community": own_count,
                    "neighbours_in_target_community": len(neighbour_names),
                    "pull": round(pull, _PLACES),
                    "pull_cut": cut,
                    "own_community_index": own,
                    "target_community_index": target,
                },
                "why_it_may_be_wrong": RISKS[KIND_MOVE],
                "strength": round(pull, _PLACES),
            }
        )
    out.sort(key=lambda s: (-s["strength"], s["symbols"][0]))
    return out[:per_kind], cut


def _density(members_of: list[str], neighbours) -> float:
    """Internal edges over possible internal pairs, in 0 to 1."""
    size = len(members_of)
    if size < 2:
        return 1.0
    member_set = set(members_of)
    internal = sum(len(neighbours.get(n, set()) & member_set) for n in members_of) / 2
    return internal / (size * (size - 1) / 2)


def _splits(view, communities, neighbours, members, per_kind) -> tuple[list[dict], dict]:
    """Large communities held together by few internal edges."""
    sizes = sorted(float(len(m)) for m in members.values())
    densities = sorted(_density(m, neighbours) for m in members.values() if len(m) >= 2)
    size_cut = round(_percentile(sizes, SPLIT_SIZE_PERCENTILE), _PLACES)
    density_cut = round(_percentile(densities, SPLIT_DENSITY_PERCENTILE), _PLACES)

    out: list[dict] = []
    for index in sorted(members):
        group = members[index]
        if len(group) < 2 or len(group) < size_cut:
            continue
        density = _density(group, neighbours)
        if density > density_cut:
            continue
        names = sorted(view.label(n) for n in group)
        paths = sorted({view.path_of(n) for n in group if view.path_of(n)})
        out.append(
            {
                "kind": KIND_SPLIT,
                "title": f"Consider splitting the community holding {names[0]} and {len(names) - 1} others",
                "suggestion": (
                    f"This community has {len(names)} members but only "
                    f"{round(density, _PLACES)} of the internal connections it "
                    f"could have, and its members are spread over {len(paths)} "
                    f"files. Consider whether it is really one thing."
                ),
                "symbols": names[:20],
                "related_symbols": paths[:10],
                "measurement": {
                    "members": len(names),
                    "size_cut": size_cut,
                    "internal_density": round(density, _PLACES),
                    "density_cut": density_cut,
                    "files_spanned": len(paths),
                    "community_index": index,
                },
                "why_it_may_be_wrong": RISKS[KIND_SPLIT],
                "strength": round(1.0 - density, _PLACES),
            }
        )
    out.sort(key=lambda s: (-s["strength"], s["title"]))
    return out[:per_kind], {"size": size_cut, "density": density_cut}


def _merges(view, communities, neighbours, members, per_kind) -> tuple[list[dict], float]:
    """Community pairs joined by unusually heavy traffic."""
    traffic: dict[tuple[int, int], int] = {}
    for node_id, near in neighbours.items():
        own = communities.get(node_id)
        for other in near:
            theirs = communities.get(other)
            if theirs == own:
                continue
            pair = (own, theirs) if own < theirs else (theirs, own)
            traffic[pair] = traffic.get(pair, 0) + 1
    # Each undirected edge was counted from both ends.
    traffic = {pair: count // 2 for pair, count in traffic.items() if count >= 2}
    # One crossing edge is a reference, not traffic between two regions.
    cut = round(_percentile(sorted(float(c) for c in traffic.values()), MERGE_TRAFFIC_PERCENTILE), _PLACES)

    out: list[dict] = []
    for (a, b), count in sorted(traffic.items()):
        if count < cut or count < MERGE_MIN_CROSSING:
            continue
        a_size, b_size = len(members.get(a, [])), len(members.get(b, []))
        internal = sum(
            len(neighbours.get(n, set()) & set(members.get(a, [])))
            for n in members.get(a, [])
        ) / 2 + sum(
            len(neighbours.get(n, set()) & set(members.get(b, [])))
            for n in members.get(b, [])
        ) / 2
        names = sorted(view.label(n) for n in members.get(a, []) + members.get(b, []))
        out.append(
            {
                "kind": KIND_MERGE,
                "title": f"Consider whether communities {a} and {b} are one thing",
                "suggestion": (
                    f"{count} edges cross between these two communities, against "
                    f"{int(internal)} inside them. Consider whether the boundary "
                    f"between them is carrying its weight."
                ),
                "symbols": names[:20],
                "related_symbols": [],
                "measurement": {
                    "crossing_edges": count,
                    "traffic_cut": cut,
                    "internal_edges": int(internal),
                    "community_sizes": {str(a): a_size, str(b): b_size},
                    "community_indices": [a, b],
                },
                "why_it_may_be_wrong": RISKS[KIND_MERGE],
                "strength": round(min(1.0, count / max(1.0, internal + count)), _PLACES),
            }
        )
    out.sort(key=lambda s: (-s["strength"], s["title"]))
    return out[:per_kind], cut


def _decouples(db, tenant_id, preds, resolution, max_nodes, per_kind) -> list[dict]:
    """The highest-scoring unexpected couplings, restated as a proposal."""
    from .coupling import unexpected_coupling

    scored = unexpected_coupling(
        db,
        tenant_id=tenant_id,
        predicates=preds,
        limit=per_kind,
        resolution=resolution,
        max_nodes=max_nodes,
    )
    out: list[dict] = []
    for row in scored["couplings"]:
        out.append(
            {
                "kind": KIND_DECOUPLE,
                "title": f"Consider decoupling {row['from']} from {row['to']}",
                "suggestion": (
                    f"This {row['predicate']} edge is surprising given the "
                    f"surrounding structure. Consider whether the dependency "
                    f"belongs, or whether it wants an interface between the two."
                ),
                "symbols": [row["from"], row["to"]],
                "related_symbols": [],
                "measurement": {
                    "coupling_score": row["score"],
                    "signals": row.get("signals", []),
                    "predicate": row["predicate"],
                    "weights": scored["weights"],
                },
                "why_it_may_be_wrong": RISKS[KIND_DECOUPLE],
                "strength": round(float(row["score"]), _PLACES),
            }
        )
    out.sort(key=lambda s: (-s["strength"], s["title"]))
    return out[:per_kind]

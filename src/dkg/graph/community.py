"""Mnemosyne: the base community detector (original code).

Mnemosyne is an original modularity-optimization detector in the greedy
local-move-and-aggregation lineage. It is written from scratch in pure standard
library Python with no third-party dependency and no copyleft code, so the
platform detects communities on its own without a third-party library. The
technique is described
generically as modularity optimization; the two well-known eponymous algorithms
of this family are deliberately not named or used.

Given a weighted, undirected graph it repeatedly (1) moves each node into the
neighbouring community that most increases modularity, then (2) aggregates each
community into a super-node and repeats on the smaller graph, until modularity
stops improving. It is deterministic: nodes are visited in a stable sorted order
and ties keep the current community, so a given graph always yields the same
partition. A resolution parameter scales the null-model term (higher resolution
yields more, smaller communities).

Community assignments are advisory and structural, not an authoritative account
of meaning; callers should treat them as an exploratory lens over link structure.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

Edge = tuple[str, str, float]


class _Graph:
    """Weighted undirected graph with self-loops, built for aggregation."""

    def __init__(self) -> None:
        self.adj: dict[str, dict[str, float]] = defaultdict(dict)
        self.self_loop: dict[str, float] = defaultdict(float)
        self.nodes: list[str] = []

    @classmethod
    def from_edges(cls, nodes: list[str], edges: list[Edge]) -> _Graph:
        g = cls()
        g.nodes = sorted(set(nodes))
        present = set(g.nodes)
        for u, v, w in edges:
            if u not in present or v not in present or w <= 0:
                continue
            if u == v:
                g.self_loop[u] += w
                continue
            g.adj[u][v] = g.adj[u].get(v, 0.0) + w
            g.adj[v][u] = g.adj[v].get(u, 0.0) + w
        return g

    def degree(self, node: str) -> float:
        return sum(self.adj[node].values()) + 2.0 * self.self_loop[node]

    def total_weight(self) -> float:
        inter = sum(sum(nbrs.values()) for nbrs in self.adj.values()) / 2.0
        return inter + sum(self.self_loop.values())


def _local_move(g: _Graph, comm: dict[str, int], m: float, resolution: float) -> bool:
    """One local-optimization sweep. Returns True if any node changed community."""
    if m <= 0:
        return False
    k = {n: g.degree(n) for n in g.nodes}
    sigma_tot: dict[int, float] = defaultdict(float)
    for n in g.nodes:
        sigma_tot[comm[n]] += k[n]

    improved_any = False
    changed = True
    while changed:
        changed = False
        for i in g.nodes:
            ci = comm[i]
            ki = k[i]
            sigma_tot[ci] -= ki
            # Weight from i into each neighbouring community.
            weight_to: dict[int, float] = defaultdict(float)
            for j, w in g.adj[i].items():
                weight_to[comm[j]] += w
            # Start from staying in the current community; move to a neighbouring
            # community only if it strictly increases modularity. Starting from
            # one-node-per-community, this merges nodes and always converges; ties
            # never trigger a move, so there is no oscillation.
            best_c = ci
            best_gain = weight_to.get(ci, 0.0) - resolution * sigma_tot[ci] * ki / (2.0 * m)
            for c, w_in in weight_to.items():
                if c == ci:
                    continue
                gain = w_in - resolution * sigma_tot[c] * ki / (2.0 * m)
                if gain > best_gain:
                    best_gain = gain
                    best_c = c
            sigma_tot[best_c] += ki
            comm[i] = best_c
            if best_c != ci:
                changed = True
                improved_any = True
    return improved_any


def modularity(g: _Graph, comm: dict[str, int], resolution: float = 1.0) -> float:
    m = g.total_weight()
    if m <= 0:
        return 0.0
    internal: dict[int, float] = defaultdict(float)
    tot: dict[int, float] = defaultdict(float)
    for n in g.nodes:
        c = comm[n]
        tot[c] += g.degree(n)
        internal[c] += g.self_loop[n]
        for j, w in g.adj[n].items():
            if comm[j] == c:
                internal[c] += w / 2.0  # each internal edge counted once
    q = 0.0
    for c in tot:
        q += internal[c] / m - resolution * (tot[c] / (2.0 * m)) ** 2
    return q


def coverage(g: _Graph, comm: dict[str, int]) -> float:
    """Fraction of total edge weight that falls inside communities (coherence)."""
    m = g.total_weight()
    if m <= 0:
        return 0.0
    inside = 0.0
    for n in g.nodes:
        inside += g.self_loop[n]
        for j, w in g.adj[n].items():
            if comm[j] == comm[n]:
                inside += w / 2.0
    return inside / m


def _aggregate(g: _Graph, comm: dict[str, int], label: dict[int, str]) -> _Graph:
    """Collapse each community into a super-node, preserving weights."""
    agg = _Graph()
    agg.nodes = sorted(set(label.values()))
    for n in g.nodes:
        cn = label[comm[n]]
        agg.self_loop[cn] += g.self_loop[n]
    for n in g.nodes:
        cn = label[comm[n]]
        for j, w in g.adj[n].items():
            cj = label[comm[j]]
            if cn == cj:
                # Each internal undirected edge is seen twice (n->j and j->n);
                # add half each time so the total internal weight is counted once.
                agg.self_loop[cn] += w / 2.0
            else:
                agg.adj[cn][cj] = agg.adj[cn].get(cj, 0.0) + w
    return agg


def detect_communities(
    nodes: list[str], edges: list[Edge], *, resolution: float = 1.0, max_levels: int = 50
) -> dict:
    """Detect communities via modularity optimization.

    Returns a dict with the node-to-community-index assignment, the number of
    communities, the final modularity, and the coverage (coherence).
    """
    base = _Graph.from_edges(nodes, edges)
    if not base.nodes:
        return {"assignment": {}, "num_communities": 0, "modularity": 0.0, "coverage": 0.0}

    current = base
    membership: dict[str, str] = {n: n for n in base.nodes}  # original node -> current-level node
    comm = {n: idx for idx, n in enumerate(current.nodes)}
    prev_q: float | None = None

    for level in range(max_levels):
        improved = _local_move(current, comm, current.total_weight(), resolution)
        q = modularity(current, comm, resolution)
        label = {c: f"L{level}:{c}" for c in set(comm.values())}
        # Fold this level's communities into the original-node membership.
        membership = {orig: label[comm[cur]] for orig, cur in membership.items()}
        if not improved or (prev_q is not None and q <= prev_q + 1e-12):
            break
        prev_q = q
        current = _aggregate(current, comm, label)
        comm = {n: idx for idx, n in enumerate(current.nodes)}

    # Compact the final super-labels to stable community indices.
    labels = sorted(set(membership.values()))
    index_of = {lab: i for i, lab in enumerate(labels)}
    assignment = {orig: index_of[lab] for orig, lab in membership.items()}
    final_comm = {n: assignment[n] for n in base.nodes}
    return {
        "assignment": assignment,
        "num_communities": len(labels),
        "modularity": round(modularity(base, final_comm, resolution), 6),
        "coverage": round(coverage(base, final_comm), 6),
    }


def communities_from_db(db, *, tenant_id: str = "local", resolution: float = 1.0) -> dict:
    """Build the entity graph from the shared relationships table and detect.

    Uses ``relationships.weight`` (edge confidence) as the edge weight; a missing
    weight defaults to 1.0. Returns the detection result plus per-community member
    lists with entity display names, ordered largest first. Read-only.
    """
    rows = db.fetchall(
        "SELECT subject_id, object_id, weight FROM relationships WHERE tenant_id = ?;",
        (tenant_id,),
    )
    edges: list[Edge] = []
    nodes: set[str] = set()
    for r in rows:
        s, o = r["subject_id"], r["object_id"]
        w = r["weight"]
        weight = float(w) if w is not None else 1.0
        nodes.add(s)
        nodes.add(o)
        edges.append((s, o, weight))

    result = detect_communities(sorted(nodes), edges, resolution=resolution)
    members: dict[int, list[str]] = defaultdict(list)
    for node, cid in result["assignment"].items():
        members[cid].append(node)

    display: dict[str, str] = {}
    if nodes:
        placeholders = ",".join("?" for _ in nodes)
        drows = db.fetchall(
            f"SELECT entity_id, display FROM entities WHERE entity_id IN ({placeholders});",
            tuple(sorted(nodes)),
        )
        display = {d["entity_id"]: d["display"] for d in drows}

    communities: list[dict[str, Any]] = [
        {
            "community": cid,
            "size": len(ids),
            "members": [{"entity_id": e, "display": display.get(e, e)} for e in sorted(ids)],
        }
        for cid, ids in members.items()
    ]
    communities.sort(key=lambda c: (-int(c["size"]), int(c["community"])))
    return {
        "algorithm": "mnemosyne",
        "method": "modularity-optimization",
        "resolution": resolution,
        "num_communities": result["num_communities"],
        "modularity": result["modularity"],
        "coverage": result["coverage"],
        "communities": communities,
        "note": "Communities are structural and advisory, not an authoritative account of meaning.",
    }


def communities_combined(db, *, tenant_id: str = "local", resolution: float = 1.0) -> dict:
    """The default path: a Mnemosyne base pass, then an Ariadne refinement pass.

    Both detectors genuinely run and both contribute. Mnemosyne produces the base
    partition with no third-party dependency, which is what keeps the core
    self-sufficient. Ariadne then runs its refinement over the same graph, and
    the partition with the higher modularity is the one returned.

    Selection is by measured modularity, not by preference: if the refinement
    does not improve the partition, the base pass is kept and the result says so.
    A detector that were always chosen regardless of its score would make the
    second pass decorative.

    Ariadne is optional. When it is absent the base pass is returned with the
    reason recorded, so the default path still works on an install that does not
    have it. Read-only throughout.
    """
    base = communities_from_db(db, tenant_id=tenant_id, resolution=resolution)
    passes: list[dict[str, Any]] = [
        {
            "detector": "mnemosyne",
            "role": "base",
            "num_communities": base["num_communities"],
            "modularity": base["modularity"],
            "coverage": base["coverage"],
            "ran": True,
        }
    ]

    try:
        from ..ariadne import detect_communities_ariadne
    except Exception as e:  # optional module absent
        result = dict(base)
        result.update(
            {
                "algorithm": "mnemosyne",
                "method": "modularity-optimization",
                "passes": passes
                + [{"detector": "ariadne", "role": "refinement", "ran": False, "reason": f"{e!r}"}],
                "selected_detector": "mnemosyne",
                "refinement_applied": False,
                "selection_reason": "ariadne is not installed; the base pass stands alone",
            }
        )
        return result

    refined = detect_communities_ariadne(db, resolution=resolution, tenant_id=tenant_id)
    passes.append(
        {
            "detector": "ariadne",
            "role": "refinement",
            "num_communities": refined.get("num_communities"),
            "modularity": refined.get("modularity"),
            "coverage": refined.get("coverage"),
            "ran": True,
        }
    )

    base_q = float(base.get("modularity") or 0.0)
    refined_q = float(refined.get("modularity") or 0.0)
    # Strictly greater, so a tie deterministically keeps the base partition.
    use_refined = refined_q > base_q
    chosen = refined if use_refined else base
    result = dict(chosen)
    result.update(
        {
            "algorithm": "mnemosyne+ariadne",
            "method": "modularity-optimization with refinement",
            "passes": passes,
            "selected_detector": "ariadne" if use_refined else "mnemosyne",
            "refinement_applied": use_refined,
            "selection_reason": (
                f"ariadne modularity {refined_q} beat mnemosyne {base_q}"
                if use_refined
                else f"ariadne modularity {refined_q} did not beat mnemosyne {base_q}; base pass kept"
            ),
            "note": "Communities are structural and advisory, not an authoritative account of meaning.",
        }
    )
    return result

"""Ariadne detector: modularity optimization with a refinement step.

Ariadne is the maintainer's original refinement community detector. It is an
original implementation in the modularity-optimization method family with an
added refinement step that guarantees each returned community is internally
connected, which the plain greedy method does not. The two well-known eponymous
algorithms of this family are deliberately not named or used.

Beyond the base method, Ariadne adds three genuine capabilities:

1. Semantic edge weighting. When the embeddings extra is present, each structural
   edge weight is scaled by the cosine similarity of the two entities' embeddings,
   so communities reflect semantic similarity and not only link structure.
2. Auto-tuned resolution. When no resolution is given, Ariadne sweeps a set of
   resolutions and keeps the partition with the best structural modularity.
3. Optional labelling. Each community can be given a short deterministic label
   derived from its members, computed locally and offline.

Quality is always reported on the structural graph (original edge weights), so
Ariadne and the default detector are measured on the same objective and can be
compared fairly.

One licence covers the whole repository, this module included: the
D-Knowledge Graph Source-Available Non-Commercial Licence in the LICENSE file at
the repository root. There are no separate or additional terms for this module
and it is not excluded from the built wheel.
"""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from typing import Any

from ..adapters.embedding import Model2VecEmbeddingAdapter, cosine
from ..graph.community import Edge, _Graph, coverage, detect_communities, modularity

_RESOLUTION_SWEEP = (0.5, 0.75, 1.0, 1.5, 2.0)
_STOPWORDS = {"the", "a", "an", "of", "and", "or", "to", "in", "for", "on", "with", "is"}


def _refine(assignment: dict[str, int], edges: list[Edge]) -> dict[str, int]:
    """Split each community into internally connected components.

    The greedy method can leave a community internally disconnected. This
    refinement guarantees each returned community is a single connected component
    in the structural graph, which is the well-connectedness the refinement
    family targets.
    """
    adj: dict[str, dict[str, float]] = defaultdict(dict)
    for u, v, w in edges:
        if u == v or w <= 0:
            continue
        adj[u][v] = adj[u].get(v, 0.0) + w
        adj[v][u] = adj[v].get(u, 0.0) + w

    comm_nodes: dict[int, list[str]] = defaultdict(list)
    for node, cid in assignment.items():
        comm_nodes[cid].append(node)

    refined: dict[str, int] = {}
    next_id = 0
    for _cid, nodes in sorted(comm_nodes.items()):
        nodeset = set(nodes)
        seen: set[str] = set()
        for start in sorted(nodes):
            if start in seen:
                continue
            component: list[str] = []
            queue = deque([start])
            seen.add(start)
            while queue:
                x = queue.popleft()
                component.append(x)
                for y in adj.get(x, {}):
                    if y in nodeset and y not in seen:
                        seen.add(y)
                        queue.append(y)
            for x in component:
                refined[x] = next_id
            next_id += 1
    return refined


def _augment_with_embeddings(
    edges: list[Edge], node_text: dict[str, str], adapter: Model2VecEmbeddingAdapter
) -> list[Edge]:
    """Scale each edge weight by the semantic similarity of its endpoints."""
    names = sorted(node_text)
    vectors = adapter.embed([node_text[n] for n in names])
    emb = dict(zip(names, vectors, strict=False))
    augmented: list[Edge] = []
    for u, v, w in edges:
        sim = 0.0
        if u in emb and v in emb:
            sim = max(0.0, cosine(emb[u], emb[v]))
        augmented.append((u, v, w * (1.0 + sim)))
    return augmented


def _auto_resolution(
    nodes: list[str], detection_edges: list[Edge], structural: _Graph
) -> tuple[float, dict[str, int], float]:
    """Pick the resolution whose refined partition has the best structural Q."""
    best: tuple[float, float, dict[str, int]] | None = None
    for r in _RESOLUTION_SWEEP:
        coarse = detect_communities(nodes, detection_edges, resolution=r)["assignment"]
        refined = _refine(coarse, detection_edges)
        q = modularity(structural, refined, resolution=1.0)
        ncomm = len(set(refined.values()))
        # Prefer higher modularity; break ties toward fewer communities.
        score = (round(q, 9), -ncomm)
        if best is None or score > (round(best[0], 9), -len(set(best[2].values()))):
            best = (q, r, refined)
    assert best is not None
    return best[1], best[2], best[0]


def _label(members: list[str]) -> str:
    tokens: Counter[str] = Counter()
    for name in members:
        for tok in name.lower().replace("_", " ").split():
            if tok.isalnum() and tok not in _STOPWORDS and len(tok) > 2:
                tokens[tok] += 1
    top = [t for t, _ in tokens.most_common(3)]
    return " / ".join(top) if top else "unlabelled"


def detect_communities_ariadne(
    db,
    *,
    resolution: float | None = None,
    use_embeddings: bool = True,
    label: bool = False,
    tenant_id: str = "local",
) -> dict:
    """Ariadne community detection over the shared entity graph. Read-only.

    Returns the detected communities with structural modularity and coverage
    (comparable to the default detector), the chosen resolution, whether semantic
    edge weighting was applied, and optional per-community labels.
    """
    rows = db.fetchall(
        "SELECT subject_id, object_id, weight FROM relationships WHERE tenant_id = ?;",
        (tenant_id,),
    )
    nodeset: set[str] = set()
    structural_edges: list[Edge] = []
    for r in rows:
        s, o = r["subject_id"], r["object_id"]
        w = float(r["weight"]) if r["weight"] is not None else 1.0
        nodeset.add(s)
        nodeset.add(o)
        structural_edges.append((s, o, w))
    nodes = sorted(nodeset)

    display: dict[str, str] = {}
    if nodes:
        placeholders = ",".join("?" for _ in nodes)
        drows = db.fetchall(
            f"SELECT entity_id, display FROM entities WHERE entity_id IN ({placeholders});",
            tuple(nodes),
        )
        display = {d["entity_id"]: d["display"] for d in drows}

    embeddings_used = False
    embeddings_reason: str | None = None
    detection_edges = structural_edges
    if use_embeddings and nodes:
        adapter = Model2VecEmbeddingAdapter()
        ok, why = adapter.available()
        if ok:
            node_text = {n: display.get(n, n) for n in nodes}
            detection_edges = _augment_with_embeddings(structural_edges, node_text, adapter)
            embeddings_used = True
        else:
            embeddings_reason = why

    structural = _Graph.from_edges(nodes, structural_edges)
    if not nodes:
        return {
            "algorithm": "ariadne",
            "method": "modularity-optimization-with-refinement",
            "resolution": resolution or 1.0,
            "embeddings_used": False,
            "num_communities": 0,
            "modularity": 0.0,
            "coverage": 0.0,
            "communities": [],
            "note": "Empty graph.",
        }

    if resolution is None:
        chosen_r, refined, q = _auto_resolution(nodes, detection_edges, structural)
    else:
        coarse = detect_communities(nodes, detection_edges, resolution=resolution)["assignment"]
        refined = _refine(coarse, detection_edges)
        chosen_r = resolution
        q = modularity(structural, refined, resolution=1.0)

    members: dict[int, list[str]] = defaultdict(list)
    for node, cid in refined.items():
        members[cid].append(node)

    communities: list[dict[str, Any]] = []
    for cid, ids in members.items():
        entry: dict[str, Any] = {
            "community": cid,
            "size": len(ids),
            "members": [{"entity_id": e, "display": display.get(e, e)} for e in sorted(ids)],
        }
        if label:
            entry["label"] = _label([display.get(e, e) for e in ids])
        communities.append(entry)
    communities.sort(key=lambda c: (-int(c["size"]), int(c["community"])))

    result = {
        "algorithm": "ariadne",
        "method": "modularity-optimization-with-refinement",
        "resolution": chosen_r,
        "auto_tuned_resolution": resolution is None,
        "embeddings_used": embeddings_used,
        "num_communities": len(members),
        "modularity": round(q, 6),
        "coverage": round(coverage(structural, refined), 6),
        "communities": communities,
        "note": "Ariadne refinement detector. Communities are advisory.",
    }
    if embeddings_reason:
        result["embeddings_note"] = f"structural only; embeddings unavailable: {embeddings_reason}"
    return result

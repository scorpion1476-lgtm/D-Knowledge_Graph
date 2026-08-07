"""Split an oversized community by re-detecting inside it.

Why this exists. Modularity optimization at one resolution can leave a community
holding a large share of the graph. Such a community is technically a valid
partition and practically useless: "these 900 symbols are one component" tells a
reader nothing they could act on. The usual fix is to raise the resolution, but
that re-partitions the WHOLE graph and disturbs communities that were already
good.

Splitting instead re-runs detection on the induced subgraph of the oversized
community alone, at a raised resolution, and keeps the split only when it
actually helps. Everything outside is untouched.

Two rules keep this honest:

* **The threshold comes from the graph, not from a constant.** A community is
  oversized when it holds more than a documented SHARE of the assigned nodes.
  The share is the only tunable and it is reported in the result with the node
  count it worked out to, so a reader can see what the cut actually was.

* **A split that does not improve the partition is discarded.** The measured
  modularity of the whole partition after splitting is compared against before.
  If it did not improve, the original community is kept and the result says the
  split was rejected and why. Splitting for its own sake would trade one useless
  answer for several.

Community indices are arbitrary labels produced independently per run, so a
split renumbers. Never compare indices across runs; compare co-membership.

Read-only with respect to the database. Iterative throughout.
"""

from __future__ import annotations

from collections import defaultdict

from .community import Edge, _Graph, detect_communities, modularity

# A community holding more than this share of the assigned nodes is oversized.
# One number, documented, reported in every result along with the node count it
# resolved to on the graph in hand.
DEFAULT_OVERSIZE_SHARE = 0.25

# Resolution multiplier for the re-detection inside an oversized community.
# Raising resolution favours smaller communities, which is the whole point of
# re-running rather than accepting the original grouping.
SPLIT_RESOLUTION_FACTOR = 2.0

# A split must improve whole-partition modularity by at least this much to be
# kept. Zero would keep splits that changed nothing measurable, which is how a
# partition ends up finely diced for no gain.
MIN_MODULARITY_GAIN = 1e-9


def split_oversized(
    nodes: list[str],
    edges: list[Edge],
    assignment: dict[str, int],
    *,
    oversize_share: float = DEFAULT_OVERSIZE_SHARE,
    resolution: float = 1.0,
    max_rounds: int = 3,
) -> dict:
    """Split every oversized community, keeping only the splits that help.

    ``max_rounds`` bounds the recursion: a split community can itself still be
    oversized, and re-splitting is allowed, but not without limit. Written as a
    loop rather than recursion so a pathological graph cannot exhaust the stack.
    """
    if not nodes or not assignment:
        return {
            "assignment": dict(assignment),
            "split": [],
            "rejected": [],
            "rounds": 0,
            "why": "nothing to split: the graph or the partition is empty",
        }

    # One graph object, built once: modularity is measured many times below and
    # rebuilding the adjacency each time would dominate the cost.
    graph = _Graph.from_edges(nodes, edges)
    current = dict(assignment)
    total = len(current)
    threshold = max(1, int(total * float(oversize_share)))
    by_pair = _edge_lookup(edges)
    split_records: list[dict] = []
    rejected: list[dict] = []
    rounds = 0

    for _ in range(max(1, int(max_rounds))):
        rounds += 1
        oversized = sorted(
            (cid for cid, size in _sizes(current).items() if size > threshold),
            key=lambda cid: (-_sizes(current)[cid], cid),
        )
        if not oversized:
            break
        changed = False
        for cid in oversized:
            members = sorted(n for n, c in current.items() if c == cid)
            induced = [
                (a, b, w)
                for (a, b), w in by_pair.items()
                if a in set(members) and b in set(members)
            ]
            if len(members) < 2 or not induced:
                rejected.append(
                    {
                        "community": cid,
                        "size": len(members),
                        "reason": "no internal edges to split on",
                    }
                )
                continue
            inner = detect_communities(
                members, induced, resolution=resolution * SPLIT_RESOLUTION_FACTOR
            )
            if inner["num_communities"] <= 1:
                rejected.append(
                    {
                        "community": cid,
                        "size": len(members),
                        "reason": "re-detection at a higher resolution found no sub-structure",
                    }
                )
                continue

            candidate = dict(current)
            next_label = max(current.values()) + 1
            remap: dict[int, int] = {}
            for node, sub in inner["assignment"].items():
                if sub not in remap:
                    remap[sub] = cid if not remap else next_label + len(remap) - 1
                candidate[node] = remap[sub]

            before = modularity(graph, current, resolution=resolution)
            after = modularity(graph, candidate, resolution=resolution)
            if after - before < MIN_MODULARITY_GAIN:
                rejected.append(
                    {
                        "community": cid,
                        "size": len(members),
                        "reason": "the split did not improve whole-partition modularity",
                        "modularity_before": round(before, 6),
                        "modularity_after": round(after, 6),
                    }
                )
                continue
            split_records.append(
                {
                    "community": cid,
                    "size_before": len(members),
                    "parts": inner["num_communities"],
                    "modularity_before": round(before, 6),
                    "modularity_after": round(after, 6),
                    "gain": round(after - before, 6),
                }
            )
            current = candidate
            changed = True
        if not changed:
            break

    split_records.sort(key=lambda r: (-r["gain"], r["community"]))
    rejected.sort(key=lambda r: (-r["size"], r["community"]))
    return {
        "assignment": current,
        "split": split_records,
        "rejected": rejected,
        "rounds": rounds,
        "threshold": {
            "oversize_share": oversize_share,
            "nodes_in_partition": total,
            "oversized_above_nodes": threshold,
            "derivation": (
                "a community is oversized when it holds more than the documented "
                "share of the assigned nodes; the share is the only tunable and the "
                "node count it resolved to on this graph is reported next to it"
            ),
        },
        "modularity": round(modularity(graph, current, resolution=resolution), 6),
        "why": (
            "An oversized community is re-detected on its own induced subgraph at a "
            "raised resolution, leaving the rest of the partition untouched. A split "
            "is KEPT only when it measurably improves whole-partition modularity; "
            "otherwise the original community stands and the rejection is reported "
            "with its numbers. Community indices are arbitrary labels produced per "
            "run and a split renumbers, so never compare them across runs; compare "
            "co-membership instead."
        ),
    }


def _sizes(assignment: dict[str, int]) -> dict[int, int]:
    out: dict[int, int] = defaultdict(int)
    for cid in assignment.values():
        out[cid] += 1
    return dict(out)


def _edge_lookup(edges: list[Edge]) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = {}
    for a, b, w in edges:
        out[(a, b)] = out.get((a, b), 0.0) + float(w)
    return out


def split_communities_from_db(
    db,
    *,
    tenant_id: str = "local",
    resolution: float = 1.0,
    oversize_share: float = DEFAULT_OVERSIZE_SHARE,
) -> dict:
    """Detect, then split whatever came out oversized. Read-only."""
    from .community import communities_combined

    rows = db.fetchall(
        "SELECT subject_id, object_id, weight FROM relationships WHERE tenant_id = ?;",
        (tenant_id,),
    )
    edges: list[Edge] = []
    nodes: set[str] = set()
    for r in rows:
        s, o = r["subject_id"], r["object_id"]
        weight = float(r["weight"]) if r["weight"] is not None else 1.0
        nodes.add(s)
        nodes.add(o)
        edges.append((s, o, weight))

    detected = communities_combined(db, tenant_id=tenant_id, resolution=resolution)
    assignment: dict[str, int] = {}
    for community in detected.get("communities", []):
        for member in community["members"]:
            assignment[member["entity_id"]] = int(community["community"])

    result = split_oversized(
        sorted(nodes),
        edges,
        assignment,
        oversize_share=oversize_share,
        resolution=resolution,
    )
    members: dict[int, list[str]] = defaultdict(list)
    for node, cid in result["assignment"].items():
        members[cid].append(node)
    display: dict[str, str] = {}
    if nodes:
        placeholders = ",".join("?" for _ in nodes)
        drows = db.fetchall(
            f"SELECT entity_id, display FROM entities WHERE entity_id IN ({placeholders});",  # noqa: S608
            tuple(sorted(nodes)),
        )
        display = {d["entity_id"]: d["display"] for d in drows}

    communities: list[dict] = [
        {
            "community": cid,
            "size": len(ids),
            "members": [{"entity_id": e, "display": display.get(e, e)} for e in sorted(ids)],
        }
        for cid, ids in members.items()
    ]
    communities.sort(key=lambda c: (-int(c["size"]), int(c["community"])))
    return {
        "communities": communities,
        "num_communities": len(communities),
        "modularity_before": detected.get("modularity"),
        "modularity": result["modularity"],
        "split": result["split"],
        "rejected": result["rejected"],
        "threshold": result["threshold"],
        "rounds": result["rounds"],
        "why": result["why"],
    }

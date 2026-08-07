"""Structural execution-flow tracing over the shared code graph.

Forward traversal of ``code:calls`` from an entry symbol: what it calls, and what
those call, transitively. This is the complement of the reverse blast-radius
traversal. It is STRUCTURAL and over-approximate: reference resolution is
name-based and dynamic dispatch is not modelled, so a flow can over-flag. The
type-aware (language-server), dataflow, and taint refinements that raise flow
accuracy are deferred to Wave 4. Bounded by depth and a node cap; cycles
(recursion) are handled by not re-expanding a node already on the path.
"""

from __future__ import annotations

from collections import defaultdict, deque

from ..core.db import Database

FLOW_PREDICATE = "code:calls"
_MAX_CHAINS = 200


def _resolve_entity(db: Database, ident: str, tenant_id: str) -> dict | None:
    row = db.fetchone(
        "SELECT entity_id, canonical, display, kind FROM entities "
        "WHERE tenant_id=? AND kind LIKE 'code:%' AND (entity_id=? OR canonical=?) "
        "ORDER BY CASE WHEN kind IN ('code:function','code:method') THEN 0 ELSE 1 END LIMIT 1;",
        (tenant_id, ident, ident),
    )
    return dict(row) if row else None


def execution_flow(
    db: Database,
    ident: str,
    *,
    tenant_id: str = "local",
    depth: int = 5,
    max_nodes: int = 500,
    max_chains: int = _MAX_CHAINS,
) -> dict:
    """Trace the forward call flow from an entry symbol.

    Returns the reached callees, the traversed call edges with confidence, and
    enumerated call chains (paths from the entry to leaves), all labelled
    structural and over-approximate.
    """
    root = _resolve_entity(db, ident, tenant_id)
    if root is None:
        return {"root": None, "reached": [], "edges": [], "chains": [], "why": "entity not found"}
    depth = max(1, min(int(depth), 20))
    max_nodes = max(1, min(int(max_nodes), 5000))
    max_chains = max(1, min(int(max_chains), 2000))

    root_id = root["entity_id"]
    seen: dict[str, dict] = {root_id: root}
    adj: dict[str, list[tuple[str, float]]] = defaultdict(list)
    edges: list[dict] = []
    frontier: deque[tuple[str, int]] = deque([(root_id, 0)])
    truncated = False

    while frontier:
        cur, d = frontier.popleft()
        if d >= depth:
            continue
        rows = db.fetchall(
            """
            SELECT r.object_id, r.weight, e.canonical, e.display, e.kind
            FROM relationships r JOIN entities e ON e.entity_id = r.object_id
            WHERE r.tenant_id=? AND r.subject_id=? AND r.predicate='code:calls'
            ORDER BY r.object_id LIMIT 2000;
            """,
            (tenant_id, cur),
        )
        for row in rows:
            oid = row["object_id"]
            adj[cur].append((oid, float(row["weight"])))
            edges.append(
                {
                    "from": seen.get(cur, {}).get("canonical", cur),
                    "to": row["canonical"],
                    "confidence": float(row["weight"]),
                }
            )
            if oid in seen:
                continue  # cycle or shared callee already recorded
            if len(seen) >= max_nodes:
                truncated = True
                break
            seen[oid] = {
                "entity_id": oid,
                "canonical": row["canonical"],
                "display": row["display"],
                "kind": row["kind"],
            }
            frontier.append((oid, d + 1))

    chains, chains_truncated = _enumerate_chains(root_id, adj, seen, depth, max_chains)
    reached = [
        {"canonical": v["canonical"], "display": v["display"], "kind": v["kind"]}
        for eid, v in seen.items()
        if eid != root_id
    ]
    return {
        "root": {"canonical": root["canonical"], "display": root["display"], "kind": root["kind"]},
        "reached": reached,
        "reached_count": len(reached),
        "edges": edges,
        "chains": chains,
        "truncated": truncated,
        "chains_truncated": chains_truncated,
        "why": {
            "algorithm": "forward-bfs",
            "predicate": FLOW_PREDICATE,
            "depth": depth,
            "max_nodes": max_nodes,
            "note": "structural and over-approximate; type-aware, dataflow, and taint refinements deferred to Wave 4",
        },
    }


def _enumerate_chains(
    root_id: str,
    adj: dict[str, list[tuple[str, float]]],
    seen: dict[str, dict],
    depth: int,
    max_chains: int,
) -> tuple[list[list[str]], bool]:
    """Depth-first enumeration of call chains from the entry to leaves.

    A node already on the current path is not re-expanded (recursion is shown
    once and stopped). The number of chains is bounded; the returned flag reports
    whether the enumeration was clipped at the cap, so the caller is not misled
    into thinking the chain set is complete.
    """
    chains: list[list[str]] = []
    state = {"truncated": False}

    def name(eid: str) -> str:
        return seen.get(eid, {}).get("canonical", eid)

    def dfs(node: str, path: list[str], path_ids: set[str], d: int) -> None:
        if len(chains) >= max_chains:
            state["truncated"] = True
            return
        children = [c for c, _w in adj.get(node, [])]
        if not children or d >= depth:
            chains.append(list(path))
            return
        expandable = [c for c in children if c not in path_ids]
        if not expandable:
            chains.append(list(path))
            return
        for c in expandable:
            if len(chains) >= max_chains:
                state["truncated"] = True
                return
            dfs(c, path + [name(c)], path_ids | {c}, d + 1)

    dfs(root_id, [name(root_id)], {root_id}, 0)
    return chains, state["truncated"]

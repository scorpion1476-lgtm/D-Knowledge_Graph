"""Structural blast-radius over the code graph.

Bounded reverse traversal: from a changed entity, follow who calls it, who
inherits from it, and who imports its module, up to a depth and node cap. This is
STRUCTURAL and over-approximate: it can over-flag (dynamic dispatch, name-based
edges). The evidence-graded, git co-change, and dataflow refinements that reduce
over-flagging are deferred to Wave 4.
"""

from __future__ import annotations

from collections import deque

from ..core.db import Database

IMPACT_PREDICATES = ("code:calls", "code:inherits", "code:imports")


def _resolve_entity(db: Database, ident: str, tenant_id: str) -> dict | None:
    row = db.fetchone(
        "SELECT entity_id, canonical, display, kind FROM entities WHERE tenant_id=? AND kind LIKE 'code:%' AND (entity_id=? OR canonical=?) LIMIT 1;",
        (tenant_id, ident, ident),
    )
    return dict(row) if row else None


def blast_radius(db: Database, ident: str, *, tenant_id: str = "local", depth: int = 3, max_nodes: int = 500) -> dict:
    root = _resolve_entity(db, ident, tenant_id)
    if root is None:
        return {"root": None, "impacted": [], "impacted_count": 0, "why": "entity not found"}
    depth = max(1, min(int(depth), 10))
    max_nodes = max(1, min(int(max_nodes), 5000))
    seen: dict[str, dict] = {root["entity_id"]: root}
    frontier: deque[tuple[str, int]] = deque([(root["entity_id"], 0)])
    truncated = False
    while frontier:
        cur, d = frontier.popleft()
        if d >= depth:
            continue
        rows = db.fetchall(
            """
            SELECT r.subject_id, r.predicate, r.weight, e.canonical, e.display, e.kind
            FROM relationships r JOIN entities e ON e.entity_id = r.subject_id
            WHERE r.tenant_id=? AND r.object_id=?
              AND r.predicate IN ('code:calls','code:inherits','code:imports')
            LIMIT 2000;
            """,
            (tenant_id, cur),
        )
        for row in rows:
            sid = row["subject_id"]
            if sid in seen:
                continue
            if len(seen) >= max_nodes:
                truncated = True
                break
            seen[sid] = {"entity_id": sid, "canonical": row["canonical"], "display": row["display"], "kind": row["kind"]}
            frontier.append((sid, d + 1))
    impacted = [
        {"canonical": v["canonical"], "display": v["display"], "kind": v["kind"]}
        for eid, v in seen.items()
        if eid != root["entity_id"]
    ]
    return {
        "root": {"canonical": root["canonical"], "display": root["display"], "kind": root["kind"]},
        "impacted": impacted,
        "impacted_count": len(impacted),
        "truncated": truncated,
        "why": {
            "algorithm": "reverse-bfs",
            "predicates": list(IMPACT_PREDICATES),
            "depth": depth,
            "max_nodes": max_nodes,
            "note": "structural and over-approximate; refinements deferred to Wave 4",
        },
    }


def blast_radius_for_file(db: Database, path: str, *, tenant_id: str = "local", depth: int = 3, max_nodes: int = 500) -> dict:
    ents = db.fetchall(
        "SELECT entity_id, canonical FROM entities WHERE tenant_id=? AND kind LIKE 'code:%' AND (canonical=? OR canonical LIKE ?);",
        (tenant_id, path, f"{path}::%"),
    )
    own = {e["canonical"] for e in ents}
    impacted: dict[str, dict] = {}
    for e in ents:
        r = blast_radius(db, e["entity_id"], tenant_id=tenant_id, depth=depth, max_nodes=max_nodes)
        for imp in r["impacted"]:
            if imp["canonical"] not in own:
                impacted[imp["canonical"]] = imp
    return {
        "file": path,
        "impacted": list(impacted.values()),
        "impacted_count": len(impacted),
        "why": "structural and over-approximate; refinements deferred to Wave 4",
    }

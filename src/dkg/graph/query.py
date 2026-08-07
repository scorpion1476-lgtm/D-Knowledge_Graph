"""Graph queries: resolve, neighbourhood, path."""

from __future__ import annotations

from collections import deque
from typing import Any

from ..core.db import Database


def _resolve_entity(db: Database, ident: str) -> dict | None:
    row = db.fetchone(
        "SELECT * FROM entities WHERE entity_id = ? OR LOWER(canonical) = LOWER(?) LIMIT 1;",
        (ident, ident),
    )
    return dict(row) if row else None


def neighbourhood(
    db: Database, ident: str, *, depth: int = 2, max_nodes: int = 100
) -> dict[str, Any]:
    root = _resolve_entity(db, ident)
    if root is None:
        return {"root": None, "nodes": [], "edges": [], "why": "entity not found"}

    depth = max(0, min(int(depth), 5))
    max_nodes = max(1, min(int(max_nodes), 1000))

    seen: dict[str, dict] = {root["entity_id"]: root}
    edges: list[dict] = []
    frontier: deque[tuple[str, int]] = deque([(root["entity_id"], 0)])
    truncated = False

    while frontier:
        current, d = frontier.popleft()
        if d >= depth:
            continue
        if len(seen) >= max_nodes:
            truncated = True
            break
        rows = db.fetchall(
            """
            SELECT relationship_id, subject_id, predicate, object_id, support, weight
            FROM relationships
            WHERE subject_id = ? OR object_id = ?
            LIMIT 500;
            """,
            (current, current),
        )
        for r in rows:
            edges.append(dict(r))
            for neighbour in (r["subject_id"], r["object_id"]):
                if neighbour == current or neighbour in seen:
                    continue
                if len(seen) >= max_nodes:
                    truncated = True
                    break
                ent = _resolve_entity(db, neighbour)
                if ent is None:
                    continue
                seen[neighbour] = ent
                frontier.append((neighbour, d + 1))
    return {
        "root": root,
        "nodes": list(seen.values()),
        "edges": edges,
        "truncated": truncated,
        "why": {
            "algorithm": "bfs",
            "depth": depth,
            "max_nodes": max_nodes,
            "reached": len(seen),
        },
    }

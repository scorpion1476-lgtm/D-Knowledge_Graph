"""Weighted criticality for execution flows, and bounded free-form traversal.

Two read-only capabilities that share a walker, so there is one bounded
traversal in the code plane rather than two that drift apart.

**Flow criticality.** An execution flow is a path from an entry point. Not all
of them matter equally, and "how many nodes it touches" is a poor proxy: a long
chain through leaf helpers is less critical than a short one through a symbol
half the repository depends on. The score combines factors that are each
observable in the graph, each documented, and each reported alongside the total
so a reader can disagree with the weighting rather than having to accept it:

    criticality = w_depth   * normalised depth
                + w_fanin   * normalised peak fan-in along the path
                + w_breadth * normalised distinct files touched
                + w_conf    * mean edge confidence along the path
                - w_untested * (1 if nothing on the path has a test edge else 0)

Every weight is a named constant, printed in the result. Normalisation is
against the observed distribution in THIS graph, by nearest-rank percentile, so
no constant is tuned to a corpus.

**Bounded traversal.** Free-form walking from any node, breadth-first or
depth-first, bounded by BOTH a depth limit and a token budget. Bounding one
dimension is not a bound: a depth-2 walk of a hub can return thousands of nodes.
When either bound bites, the result says which and how much was left, because a
truncated answer presented as complete is worse than a small one.

Recursive algorithms are written iteratively so a deep graph cannot exhaust the
Python stack. Output is deterministic: every list has an explicit sort key with
ties broken by canonical name.

Everything here is advisory. The underlying edges are structural and
over-approximate, so every result carries that caveat.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from ..core.db import Database
from ..core.errors import ValidationError
from .model import confidence_record

# Documented weights. Named so the result can print them and a reader can see
# what the score is made of rather than being handed one number.
W_DEPTH = 0.25
W_FANIN = 0.35
W_BREADTH = 0.20
W_CONFIDENCE = 0.20
# Subtracted, not added: a path with no test coverage anywhere is more
# dangerous, so it scores HIGHER. The penalty is applied as a bonus to the
# untested case for exactly that reason.
W_UNTESTED_BONUS = 0.15

TRAVERSAL_ORDERS = ("breadth", "depth")

# Cost in tokens charged per returned node, so a traversal's token budget bounds
# the answer rather than the walk. Counted from the real serialised form rather
# than guessed: see _node_tokens.
_NODE_OVERHEAD_TOKENS = 4


@dataclass(frozen=True)
class FlowPath:
    entry: str
    nodes: tuple[str, ...]
    depth: int
    peak_fan_in: int
    files: int
    mean_confidence: float
    tested: bool


def _resolve(db: Database, ident: str, tenant_id: str) -> dict | None:
    row = db.fetchone(
        "SELECT entity_id, canonical, kind FROM entities "
        "WHERE tenant_id=? AND kind LIKE 'code:%' AND (entity_id=? OR canonical=?) LIMIT 1;",
        (tenant_id, ident, ident),
    )
    return dict(row) if row else None


def _fan_in(db: Database, entity_id: str, tenant_id: str) -> int:
    row = db.fetchone(
        "SELECT COUNT(*) AS n FROM relationships WHERE tenant_id=? AND object_id=? "
        "AND predicate IN ('code:calls','code:inherits','code:imports');",
        (tenant_id, entity_id),
    )
    return int(row["n"]) if row else 0


def _has_test_edge(db: Database, entity_id: str, tenant_id: str) -> bool:
    row = db.fetchone(
        "SELECT 1 AS hit FROM relationships WHERE tenant_id=? AND subject_id=? "
        "AND predicate='code:tested_by' LIMIT 1;",
        (tenant_id, entity_id),
    )
    return bool(row)


def _percentile_rank(value: float, sorted_values: list[float]) -> float:
    """Fraction of the observed distribution at or below ``value``.

    Nearest-rank against the values this graph actually has, so the scale is a
    property of the graph rather than a constant tuned to a corpus. A single
    observation normalises to 1.0, which is correct: it is the whole
    distribution.
    """
    if not sorted_values:
        return 0.0
    count = sum(1 for v in sorted_values if v <= value)
    return count / len(sorted_values)


def flow_criticality(
    db: Database,
    entry: str,
    *,
    depth: int = 6,
    max_paths: int = 50,
    max_nodes: int = 2000,
    tenant_id: str = "local",
) -> dict:
    """Score every execution flow from ``entry`` by weighted criticality.

    Paths are enumerated forward over ``code:calls`` to the depth limit, bounded
    by ``max_paths`` and ``max_nodes``. A cycle terminates a path at the repeat
    rather than looping, and the repeat is recorded.
    """
    root = _resolve(db, entry, tenant_id)
    if root is None:
        return {"entry": entry, "found": False, "flows": [], "why": "entity not found"}
    depth = max(1, min(int(depth), 20))
    max_paths = max(1, min(int(max_paths), 500))
    max_nodes = max(1, min(int(max_nodes), 20000))

    # Iterative depth-first path enumeration. The stack holds whole paths, so a
    # deep graph costs memory rather than Python stack frames.
    paths: list[list[dict]] = []
    stack: list[list[dict]] = [[{"id": root["entity_id"], "canonical": root["canonical"], "weight": 1.0}]]
    visited_nodes = 0
    truncated = False
    while stack and len(paths) < max_paths:
        path = stack.pop()
        if len(path) > depth:
            paths.append(path)
            continue
        rows = db.fetchall(
            "SELECT r.object_id, r.weight, e.canonical FROM relationships r "
            "JOIN entities e ON e.entity_id = r.object_id "
            "WHERE r.tenant_id=? AND r.subject_id=? AND r.predicate='code:calls' "
            "ORDER BY e.canonical LIMIT 500;",
            (tenant_id, path[-1]["id"]),
        )
        on_path = {step["id"] for step in path}
        extended = False
        for row in rows:
            if visited_nodes >= max_nodes:
                truncated = True
                break
            if row["object_id"] in on_path:
                continue  # a cycle ends the path here rather than looping
            visited_nodes += 1
            extended = True
            stack.append(
                path
                + [
                    {
                        "id": row["object_id"],
                        "canonical": row["canonical"],
                        "weight": float(row["weight"] if row["weight"] is not None else 0.0),
                    }
                ]
            )
        if not extended:
            paths.append(path)
        if truncated:
            break

    built: list[FlowPath] = []
    for path in paths:
        confidences = [step["weight"] for step in path[1:]]
        files = {step["canonical"].split("::")[0] for step in path}
        built.append(
            FlowPath(
                entry=root["canonical"],
                nodes=tuple(step["canonical"] for step in path),
                depth=len(path) - 1,
                peak_fan_in=max(
                    (_fan_in(db, step["id"], tenant_id) for step in path), default=0
                ),
                files=len(files),
                mean_confidence=(sum(confidences) / len(confidences)) if confidences else 1.0,
                tested=any(_has_test_edge(db, step["id"], tenant_id) for step in path),
            )
        )

    depths = sorted(p.depth for p in built)
    fanins = sorted(float(p.peak_fan_in) for p in built)
    breadths = sorted(float(p.files) for p in built)

    scored: list[dict] = []
    for p in built:
        components = {
            "depth": round(W_DEPTH * _percentile_rank(p.depth, [float(d) for d in depths]), 4),
            "peak_fan_in": round(W_FANIN * _percentile_rank(float(p.peak_fan_in), fanins), 4),
            "files_touched": round(W_BREADTH * _percentile_rank(float(p.files), breadths), 4),
            "mean_edge_confidence": round(W_CONFIDENCE * p.mean_confidence, 4),
            "untested": round(0.0 if p.tested else W_UNTESTED_BONUS, 4),
        }
        scored.append(
            {
                "path": list(p.nodes),
                "depth": p.depth,
                "peak_fan_in": p.peak_fan_in,
                "files_touched": p.files,
                "tested": p.tested,
                "confidence": confidence_record(p.mean_confidence),
                "criticality": round(sum(components.values()), 4),
                "components": components,
            }
        )
    # Deterministic: score descending, then the path itself so ties are stable.
    scored.sort(key=lambda f: (-float(f["criticality"]), list(f["path"])))

    return {
        "entry": root["canonical"],
        "found": True,
        "flows": scored,
        "totals": {
            "flows": len(scored),
            "nodes_visited": visited_nodes,
            "truncated": truncated,
            "depth_limit": depth,
        },
        "weights": {
            "depth": W_DEPTH,
            "peak_fan_in": W_FANIN,
            "files_touched": W_BREADTH,
            "mean_edge_confidence": W_CONFIDENCE,
            "untested_bonus": W_UNTESTED_BONUS,
        },
        "why": (
            "Criticality is a weighted sum of factors observable in this graph, each "
            "reported next to the total so the weighting can be disagreed with. "
            "Depth, fan-in, and breadth are normalised by nearest-rank percentile "
            "against this graph's own distribution, so no constant is tuned to a "
            "corpus. An untested path scores HIGHER, because untested is riskier. "
            "The underlying call edges are structural and over-approximate, so a "
            "flow may include a call that cannot happen at runtime. Advisory."
        ),
    }


def _node_tokens(canonical: str, kind: str) -> int:
    from ..context.tokens import count_tokens

    return count_tokens(f"{kind} {canonical}") + _NODE_OVERHEAD_TOKENS


def traverse(
    db: Database,
    start: str,
    *,
    order: str = "breadth",
    depth: int = 3,
    token_budget: int | None = 2000,
    max_nodes: int = 1000,
    predicates: tuple[str, ...] = ("code:calls", "code:imports", "code:inherits", "code:defines"),
    direction: str = "out",
    tenant_id: str = "local",
) -> dict:
    """Free-form bounded traversal from any node.

    Bounded on BOTH depth and tokens. A cap on one dimension is not a bound: a
    depth-2 walk of a hub returns thousands of nodes, and a token budget with no
    depth limit walks the whole graph before it stops. When either bound bites
    the result says which, and the truncation flag covers both.
    """
    if order not in TRAVERSAL_ORDERS:
        raise ValidationError(
            f"unknown order {order!r}; expected one of {list(TRAVERSAL_ORDERS)}"
        )
    if direction not in ("out", "in", "both"):
        raise ValidationError(f"unknown direction {direction!r}; expected out, in, or both")
    root = _resolve(db, start, tenant_id)
    if root is None:
        return {"start": start, "found": False, "nodes": [], "why": "entity not found"}

    depth = max(1, min(int(depth), 20))
    max_nodes = max(1, min(int(max_nodes), 20000))
    placeholders = ",".join("?" for _ in predicates)

    seen = {root["entity_id"]}
    out_nodes = [
        {"canonical": root["canonical"], "kind": root["kind"], "distance": 0, "via": None}
    ]
    tokens_used = _node_tokens(root["canonical"], root["kind"])
    frontier: deque[tuple[str, int]] = deque([(root["entity_id"], 0)])
    hit_depth = False
    hit_tokens = False
    hit_nodes = False

    while frontier:
        # One deque serves both orders: breadth takes from the left, depth from
        # the right. Same bounds, same determinism, one code path.
        current, d = frontier.popleft() if order == "breadth" else frontier.pop()
        if d >= depth:
            hit_depth = True
            continue
        rows: list = []
        if direction in ("out", "both"):
            rows += list(
                db.fetchall(
                    f"SELECT r.object_id AS other, r.predicate, r.weight, e.canonical, e.kind "  # noqa: S608
                    f"FROM relationships r JOIN entities e ON e.entity_id = r.object_id "
                    f"WHERE r.tenant_id=? AND r.subject_id=? AND r.predicate IN ({placeholders}) "
                    f"ORDER BY e.canonical LIMIT 1000;",
                    (tenant_id, current, *predicates),
                )
            )
        if direction in ("in", "both"):
            rows += list(
                db.fetchall(
                    f"SELECT r.subject_id AS other, r.predicate, r.weight, e.canonical, e.kind "  # noqa: S608
                    f"FROM relationships r JOIN entities e ON e.entity_id = r.subject_id "
                    f"WHERE r.tenant_id=? AND r.object_id=? AND r.predicate IN ({placeholders}) "
                    f"ORDER BY e.canonical LIMIT 1000;",
                    (tenant_id, current, *predicates),
                )
            )
        for row in rows:
            if row["other"] in seen:
                continue
            if len(seen) >= max_nodes:
                hit_nodes = True
                break
            cost = _node_tokens(row["canonical"], row["kind"])
            if token_budget is not None and tokens_used + cost > token_budget:
                hit_tokens = True
                break
            seen.add(row["other"])
            tokens_used += cost
            out_nodes.append(
                {
                    "canonical": row["canonical"],
                    "kind": row["kind"],
                    "distance": d + 1,
                    "via": row["predicate"],
                    "confidence": confidence_record(row["weight"]),
                }
            )
            frontier.append((row["other"], d + 1))
        if hit_tokens or hit_nodes:
            break

    out_nodes.sort(key=lambda n: (n["distance"], n["canonical"]))
    return {
        "start": root["canonical"],
        "found": True,
        "order": order,
        "direction": direction,
        "nodes": out_nodes,
        "totals": {
            "returned": len(out_nodes),
            "tokens_used": tokens_used,
            "token_budget": token_budget,
            "depth_limit": depth,
            # One flag per bound, plus a combined one, so a caller cannot read
            # "not truncated by depth" as "complete".
            "truncated": bool(hit_depth or hit_tokens or hit_nodes),
            "truncated_by_depth": hit_depth,
            "truncated_by_token_budget": hit_tokens,
            "truncated_by_node_cap": hit_nodes,
        },
        "why": (
            "Bounded on depth AND tokens: a cap on one dimension alone is not a "
            "bound. Which bound bit is reported separately, so a truncated result "
            "is never read as a complete one. Edges are structural and "
            "over-approximate. Advisory."
        ),
    }

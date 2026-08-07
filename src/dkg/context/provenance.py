"""Provenance-bounded context.

The usual way to give a model context is a fixed neighbourhood: take the seeds,
walk out N hops, send everything reached. It is simple and it scales badly, because
the cost of a hop grows with the degree of whatever you landed on, and most of
what comes back supports nothing.

The alternative here is to return exactly the evidence that supports the answer
and then stop. The graph already records which chunk supports which claim and
which entity a claim mentions, so "what supports this" is a lookup rather than a
guess. Where a question is about code rather than claims, the supporting set is
the symbols actually on the resolved paths, not everything within N hops.

Both strategies are implemented here so the benchmark can measure one against
the other rather than assert that one is better.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass

from ..core.db import Database
from .pack import Unit

_CODE_PREDICATES = ("code:calls", "code:imports", "code:inherits")


@dataclass
class ContextResult:
    strategy: str
    units: list[Unit]
    seeds: list[str]
    reached: int
    why: str

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "seeds": self.seeds,
            "units": [u.key for u in self.units],
            "unit_count": len(self.units),
            "reached": self.reached,
            "why": self.why,
        }


def _entity_ids(db: Database, canonicals: Sequence[str], tenant_id: str) -> dict[str, str]:
    if not canonicals:
        return {}
    placeholders = ",".join("?" * len(canonicals))
    rows = db.fetchall(
        f"SELECT entity_id, canonical FROM entities WHERE tenant_id=? AND canonical IN ({placeholders});",
        (tenant_id, *canonicals),
    )
    return {r["canonical"]: r["entity_id"] for r in rows}


def fixed_neighbourhood(
    db: Database,
    seeds: Sequence[str],
    *,
    tenant_id: str = "local",
    depth: int = 2,
    max_nodes: int = 400,
) -> ContextResult:
    """Everything within ``depth`` hops of the seeds, in either direction.

    The baseline strategy. Included so the comparison is against a real
    alternative rather than a straw man.
    """
    ids = _entity_ids(db, list(seeds), tenant_id)
    seen: dict[str, str] = {v: k for k, v in ids.items()}
    frontier: deque[tuple[str, int]] = deque((eid, 0) for eid in ids.values())
    while frontier:
        current, d = frontier.popleft()
        if d >= depth or len(seen) >= max_nodes:
            continue
        rows = db.fetchall(
            "SELECT r.subject_id, r.object_id, s.canonical AS s_can, o.canonical AS o_can "
            "FROM relationships r "
            "JOIN entities s ON s.entity_id = r.subject_id "
            "JOIN entities o ON o.entity_id = r.object_id "
            "WHERE r.tenant_id=? AND (r.subject_id=? OR r.object_id=?) "
            "AND r.predicate IN ('code:calls','code:imports','code:inherits') "
            "ORDER BY r.subject_id, r.object_id LIMIT 500;",
            (tenant_id, current, current),
        )
        for row in rows:
            for eid, can in ((row["subject_id"], row["s_can"]), (row["object_id"], row["o_can"])):
                if eid not in seen and len(seen) < max_nodes:
                    seen[eid] = can
                    frontier.append((eid, d + 1))
    keys = sorted(seen.values())
    return ContextResult(
        strategy="fixed_neighbourhood",
        units=[Unit(key=k, kind="code:context", text="", score=0.0) for k in keys],
        seeds=sorted(seeds),
        reached=len(keys),
        why=f"every node within {depth} hops of the seeds, capped at {max_nodes}",
    )


def provenance_bounded(
    db: Database,
    seeds: Sequence[str],
    *,
    tenant_id: str = "local",
    depth: int = 3,
    max_nodes: int = 400,
) -> ContextResult:
    """Only the nodes that actually support the seeds.

    For code seeds that means the resolved reverse-reachable set: the symbols
    that can actually reach the seed, which is what a change to it can break.
    Nodes that merely sit near a seed without a path to it are not evidence and
    are not returned.
    """
    ids = _entity_ids(db, list(seeds), tenant_id)
    supporting: dict[str, str] = {}
    frontier: deque[tuple[str, int]] = deque((eid, 0) for eid in ids.values())
    visited = set(ids.values())
    while frontier:
        current, d = frontier.popleft()
        if d >= depth or len(supporting) >= max_nodes:
            continue
        rows = db.fetchall(
            "SELECT r.subject_id, e.canonical FROM relationships r "
            "JOIN entities e ON e.entity_id = r.subject_id "
            "WHERE r.tenant_id=? AND r.object_id=? "
            "AND r.predicate IN ('code:calls','code:imports','code:inherits') "
            "ORDER BY e.canonical LIMIT 500;",
            (tenant_id, current),
        )
        for row in rows:
            sid = row["subject_id"]
            if sid in visited:
                continue
            visited.add(sid)
            if len(supporting) < max_nodes:
                supporting[sid] = row["canonical"]
                frontier.append((sid, d + 1))
    keys = sorted(set(seeds) | set(supporting.values()))
    return ContextResult(
        strategy="provenance_bounded",
        units=[Unit(key=k, kind="code:context", text="", score=0.0, required=k in set(seeds)) for k in keys],
        seeds=sorted(seeds),
        reached=len(keys),
        why=(
            "the seeds plus only the nodes with a resolved path to them, which is "
            "what a change to a seed can actually reach; adjacency without a path "
            "is not evidence"
        ),
    )


def claim_evidence_bounded(db: Database, claim_ids: Sequence[str], *, tenant_id: str = "local") -> ContextResult:
    """Exactly the chunks recorded as supporting the given claims."""
    if not claim_ids:
        return ContextResult("claim_evidence_bounded", [], [], 0, "no claims requested")
    placeholders = ",".join("?" * len(claim_ids))
    rows = db.fetchall(
        "SELECT c.claim_id, ch.chunk_id, ch.text FROM claims c "
        "JOIN chunks ch ON ch.chunk_id = c.chunk_id "
        f"WHERE c.tenant_id=? AND c.claim_id IN ({placeholders}) ORDER BY c.claim_id;",
        (tenant_id, *claim_ids),
    )
    units = [
        Unit(key=r["chunk_id"], kind="evidence:chunk", text=r["text"] or "", score=1.0, required=True)
        for r in rows
    ]
    return ContextResult(
        strategy="claim_evidence_bounded",
        units=units,
        seeds=sorted(claim_ids),
        reached=len(units),
        why="exactly the chunks the evidence ledger records as supporting these claims",
    )

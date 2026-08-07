"""Post-processing: the derived views, computed once and named as a stage.

Parsing and post-processing are different work with different costs. Parsing is
proportional to the files that changed; the derived views (community structure,
execution flows, the per-symbol risk index, the search index) are each a walk
over the whole graph and are proportional to everything. Folding them into
ingest means a large repository cannot separate the two, and computing them at
query time instead means the cheapest possible answer is never available and a
repeated question pays the full price every time.

So they are a named stage that can be run on its own, skipped entirely, or
reduced. Four levels, from cheapest to most complete:

    none      nothing derived; the graph is written and that is all
    minimal   community structure only
    standard  community structure and the execution-flow catalogue
    full      the above plus the per-symbol risk index and the search index

The level ACTUALLY APPLIED is reported, not the level requested, because a stage
whose capability is missing is reported not run rather than silently omitted. A
level is a request; the result says what happened.

Everything written here is derived and disposable. Each table can be dropped and
rebuilt from the entities and relationships it came from, every reader falls back
to computing live when a row is absent, and every row records the graph revision
it was computed against so a stale answer is identifiable as stale.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from ..core.db import Database
from .analysis import (
    DEFAULT_MAX_NODES,
    STRUCTURAL_PREDICATES,
    CodeGraphView,
    load_code_graph,
)
from .deadcode import ENTRY_POINT_KINDS, ENTRY_POINT_NAMES

# Ordered cheapest first. The order is the contract: a level runs every stage of
# the levels below it.
LEVELS = ("none", "minimal", "standard", "full")
DEFAULT_LEVEL = "standard"

STAGE_COMMUNITIES = "communities"
STAGE_FLOWS = "flows"
STAGE_RISK = "risk"
STAGE_INDEX = "index"
STAGES = (STAGE_COMMUNITIES, STAGE_FLOWS, STAGE_RISK, STAGE_INDEX)

LEVEL_STAGES: dict[str, tuple[str, ...]] = {
    "none": (),
    "minimal": (STAGE_COMMUNITIES,),
    "standard": (STAGE_COMMUNITIES, STAGE_FLOWS),
    "full": (STAGE_COMMUNITIES, STAGE_FLOWS, STAGE_RISK, STAGE_INDEX),
}

CALL_PREDICATE = "code:calls"

# Bounds. A pathological graph must not turn a stage into an unbounded walk.
MAX_FLOWS = 500
MAX_FLOW_DEPTH = 8
MAX_FLOW_STEPS = 200
MAX_COMMUNITY_MEMBERS_STORED = 500

_PLACES = 4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def graph_revision(db: Database, tenant_id: str = "local") -> str:
    """A short digest identifying the current code graph.

    Computed from the counts and the maximum entity and relationship ids, which
    move whenever the graph does. It is an identity, not a checksum: two
    revisions being equal means nothing observable changed, and that is enough
    to tell a stale derived row from a current one.
    """
    row = db.fetchone(
        "SELECT COUNT(*) AS n, COALESCE(MAX(entity_id),'') AS m FROM entities "
        "WHERE tenant_id=? AND kind LIKE 'code:%';",
        (tenant_id,),
    )
    erow = db.fetchone(
        "SELECT COUNT(*) AS n, COALESCE(MAX(relationship_id),'') AS m FROM relationships "
        "WHERE tenant_id=? AND predicate LIKE 'code:%';",
        (tenant_id,),
    )
    # An aggregate always returns a row, but the driver's type says it may not,
    # and a revision computed from a missing row would silently equal every
    # other missing-row revision. The empty case is spelled out instead.
    nodes = (row["n"], row["m"]) if row is not None else (0, "")
    edges = (erow["n"], erow["m"]) if erow is not None else (0, "")
    material = f"{nodes[0]}:{nodes[1]}:{edges[0]}:{edges[1]}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def resolve_level(level: str | None) -> str:
    """Normalise a requested level, refusing an unknown one loudly."""
    wanted = (level or DEFAULT_LEVEL).strip().lower()
    if wanted not in LEVEL_STAGES:
        from ..core.errors import ValidationError

        raise ValidationError(
            f"unknown post-processing level {level!r}; known: {', '.join(LEVELS)}"
        )
    return wanted


def run_postprocess(
    db: Database,
    *,
    level: str | None = DEFAULT_LEVEL,
    tenant_id: str = "local",
    resolution: float = 1.0,
    max_nodes: int = DEFAULT_MAX_NODES,
    stages: tuple[str, ...] | None = None,
) -> dict:
    """Run the derived-view stages for a level, or the named stages alone.

    ``stages`` overrides the level, which is what makes one stage re-runnable on
    its own without redoing the rest.
    """
    wanted_level = resolve_level(level)
    selected = tuple(stages) if stages is not None else LEVEL_STAGES[wanted_level]
    unknown = [s for s in selected if s not in STAGES]
    if unknown:
        from ..core.errors import ValidationError

        raise ValidationError(f"unknown post-processing stage(s): {', '.join(sorted(unknown))}")

    revision = graph_revision(db, tenant_id)
    view: CodeGraphView | None = None
    if selected:
        view = load_code_graph(db, tenant_id=tenant_id, max_nodes=max_nodes)

    reports: list[dict] = []
    for stage in STAGES:
        if stage not in selected:
            reports.append(
                {"stage": stage, "ran": False, "reason": f"not part of level {wanted_level!r}"}
            )
            continue
        assert view is not None
        reports.append(_run_stage(db, stage, view, tenant_id, revision, resolution))

    applied = [r["stage"] for r in reports if r["ran"]]
    payload = {
        "level_requested": wanted_level,
        # The level that actually held, which is not the requested one when a
        # stage could not run.
        "level_applied": _applied_level(applied),
        "stages": reports,
        "stages_run": applied,
        "graph_revision": revision,
        "ran_at": _now(),
        "why": {
            "derived": (
                "everything written here is derived and disposable: each table "
                "rebuilds from the entities and relationships it came from, and "
                "every reader computes live when a row is absent"
            ),
            "levels": {name: list(LEVEL_STAGES[name]) for name in LEVELS},
            "staleness": (
                "each row records the graph revision it was computed against, so "
                "a derived answer from an older graph is identifiable as stale "
                "rather than served as current"
            ),
        },
    }
    with db.transaction():
        db.execute(
            "INSERT INTO code_postprocess_runs(tenant_id, level, stages_json, ran_at, graph_revision) "
            "VALUES (?,?,?,?,?) ON CONFLICT(tenant_id) DO UPDATE SET "
            "level=excluded.level, stages_json=excluded.stages_json, "
            "ran_at=excluded.ran_at, graph_revision=excluded.graph_revision;",
            (tenant_id, wanted_level, json.dumps(reports), payload["ran_at"], revision),
        )
    return payload


def _applied_level(applied: list[str]) -> str:
    """The richest level whose stages all ran."""
    best = "none"
    for name in LEVELS:
        required = set(LEVEL_STAGES[name])
        if required <= set(applied):
            best = name
    return best


def _run_stage(db, stage, view, tenant_id, revision, resolution) -> dict:
    if stage == STAGE_COMMUNITIES:
        return _stage_communities(db, view, tenant_id, revision, resolution)
    if stage == STAGE_FLOWS:
        return _stage_flows(db, view, tenant_id, revision)
    if stage == STAGE_RISK:
        return _stage_risk(db, view, tenant_id, revision)
    return _stage_index(db, tenant_id)


# -- communities --------------------------------------------------------------


def _stage_communities(db, view: CodeGraphView, tenant_id, revision, resolution) -> dict:
    communities = view.communities(STRUCTURAL_PREDICATES, resolution=resolution)
    neighbours = view.undirected_adjacency(STRUCTURAL_PREDICATES)
    members: dict[int, list[str]] = {}
    for node_id, index in communities.items():
        members.setdefault(index, []).append(node_id)

    rows = []
    for index in sorted(members):
        group = sorted(members[index])
        group_set = set(group)
        internal = int(sum(len(neighbours.get(n, set()) & group_set) for n in group) / 2)
        external = sum(len(neighbours.get(n, set()) - group_set) for n in group)
        size = len(group)
        density = (
            round(internal / (size * (size - 1) / 2), _PLACES) if size >= 2 else 1.0
        )
        names = sorted(view.label(n) for n in group)
        files = sorted({view.path_of(n) for n in group if view.path_of(n)})
        entries = sorted(
            view.label(n)
            for n in group
            if view.nodes[n].kind in ENTRY_POINT_KINDS
            or view.nodes[n].display in ENTRY_POINT_NAMES
        )
        rows.append(
            (
                tenant_id,
                index,
                size,
                len(files),
                internal,
                external,
                density,
                json.dumps(names[:MAX_COMMUNITY_MEMBERS_STORED]),
                json.dumps(files),
                json.dumps(entries),
                _now(),
                revision,
            )
        )

    with db.transaction():
        db.execute("DELETE FROM code_community_summaries WHERE tenant_id=?;", (tenant_id,))
        for row in rows:
            db.execute(
                "INSERT INTO code_community_summaries(tenant_id, community_index, member_count, "
                "file_count, internal_edges, external_edges, density, members_json, files_json, "
                "entry_points_json, computed_at, graph_revision) VALUES (?,?,?,?,?,?,?,?,?,?,?,?);",
                row,
            )
    return {
        "stage": STAGE_COMMUNITIES,
        "ran": True,
        "communities": len(rows),
        "resolution": resolution,
        "note": (
            "community indices are arbitrary labels from THIS run; never compare "
            "them across runs, compare co-membership"
        ),
    }


# -- flows --------------------------------------------------------------------


def _entry_points(view: CodeGraphView) -> list[str]:
    """Nodes execution can start at, in a deterministic order."""
    framework_out = view.out_adjacency(("code:routes_to", "code:dispatches"))
    return sorted(
        nid
        for nid in view.node_ids()
        if view.nodes[nid].kind in ENTRY_POINT_KINDS
        or view.nodes[nid].display in ENTRY_POINT_NAMES
        or framework_out.get(nid)
    )


def _trace(view: CodeGraphView, entry: str) -> list[dict]:
    """Ordered steps of the flow from one entry point.

    Breadth-first over call and dispatch edges, written iteratively so a deep
    graph cannot exhaust the stack, and bounded by depth and step count. A node
    already on the flow is not re-expanded, so recursion terminates.
    """
    out = view.out_adjacency((CALL_PREDICATE, "code:routes_to", "code:dispatches"))
    steps: list[dict] = []
    seen = {entry}
    frontier = [(entry, 0)]
    while frontier and len(steps) < MAX_FLOW_STEPS:
        node, depth = frontier.pop(0)
        node_obj = view.nodes[node]
        steps.append(
            {
                "order": len(steps),
                "depth": depth,
                "canonical": node_obj.canonical,
                "kind": node_obj.kind,
                "path": node_obj.path,
            }
        )
        if depth >= MAX_FLOW_DEPTH:
            continue
        for child in out.get(node, ()):
            if child in seen:
                continue
            seen.add(child)
            frontier.append((child, depth + 1))
    return steps


def _stage_flows(db, view: CodeGraphView, tenant_id, revision) -> dict:
    entries = _entry_points(view)[:MAX_FLOWS]
    traced: list[tuple[str, list[dict]]] = []
    for entry in entries:
        steps = _trace(view, entry)
        if len(steps) < 2:
            # A flow of one step is the entry point standing alone. It reaches
            # nothing, so cataloguing it would fill the catalogue with
            # non-flows.
            continue
        traced.append((entry, steps))

    # Ranking, documented: a flow that goes deeper and touches more files is
    # more of the system. Both terms are normalised by the maximum observed in
    # THIS catalogue, so the score is a position here rather than an absolute.
    max_steps = max((len(s) for _e, s in traced), default=1)
    max_files = max((len({st["path"] for st in s}) for _e, s in traced), default=1)

    rows: list[tuple] = []
    file_rows: list[tuple[str, str, str]] = []
    for entry, steps in traced:
        node = view.nodes[entry]
        files = sorted({st["path"] for st in steps if st["path"]})
        depth = max(st["depth"] for st in steps)
        rank = round(
            0.5 * (len(steps) / max_steps) + 0.5 * (len(files) / max(1, max_files)), _PLACES
        )
        flow_id = hashlib.sha256(f"{tenant_id}:{node.canonical}".encode()).hexdigest()[:24]
        rows.append(
            (
                flow_id,
                tenant_id,
                node.canonical,
                node.canonical,
                node.kind,
                depth,
                len(steps),
                len(files),
                rank,
                json.dumps(steps),
                _now(),
                revision,
            )
        )
        file_rows.extend((flow_id, tenant_id, path) for path in files)

    with db.transaction():
        db.execute("DELETE FROM code_flow_files WHERE tenant_id=?;", (tenant_id,))
        db.execute("DELETE FROM code_flows WHERE tenant_id=?;", (tenant_id,))
        for row in rows:
            db.execute(
                "INSERT INTO code_flows(flow_id, tenant_id, name, entry_canonical, entry_kind, "
                "depth, step_count, file_count, rank_score, steps_json, computed_at, graph_revision) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?);",
                row,
            )
        for row in file_rows:
            db.execute(
                "INSERT OR IGNORE INTO code_flow_files(flow_id, tenant_id, path) VALUES (?,?,?);",
                row,
            )
    return {
        "stage": STAGE_FLOWS,
        "ran": True,
        "flows": len(rows),
        "entry_points_considered": len(entries),
        "note": (
            "flows are traced structurally over call and dispatch edges, so they "
            "over-approximate exactly as those edges do"
        ),
    }


# -- risk index ---------------------------------------------------------------


def _stage_risk(db, view: CodeGraphView, tenant_id, revision) -> dict:
    from .risk import _level_cuts, _level_for, _RiskModel

    model = _RiskModel(view)
    symbols = view.symbol_ids()
    scored = [(nid, *model.score(nid)) for nid in symbols]
    cuts = _level_cuts([s for _n, s, _c in scored])

    with db.transaction():
        db.execute("DELETE FROM code_symbol_risk WHERE tenant_id=?;", (tenant_id,))
        for node_id, score, contributions in scored:
            node = view.nodes[node_id]
            db.execute(
                "INSERT INTO code_symbol_risk(tenant_id, canonical, path, score, level, "
                "factors_json, computed_at, graph_revision) VALUES (?,?,?,?,?,?,?,?);",
                (
                    tenant_id,
                    node.canonical,
                    node.path,
                    score,
                    _level_for(score, cuts),
                    json.dumps({"contributions": contributions, "raw": model.raw(node_id)}),
                    _now(),
                    revision,
                ),
            )
    return {
        "stage": STAGE_RISK,
        "ran": True,
        "symbols": len(scored),
        "level_cuts": cuts,
        "note": "structural factors only; the opt-in churn signal is never precomputed",
    }


# -- search index -------------------------------------------------------------


def _stage_index(db, tenant_id) -> dict:
    """Rebuild the vector index, when a real embedding model is staged.

    Capability-detected. The hashing adapter is the zero-dependency fallback,
    not a model, so building an index from it would put stub vectors in the
    store under a real-looking tag. With no real model this stage is reported
    NOT RUN with the reason, which is different from having failed.
    """
    from ..adapters.embedding import default_embedding_adapter
    from ..search.vector_index import reindex

    adapter = default_embedding_adapter()
    ok, why = adapter.available()
    if adapter.name == "hashing" or not ok:
        return {
            "stage": STAGE_INDEX,
            "ran": False,
            "reason": (
                f"no real embedding model staged (adapter {adapter.name!r}: {why}). "
                "Install the 'embeddings' extra and pre-stage a model."
            ),
        }
    try:
        built = reindex(db, adapter=adapter, tenant_id=tenant_id)
    except Exception as e:
        return {"stage": STAGE_INDEX, "ran": False, "reason": f"index build failed: {e}"}
    return {
        "stage": STAGE_INDEX,
        "ran": True,
        "vectors": built.get("vectors", 0),
        "model": built.get("model", ""),
    }


# -- readers ------------------------------------------------------------------


def last_run(db: Database, *, tenant_id: str = "local") -> dict | None:
    row = db.fetchone(
        "SELECT level, stages_json, ran_at, graph_revision FROM code_postprocess_runs WHERE tenant_id=?;",
        (tenant_id,),
    )
    if row is None:
        return None
    return {
        "level": row["level"],
        "stages": json.loads(row["stages_json"]),
        "ran_at": row["ran_at"],
        "graph_revision": row["graph_revision"],
        "current": row["graph_revision"] == graph_revision(db, tenant_id),
    }

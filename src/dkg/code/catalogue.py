"""Reading the persisted flow catalogue and the precomputed summaries.

The post-processing stage writes these; this module is the read side. Three
questions the catalogue answers that live tracing could not:

    which flows does this system have          list, in ranked order
    what does this one do                      retrieve by name or identifier
    which flows does my change touch           given a changed file set

The last is the one that needs persistence. Answering it live would mean tracing
every entry point on every call, which is the whole graph, every time. Against
the catalogue it is an index lookup on the files each flow passes through.

Every reader here reports its SOURCE and whether the row is current. A derived
answer computed against an older graph is served with that fact attached rather
than presented as though it were fresh; a caller that wants freshness re-runs the
stage. When nothing is precomputed at all, the reader says so and names the
command that would build it rather than silently returning empty.

Read-only.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from ..core.db import Database
from .postprocess import graph_revision

_MAX_LIMIT = 1000


def _count(db: Database, sql: str, params: tuple) -> int:
    """A COUNT(*) that reports zero rather than indexing a row that is not there."""
    row = db.fetchone(sql, params)
    return int(row["n"]) if row is not None else 0


def _currency(db: Database, tenant_id: str, revision: str) -> dict:
    """Whether a stored row was computed against the graph as it stands now."""
    current = graph_revision(db, tenant_id)
    return {
        "graph_revision": revision,
        "current_graph_revision": current,
        "current": revision == current,
        "note": (
            "computed against the graph as it now stands"
            if revision == current
            else "STALE: computed against an earlier graph; re-run the post-processing "
            "stage (dkg code-postprocess) for a current answer"
        ),
    }


def _not_built(what: str) -> dict:
    return {
        "source": "not precomputed",
        "reason": (
            f"no {what} have been precomputed for this graph. Run "
            "`dkg code-postprocess --level standard`, or ingest with a level that "
            "includes the stage."
        ),
    }


def list_flows(db: Database, *, tenant_id: str = "local", limit: int = 50) -> dict:
    """Catalogued flows in ranked order, highest first."""
    limit = max(1, min(int(limit), _MAX_LIMIT))
    rows = db.fetchall(
        "SELECT flow_id, name, entry_canonical, entry_kind, depth, step_count, file_count, "
        "rank_score, computed_at, graph_revision FROM code_flows WHERE tenant_id=? "
        "ORDER BY rank_score DESC, name ASC LIMIT ?;",
        (tenant_id, limit + 1),
    )
    if not rows:
        return {"flows": [], "total": 0, **_not_built("flows")}
    truncated = len(rows) > limit
    rows = rows[:limit]
    total = _count(db, "SELECT COUNT(*) AS n FROM code_flows WHERE tenant_id=?;", (tenant_id,))
    return {
        "flows": [
            {
                "flow_id": r["flow_id"],
                "name": r["name"],
                "entry": r["entry_canonical"],
                "entry_kind": r["entry_kind"],
                "depth": r["depth"],
                "steps": r["step_count"],
                "files": r["file_count"],
                "rank_score": round(float(r["rank_score"]), 4),
            }
            for r in rows
        ],
        "total": int(total),
        "returned": len(rows),
        "truncated": truncated,
        "source": "precomputed",
        "computed_at": rows[0]["computed_at"],
        **_currency(db, tenant_id, rows[0]["graph_revision"]),
        "ranking": (
            "rank_score is half the flow's step count and half its file count, "
            "each normalised by the maximum observed in THIS catalogue. It is a "
            "position within this repository, not an absolute measure of "
            "importance, and it is structural like every edge it rests on."
        ),
    }


def get_flow(db: Database, ident: str, *, tenant_id: str = "local") -> dict:
    """One flow with its ordered steps, addressed by identifier or name."""
    row = db.fetchone(
        "SELECT flow_id, name, entry_canonical, entry_kind, depth, step_count, file_count, "
        "rank_score, steps_json, computed_at, graph_revision FROM code_flows "
        "WHERE tenant_id=? AND (flow_id=? OR name=?) LIMIT 1;",
        (tenant_id, ident, ident),
    )
    if row is None:
        built = _count(db, "SELECT COUNT(*) AS n FROM code_flows WHERE tenant_id=?;", (tenant_id,))
        if not built:
            return {"flow": None, **_not_built("flows")}
        return {
            "flow": None,
            "source": "precomputed",
            "reason": f"no catalogued flow is named or identified by {ident!r}",
        }
    files = [
        r["path"]
        for r in db.fetchall(
            "SELECT path FROM code_flow_files WHERE flow_id=? ORDER BY path;", (row["flow_id"],)
        )
    ]
    return {
        "flow": {
            "flow_id": row["flow_id"],
            "name": row["name"],
            "entry": row["entry_canonical"],
            "entry_kind": row["entry_kind"],
            "depth": row["depth"],
            "steps": json.loads(row["steps_json"]),
            "step_count": row["step_count"],
            "files": files,
            "file_count": row["file_count"],
            "rank_score": round(float(row["rank_score"]), 4),
        },
        "source": "precomputed",
        "computed_at": row["computed_at"],
        **_currency(db, tenant_id, row["graph_revision"]),
        "why": (
            "the steps are a bounded breadth-first traversal of call and dispatch "
            "edges from the entry point. Structural and over-approximate: a call "
            "made through a variable is not in it, and a name-matched edge may put "
            "a step in it that never runs."
        ),
    }


def flows_affected_by(
    db: Database, files: Iterable[str], *, tenant_id: str = "local", limit: int = 50
) -> dict:
    """Which catalogued flows pass through any of the named files."""
    wanted = sorted({str(f).strip() for f in files if str(f).strip()})
    limit = max(1, min(int(limit), _MAX_LIMIT))
    if not wanted:
        return {"flows": [], "changed_files": [], "total": 0, "source": "precomputed"}
    built = _count(db, "SELECT COUNT(*) AS n FROM code_flows WHERE tenant_id=?;", (tenant_id,))
    if not built:
        return {"flows": [], "changed_files": wanted, "total": 0, **_not_built("flows")}

    placeholders = ",".join("?" * len(wanted))
    rows = db.fetchall(
        "SELECT f.flow_id, f.name, f.entry_canonical, f.rank_score, f.step_count, "
        "COUNT(DISTINCT ff.path) AS touched, f.graph_revision "
        "FROM code_flows f JOIN code_flow_files ff ON ff.flow_id = f.flow_id "
        f"WHERE f.tenant_id=? AND ff.path IN ({placeholders}) "
        "GROUP BY f.flow_id ORDER BY touched DESC, f.rank_score DESC, f.name ASC LIMIT ?;",
        (tenant_id, *wanted, limit),
    )
    unmatched = sorted(
        set(wanted)
        - {
            r["path"]
            for r in db.fetchall(
                "SELECT DISTINCT path FROM code_flow_files WHERE tenant_id=? "
                f"AND path IN ({placeholders});",
                (tenant_id, *wanted),
            )
        }
    )
    revision = rows[0]["graph_revision"] if rows else graph_revision(db, tenant_id)
    return {
        "flows": [
            {
                "flow_id": r["flow_id"],
                "name": r["name"],
                "entry": r["entry_canonical"],
                "files_touched": int(r["touched"]),
                "steps": r["step_count"],
                "rank_score": round(float(r["rank_score"]), 4),
            }
            for r in rows
        ],
        "changed_files": wanted,
        "files_in_no_flow": unmatched,
        "total": len(rows),
        "source": "precomputed",
        **_currency(db, tenant_id, revision),
        "why": (
            "a flow is reported affected when it passes through a changed file. "
            "That is a structural over-approximation in both directions: a flow "
            "may touch the file without touching the changed lines, and a flow "
            "reached only through a dynamic call is not catalogued at all."
        ),
    }


def community_summary(
    db: Database, index: int | None = None, *, tenant_id: str = "local", limit: int = 50
) -> dict:
    """Precomputed community summaries, all of them or one by index."""
    limit = max(1, min(int(limit), _MAX_LIMIT))
    if index is not None:
        rows = db.fetchall(
            "SELECT * FROM code_community_summaries WHERE tenant_id=? AND community_index=?;",
            (tenant_id, int(index)),
        )
    else:
        rows = db.fetchall(
            "SELECT * FROM code_community_summaries WHERE tenant_id=? "
            "ORDER BY member_count DESC, community_index ASC LIMIT ?;",
            (tenant_id, limit),
        )
    if not rows:
        return {"communities": [], **_not_built("community summaries")}
    return {
        "communities": [
            {
                "community_index": r["community_index"],
                "members": r["member_count"],
                "files": r["file_count"],
                "internal_edges": r["internal_edges"],
                "external_edges": r["external_edges"],
                "density": round(float(r["density"]), 4),
                "member_names": json.loads(r["members_json"]),
                "file_paths": json.loads(r["files_json"]),
                "entry_points": json.loads(r["entry_points_json"]),
            }
            for r in rows
        ],
        "source": "precomputed",
        "computed_at": rows[0]["computed_at"],
        **_currency(db, tenant_id, rows[0]["graph_revision"]),
        "why": (
            "community indices are arbitrary labels produced independently per "
            "run. Never compare an index across runs; compare co-membership sets."
        ),
    }


def symbol_risk(
    db: Database, canonical: str | None = None, *, tenant_id: str = "local", limit: int = 50
) -> dict:
    """The precomputed risk index, for one symbol or the highest-scoring ones."""
    limit = max(1, min(int(limit), _MAX_LIMIT))
    if canonical:
        rows = db.fetchall(
            "SELECT * FROM code_symbol_risk WHERE tenant_id=? AND canonical=?;",
            (tenant_id, canonical),
        )
    else:
        rows = db.fetchall(
            "SELECT * FROM code_symbol_risk WHERE tenant_id=? "
            "ORDER BY score DESC, canonical ASC LIMIT ?;",
            (tenant_id, limit),
        )
    if not rows:
        return {"symbols": [], **_not_built("symbol risk scores")}
    return {
        "symbols": [
            {
                "canonical": r["canonical"],
                "path": r["path"],
                "score": round(float(r["score"]), 4),
                "level": r["level"],
                **json.loads(r["factors_json"]),
            }
            for r in rows
        ],
        "source": "precomputed",
        "computed_at": rows[0]["computed_at"],
        **_currency(db, tenant_id, rows[0]["graph_revision"]),
        "why": (
            "structural factors only. The opt-in git churn signal is never "
            "precomputed, because it is not derived from the graph and would "
            "make a stored row depend on history the graph does not hold."
        ),
    }

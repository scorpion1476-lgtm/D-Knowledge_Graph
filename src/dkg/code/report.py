"""Structural code-analysis report for the consumer action and CLI.

Summarises the code graph (files, per-language symbol counts, per-predicate edge
counts) and, when a git base ref is given, the aggregate structural blast-radius
of the changed files. The impact number is advisory and over-approximate: it is
a structural signal, not an oracle. No network.

Three things live here beyond the summary, all of them read-only and all of them
advisory:

**The review block.** Everything a pull-request comment needs, assembled from
the existing analyses rather than from a second model of the same facts: the
overall risk level and score from ``risk.change_risk``, the changed symbols with
their file and line locations and their test-coverage status, the affected
execution flows ranked by ``criticality.flow_criticality``, the test gaps from
``gaps.knowledge_gaps``, and the estimated token saving from the shared
``context.savings`` record. Nothing here re-implements a risk model.

**The gates.** A merge gate on a NAMED risk level, with the thresholds for every
level published in the output next to the verdict, off by default, and with the
score reported whether the gate is on or off. The older integer impact gate is
kept working and is labelled deprecated rather than being silently redefined.

**The cached-database check.** Continuous integration can restore a previously
built graph. A restored file that is truncated, corrupt, or written by a newer
schema must not be analysed as though it were sound, so it is validated first
and removed on failure, which makes the next step a full rebuild rather than a
wrong answer.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..core.db import Database
from .changes import _git, is_git_repo
from .impact import blast_radius_for_file
from .ingest import CODE_EXTS
from .languages import LanguageRegistry

# How many changed symbols are used as execution-flow entry points, and how many
# flows are reported. Both are bounds, not preferences: a change touching two
# hundred symbols must not turn one comment into two hundred graph traversals.
FLOW_ENTRY_LIMIT = 5
DEFAULT_TOP = 10

# Bound on the line-span lookup, which is keyed on the scored symbols.
MAX_SPAN_LOOKUP = 500

# Tables a restored database must contain before it is worth analysing. The
# migration table is included on purpose: a file with entities but no migration
# ledger cannot be brought up to date safely.
CACHE_REQUIRED_TABLES = ("entities", "meta", "relationships", "schema_migrations")

# The gate is named, not numeric, and "off" is the default.
RISK_GATE_OFF = "off"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _metadata(raw: object) -> dict:
    if not raw:
        return {}
    try:
        obj = json.loads(str(raw))
    except (ValueError, TypeError):
        return {}
    return obj if isinstance(obj, dict) else {}


def _as_int(raw: object) -> int:
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _changed_files(repo: Path, base: str, exts: set[str]) -> list[str]:
    if not is_git_repo(repo):
        return []
    out = _git(repo, "diff", "--name-only", base, "--")
    return [
        line.strip()
        for line in out.splitlines()
        if line.strip() and Path(line.strip()).suffix.lower() in exts
    ]


def build_report(
    db: Database,
    repo: str | Path,
    *,
    base: str | None = None,
    tenant_id: str = "local",
    languages: LanguageRegistry | None = None,
    depth: int = 3,
    max_nodes: int = 500,
    review: bool = False,
    top: int = DEFAULT_TOP,
) -> dict:
    repo = Path(repo)
    exts = CODE_EXTS | (languages.extensions() if languages else set())

    kind_rows = db.fetchall(
        "SELECT kind, COUNT(*) AS n FROM entities WHERE tenant_id=? AND kind LIKE 'code:%' GROUP BY kind ORDER BY kind;",
        (tenant_id,),
    )
    by_kind = {r["kind"].split(":", 1)[1]: r["n"] for r in kind_rows}
    pred_rows = db.fetchall(
        "SELECT predicate, COUNT(*) AS n FROM relationships WHERE tenant_id=? AND predicate LIKE 'code:%' GROUP BY predicate ORDER BY predicate;",
        (tenant_id,),
    )
    by_pred = {r["predicate"].split(":", 1)[1]: r["n"] for r in pred_rows}
    summary = {
        "files": by_kind.get("module", 0),
        "symbols_by_kind": by_kind,
        "edges_by_predicate": by_pred,
        "total_symbols": sum(by_kind.values()),
        "total_edges": sum(by_pred.values()),
    }

    impact: dict | None = None
    changed: list[str] = []
    if base:
        changed = _changed_files(repo, base, exts)
        impacted: dict[str, dict] = {}
        for rel in changed:
            r = blast_radius_for_file(db, rel, tenant_id=tenant_id, depth=depth, max_nodes=max_nodes)
            for imp in r["impacted"]:
                impacted[imp["canonical"]] = imp
        impact = {
            "base_ref": base,
            "changed_files": changed,
            "impacted_count": len(impacted),
            "impacted": sorted(impacted.values(), key=lambda x: x["canonical"]),
            "advisory": "structural and over-approximate; a signal to prioritise review, not an oracle",
        }

    report = {
        "repo": str(repo.resolve()),
        "generated_at": _now(),
        "summary": summary,
        "impact": impact,
    }
    if review:
        report["review"] = build_review(
            db,
            repo,
            changed_files=changed,
            base=base,
            tenant_id=tenant_id,
            top=top,
        )
    return report


# -- the review block (what the pull-request comment is rendered from) --------


def build_review(
    db: Database,
    repo: str | Path,
    *,
    changed_files: list[str],
    base: str | None = None,
    tenant_id: str = "local",
    top: int = DEFAULT_TOP,
) -> dict:
    """Assemble everything the pull-request comment reports, from one change set.

    Six contents, each from the analysis that already owns it: the overall risk
    level, the changed symbols ranked by risk with location and coverage, the
    affected execution flows ranked by criticality, the test gaps, the estimated
    token saving, and the standing advisory caveat. Nothing here computes a
    second risk model, a second criticality model, or a second gap model.

    Read-only. Every list has an explicit sort key with the canonical name
    breaking ties, so the same database and change set render byte-identically.
    """
    from .criticality import flow_criticality
    from .gaps import knowledge_gaps
    from .risk import change_risk

    repo = Path(repo)
    top = max(1, min(int(top), 200))
    changed = sorted({str(f).strip() for f in changed_files if str(f).strip()})

    risk = change_risk(db, files=changed, tenant_id=tenant_id, limit=max(top, FLOW_ENTRY_LIMIT))
    scored = risk["symbols"]
    spans = _line_spans(db, [s["canonical"] for s in scored], tenant_id)

    changed_symbols = []
    for entry in scored:
        start, end = spans.get(entry["canonical"], (0, 0))
        tested = bool(entry["raw"]["has_test_edge"])
        changed_symbols.append(
            {
                "canonical": entry["canonical"],
                "kind": entry["kind"],
                "path": entry["path"],
                "start_line": start,
                "end_line": end,
                # A span of zero means the parser recorded none. Reported as
                # unknown rather than as line one, which would be a guess.
                "location": _location(entry["path"], start, end),
                "score": entry["structural_score"],
                "level": entry["level"],
                "tested": tested,
                "test_status": "test edge present" if tested else "no test edge",
                "callers": entry["raw"]["callers"],
                "entry_points_reaching": entry["raw"]["entry_points_reaching"],
            }
        )
    changed_symbols.sort(key=lambda s: (-float(s["score"]), s["canonical"]))

    flows = _affected_flows(db, changed_symbols, tenant_id, top, flow_criticality)
    gaps = _test_gaps(db, changed, top, knowledge_gaps)

    review = {
        "scope": {
            "base_ref": base,
            "changed_files": changed,
            "changed_symbol_count": risk["symbol_count"],
            "note": (
                "no base ref was supplied, so no file was in the change set and "
                "nothing was scored; the level below is the level of an empty "
                "change set, not a judgement that the change is safe"
                if not changed
                else "the change set is the source files git reports changed against the base ref"
            ),
        },
        "risk": {
            "level": risk["change_level"],
            "score": risk["change_score"],
            "levels": risk["levels"],
            "weights": risk["weights"],
        },
        "changed_symbols": changed_symbols[:top],
        "changed_symbols_truncated": len(changed_symbols) > top,
        "flows": flows,
        "test_gaps": gaps,
        "token_saving": None,
        "why": {
            "advisory": (
                "ADVISORY. Every row is read off a structural, over-approximate "
                "code graph: call edges are name-based, dynamic dispatch is not "
                "modelled, and a test the parser could not connect leaves no "
                "edge behind. A high score is a reason to read the change, not a "
                "defect, and an absent test edge is not proof of an absent test."
            ),
            "flow_entry_limit": FLOW_ENTRY_LIMIT,
            "top": top,
            "ordering": (
                "changed symbols by descending risk score then canonical name; "
                "flows by descending criticality then the path itself; both "
                "orders are total, so the render is deterministic"
            ),
        },
    }
    review["token_saving"] = _token_saving(review, repo)
    return review


def _location(path: str, start: int, end: int) -> str:
    """A file and line location, or the file alone when no span was recorded."""
    if not path:
        return "(no path recorded)"
    if start <= 0 or end < start:
        return f"{path} (no line span recorded)"
    if start == end:
        return f"{path}:{start}"
    return f"{path}:{start}-{end}"


def _line_spans(db: Database, canonicals: list[str], tenant_id: str) -> dict[str, tuple[int, int]]:
    """Definition line spans for the named symbols, in one bounded query."""
    wanted = sorted({c for c in canonicals if c})[:MAX_SPAN_LOOKUP]
    if not wanted:
        return {}
    placeholders = ",".join("?" * len(wanted))
    rows = db.fetchall(
        "SELECT canonical, metadata_json FROM entities "
        f"WHERE tenant_id=? AND kind LIKE 'code:%' AND canonical IN ({placeholders}) "
        "ORDER BY canonical;",
        (tenant_id, *wanted),
    )
    spans: dict[str, tuple[int, int]] = {}
    for row in rows:
        meta = _metadata(row["metadata_json"])
        spans[row["canonical"]] = (_as_int(meta.get("start_line")), _as_int(meta.get("end_line")))
    return spans


def _affected_flows(
    db: Database, changed_symbols: list[dict], tenant_id: str, top: int, flow_criticality
) -> list[dict]:
    """Execution flows that pass THROUGH a changed symbol, ranked by criticality.

    A flow is enumerated forward from an entry, so tracing forward from the
    changed symbol itself would miss everything upstream of it: change a leaf
    helper and the flows that matter are the ones that reach it, not the ones it
    starts. So the entries are found by walking call edges BACKWARDS from each
    changed symbol to the callers that nothing calls, and the enumerated flows
    are then filtered to those that actually contain a changed symbol.

    Bounded at every step: the upstream walk has a depth and node cap, the entry
    set is capped, and the reported flows are capped. Identical paths reached
    from two entries are reported once.
    """
    changed = {s["canonical"] for s in changed_symbols}
    if not changed:
        return []
    entries = _flow_entry_points(db, changed, tenant_id)

    seen: dict[tuple[str, ...], dict] = {}
    for entry in entries:
        result = flow_criticality(db, entry, tenant_id=tenant_id)
        if not result.get("found"):
            continue
        for flow in result["flows"]:
            path = tuple(flow["path"])
            if len(path) < 2:
                continue  # a single node is not a flow
            if not changed.intersection(path):
                continue  # this flow is not affected by the change
            existing = seen.get(path)
            if existing is None or float(flow["criticality"]) > float(existing["criticality"]):
                seen[path] = {
                    "entry": result["entry"],
                    "path": list(path),
                    "criticality": flow["criticality"],
                    "depth": flow["depth"],
                    "peak_fan_in": flow["peak_fan_in"],
                    "files_touched": flow["files_touched"],
                    "tested": flow["tested"],
                }
    flows = sorted(seen.values(), key=lambda f: (-float(f["criticality"]), f["path"]))
    return flows[:top]


# Bounds on the upstream walk that finds flow entry points. A change in a symbol
# a hundred callers deep must not turn one comment into an unbounded traversal.
FLOW_UPSTREAM_DEPTH = 4
FLOW_UPSTREAM_NODES = 200


def _flow_entry_points(db: Database, changed: set[str], tenant_id: str) -> list[str]:
    """Canonical names to enumerate flows from, walking callers backwards.

    A symbol nothing calls is an entry. A changed symbol that is itself
    uncalled is its own entry, so a top-level change still produces flows.
    Deterministic: the frontier is sorted at every level and the result is
    sorted before it is capped.
    """
    frontier = sorted(changed)
    visited: set[str] = set(frontier)
    entries: set[str] = set()
    for _depth in range(FLOW_UPSTREAM_DEPTH):
        if not frontier or len(visited) >= FLOW_UPSTREAM_NODES:
            break
        placeholders = ",".join("?" * len(frontier))
        rows = db.fetchall(
            "SELECT DISTINCT caller.canonical AS caller_canonical, callee.canonical AS callee_canonical "
            "FROM relationships r "
            "JOIN entities caller ON caller.entity_id = r.subject_id "
            "JOIN entities callee ON callee.entity_id = r.object_id "
            "WHERE r.tenant_id=? AND r.predicate='code:calls' "
            f"AND callee.canonical IN ({placeholders}) "
            "ORDER BY caller.canonical LIMIT ?;",
            (tenant_id, *frontier, FLOW_UPSTREAM_NODES),
        )
        callers_of = {r["callee_canonical"] for r in rows}
        # A node in the frontier that nothing calls is where a flow starts.
        entries.update(name for name in frontier if name not in callers_of)
        nxt = sorted({r["caller_canonical"] for r in rows} - visited)
        visited.update(nxt)
        frontier = nxt[: max(0, FLOW_UPSTREAM_NODES - len(entries))]
    # Whatever the walk ran out of budget on is still a usable entry.
    entries.update(frontier)
    return sorted(entries)[:FLOW_ENTRY_LIMIT]


def _test_gaps(db: Database, changed: list[str], top: int, knowledge_gaps) -> dict:
    """Test gaps, narrowed to the change set when there is one.

    With a change set the comment should be about the change, so the rows are
    filtered to files git reported changed. Without one there is nothing to
    narrow to, and the repository-wide rows are reported instead, labelled as
    such so the two cases are never confused.
    """
    result = knowledge_gaps(db, limit=max(top, 1))
    scoped = bool(changed)
    changed_set = set(changed)

    def keep(row: dict) -> bool:
        return (not scoped) or row.get("path", "") in changed_set

    hotspots = [r for r in result["untested_hotspots"] if keep(r)]
    isolated = [r for r in result["isolated"] if keep(r)]
    hotspots.sort(key=lambda r: (-int(r.get("inbound_calls", 0)), r["canonical"]))
    isolated.sort(key=lambda r: r["canonical"])
    return {
        "scoped_to_change_set": scoped,
        "untested_hotspots": hotspots[:top],
        "isolated_symbols": isolated[:top],
        "repository_summary": result["summary"],
        "thresholds": result["thresholds"],
        "why": (
            "an untested hotspot is a symbol carrying derived-high inbound call "
            "pressure with no code:tested_by edge reaching it. That is the "
            "absence of an edge in a structural graph, not proof that the symbol "
            "is untested at runtime"
            + (
                "; these rows are filtered to the files in this change set"
                if scoped
                else "; no change set was given, so these rows are repository-wide"
            )
        ),
    }


def _token_saving(review: dict, repo: Path) -> dict:
    """The estimated token saving for this review, from the shared record.

    The baseline is the cost of reading the source files this review NAMES, not
    the whole repository. A whole-repository baseline would produce a far larger
    number and would misdescribe what the alternative actually is.
    """
    from ..context.savings import savings_record

    payload = {k: v for k, v in review.items() if k != "token_saving"}
    return savings_record(payload, repo_root=repo)


# -- the merge gates ---------------------------------------------------------


def evaluate_gates(
    report: dict, *, risk_gate: str = RISK_GATE_OFF, fail_on_impact: int | None = None
) -> dict:
    """Decide both gates and publish the thresholds behind each verdict.

    The risk gate is named, not numeric. Its cuts come from the distribution of
    scores THIS graph produces, so they move with the repository instead of
    being a constant that means different things in a library and a monolith,
    and they are printed next to the verdict so the arithmetic is checkable.

    The score is reported whether or not the gate is enabled. Turning a gate off
    suppresses the FAILURE, never the measurement.
    """
    from .risk import LEVEL_NAMES

    review = report.get("review") or {}
    risk = review.get("risk") or {}
    observed_level = risk.get("level")
    observed_score = risk.get("score")
    cuts = (risk.get("levels") or {}).get("cuts")
    derivation = (risk.get("levels") or {}).get("derivation")

    requested = str(risk_gate or RISK_GATE_OFF).strip().lower()
    risk_enabled = requested != RISK_GATE_OFF
    risk_failed = False
    risk_reason = "the risk gate is off by default; the score above is reported anyway"
    if risk_enabled and requested not in LEVEL_NAMES:
        risk_reason = (
            f"unknown risk level {requested!r}; the published levels are "
            + ", ".join(LEVEL_NAMES)
        )
    elif risk_enabled and observed_level is None:
        risk_reason = (
            "the risk gate was requested but no risk analysis was produced, so "
            "there is nothing to gate on and the gate does not fail"
        )
    elif risk_enabled:
        order = list(LEVEL_NAMES)
        risk_failed = order.index(str(observed_level)) >= order.index(requested)
        risk_reason = (
            f"observed level {observed_level} against the gate at {requested}: "
            + ("at or above the gate" if risk_failed else "below the gate")
        )

    impact = report.get("impact") or {}
    observed_impact = impact.get("impacted_count")
    impact_enabled = fail_on_impact is not None
    impact_failed = bool(
        impact_enabled
        and observed_impact is not None
        and fail_on_impact is not None
        and observed_impact > int(fail_on_impact)
    )

    return {
        "risk": {
            "requested": requested,
            "enabled": risk_enabled,
            "observed_level": observed_level,
            "observed_score": observed_score,
            "cuts": cuts,
            "derivation": derivation,
            "failed": risk_failed,
            "why": risk_reason,
        },
        "impact": {
            "requested": None if fail_on_impact is None else int(fail_on_impact),
            "enabled": impact_enabled,
            "observed_count": observed_impact,
            "failed": impact_failed,
            "deprecated": True,
            "why": (
                "DEPRECATED but unchanged. This gate compares an over-approximate "
                "impacted-entity count against an integer, which is not comparable "
                "across repositories and cannot be calibrated: the same integer is "
                "alarming in a small library and unremarkable in a monolith. It "
                "still behaves exactly as it always did, because silently "
                "redefining a flag callers already depend on would be worse than "
                "keeping a weak one. Prefer the named risk gate."
            ),
        },
        "failed": bool(risk_failed or impact_failed),
    }


# -- the cached database (continuous-integration restore) ---------------------


def prepare_cached_database(db_path: str | Path) -> dict:
    """Validate a restored graph database, removing it when it is unusable.

    A cache restore can hand back a file that is truncated, was written by a
    newer schema, or is not a database at all. Analysing it would either raise
    somewhere far from here or, worse, produce a graph that is quietly wrong. So
    it is checked first and DELETED on any failure, which makes the next step a
    full rebuild. The caller is told which happened; nothing is silent.

    Read-mostly: the only write is the removal of a file already judged unusable.
    """
    from ..core.version import CURRENT_SCHEMA_MAJOR

    path = Path(db_path)
    if not path.exists():
        return {
            "status": "miss",
            "usable": False,
            "removed": [],
            "schema_major": None,
            "reason": "no cached database at this path, so the graph is built from scratch",
            "why": _CACHE_WHY,
        }

    reason = _cache_failure_reason(path, CURRENT_SCHEMA_MAJOR)
    if reason is None:
        return {
            "status": "hit",
            "usable": True,
            "removed": [],
            "schema_major": _schema_major(path),
            "reason": "the cached database opened, passed a quick integrity check, "
            "carries every required table, and is not from a newer schema",
            "why": _CACHE_WHY,
        }

    removed = _remove_database_files(path)
    return {
        "status": "unusable",
        "usable": False,
        "removed": removed,
        "schema_major": None,
        "reason": reason,
        "why": _CACHE_WHY,
    }


_CACHE_WHY = (
    "a restored database is validated before it is trusted. When it fails, the "
    "file and its write-ahead siblings are removed so the analysis falls back to "
    "a full build. Falling back costs one slow run; analysing a corrupt restore "
    "would cost a wrong answer that nothing downstream could detect."
)


def _connect_read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)


def _cache_failure_reason(path: Path, current_schema_major: int) -> str | None:
    """Why the cached database cannot be used, or None when it can."""
    try:
        conn = _connect_read_only(path)
    except sqlite3.Error as e:
        return f"the cached database could not be opened: {e}"
    try:
        # quick_check executes a real read, which is what catches a file that is
        # not a database at all: sqlite3.connect alone opens lazily and succeeds.
        row = conn.execute("PRAGMA quick_check(1);").fetchone()
        if not row or str(row[0]).lower() != "ok":
            return f"the cached database failed its integrity check: {row[0] if row else 'no result'}"
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table';")}
        missing = [t for t in CACHE_REQUIRED_TABLES if t not in names]
        if missing:
            return "the cached database is missing required tables: " + ", ".join(sorted(missing))
        meta = conn.execute("SELECT value FROM meta WHERE key='schema_major';").fetchone()
        if meta is not None:
            try:
                found = int(meta[0])
            except (TypeError, ValueError):
                return f"the cached database records an unreadable schema version: {meta[0]!r}"
            if found > current_schema_major:
                return (
                    f"the cached database was written by a newer major schema ({found}) "
                    f"than this build supports ({current_schema_major})"
                )
    except sqlite3.Error as e:
        return f"the cached database could not be read: {e}"
    finally:
        conn.close()
    return None


def _schema_major(path: Path) -> int | None:
    try:
        conn = _connect_read_only(path)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute("SELECT value FROM meta WHERE key='schema_major';").fetchone()
        return int(row[0]) if row is not None else None
    except (sqlite3.Error, TypeError, ValueError):
        return None
    finally:
        conn.close()


def _remove_database_files(path: Path) -> list[str]:
    """Remove the database and its write-ahead siblings, reporting what went.

    The ``-wal`` and ``-shm`` files must go with it: leaving a write-ahead log
    beside a deleted database is how a "clean rebuild" ends up reading a stale
    page on the next open.
    """
    removed: list[str] = []
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        try:
            if candidate.exists():
                candidate.unlink()
                removed.append(candidate.name)
        except OSError:
            continue
    return sorted(removed)


def render_markdown(report: dict) -> str:
    s = report["summary"]
    lines = [
        "# D-Knowledge_Graph code analysis report",
        "",
        f"- Repository: `{report['repo']}`",
        f"- Generated: {report['generated_at']}",
        f"- Files: {s['files']}",
        f"- Symbols: {s['total_symbols']}",
        f"- Edges: {s['total_edges']}",
        "",
        "## Symbols by kind",
        "",
        "| kind | count |",
        "| --- | --- |",
    ]
    for kind, count in sorted(s["symbols_by_kind"].items()):
        lines.append(f"| {kind} | {count} |")
    lines += ["", "## Edges by predicate", "", "| predicate | count |", "| --- | --- |"]
    for pred, count in sorted(s["edges_by_predicate"].items()):
        lines.append(f"| {pred} | {count} |")
    impact = report.get("impact")
    if impact is not None:
        lines += [
            "",
            "## Change impact (advisory, structural)",
            "",
            f"- Base ref: `{impact['base_ref']}`",
            f"- Changed files: {len(impact['changed_files'])}",
            f"- Structural impacted entities: {impact['impacted_count']}",
            "",
            f"_{impact['advisory']}._",
        ]
        if impact["changed_files"]:
            lines += ["", "### Changed files", ""]
            for f in impact["changed_files"]:
                lines.append(f"- `{f}`")
    else:
        lines += ["", "_No base ref supplied; change-impact analysis skipped._"]
    if report.get("review") is not None:
        # One renderer for the review sections, shared with the pull-request
        # comment, so the two surfaces cannot drift into disagreeing.
        from .pr_comment import render_review_sections

        lines += ["", *render_review_sections(report)]
    return "\n".join(lines) + "\n"

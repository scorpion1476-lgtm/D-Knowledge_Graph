"""Advisory risk score for a change set, and the opt-in churn signal.

A change-set summary that says "47 entities impacted" is not comparable across
repositories and cannot be calibrated: 47 is alarming in a small library and
unremarkable in a monolith. This module turns the same graph facts into a score
in 0 to 1 by placing each factor against THIS graph's own distribution, so the
number means "unusual here" rather than "large in the absolute".

Five structural factors, each documented, each reported with its own
contribution to the total:

  execution-flow participation  how many entry points reach the symbol
  community crossing            how many communities its neighbours span
  test-coverage status          whether any test edge points at it
  security-sensitive naming     whether its name is in the published vocabulary
  caller count                  its inbound call degree

The weights are additive constants that sum to one, so the weighted sum is in
0 to 1 by construction. Every factor is normalised by nearest-rank percentile
against the observed distribution in this graph, so no constant is tuned to a
corpus. The level cuts are derived the same way, from the distribution of scores
this graph actually produces, and both the cut and its derivation are reported.

The change-frequency (churn) factor is DIFFERENT in kind: it comes from git
history rather than from the graph, so it is opt-in, never enabled by default,
reported in its own block, and can only ever RAISE the combined score. The
structural score is always reported unchanged next to it.

Everything here is ADVISORY. The underlying edges are structural and
over-approximate, so a high score is a reason to look, not a finding.
"""

from __future__ import annotations

import math
import subprocess
from collections.abc import Iterable, Sequence
from pathlib import Path

from ..core.db import Database
from ..core.errors import IngestError
from .analysis import (
    DEFAULT_MAX_NODES,
    STRUCTURAL_PREDICATES,
    TESTED_BY_PREDICATE,
    CodeGraphView,
    load_code_graph,
)
from .deadcode import ENTRY_POINT_KINDS, ENTRY_POINT_NAMES

CALL_PREDICATE = "code:calls"

# Additive weights. They sum to 1.0, which is what puts the score in 0 to 1
# without a clamp. They are a stated editorial judgement about what makes a
# change risky, not a measurement, and they are reported with every result so a
# reader can disagree with the arithmetic rather than with a black box.
WEIGHTS = {
    "execution_flow_participation": 0.25,
    "caller_count": 0.25,
    "community_crossing": 0.20,
    "test_coverage": 0.20,
    "security_sensitive_naming": 0.10,
}

# The published security vocabulary. A name is not evidence of a vulnerability;
# it is evidence that a human should read the change. The list is short and
# stated so a reader can see exactly what triggers the factor, and it is matched
# on lowercased substrings of the symbol name and its path.
SECURITY_TERMS = (
    "auth",
    "credential",
    "crypt",
    "decrypt",
    "encrypt",
    "hash",
    "login",
    "passwd",
    "password",
    "permission",
    "privilege",
    "secret",
    "session",
    "sign",
    "token",
    "verify",
)

# Level names and the percentile of THIS graph's own score distribution that
# opens each one. Positions, not tuned values: the cut is reported next to the
# level so the arithmetic is checkable.
LEVEL_PERCENTILES = (("low", 0), ("moderate", 50), ("elevated", 75), ("high", 90))
LEVEL_NAMES = tuple(name for name, _p in LEVEL_PERCENTILES)

# How much churn can raise a score. Applied to the headroom above the structural
# score, so churn never lowers a score and never pushes one past 1.0.
CHURN_WEIGHT = 0.30

# Bounds on the git history read. A repository with a hundred thousand commits
# must not turn an opt-in signal into an unbounded subprocess.
DEFAULT_CHURN_COMMITS = 500
_GIT_TIMEOUT = 60
_PLACES = 4


# -- churn (opt-in, git history) ---------------------------------------------


def _git(repo: Path, *args: str) -> str:
    cmd = ["git", "-C", str(repo), *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        raise IngestError(f"git failed to run: {e}") from e
    if proc.returncode != 0:
        raise IngestError(f"git {' '.join(args)} failed (rc={proc.returncode})")
    return proc.stdout


def file_churn(
    repo: str | Path, *, max_commits: int = DEFAULT_CHURN_COMMITS, since: str | None = None
) -> dict:
    """How many of the last ``max_commits`` commits touched each file.

    Local git history only: no network, list arguments, no shell, bounded
    timeout, and a bounded number of commits. Returns counts keyed by
    repository-relative path plus the window that produced them, because a churn
    number without its window is not comparable to anything.
    """
    repo = Path(repo)
    max_commits = max(1, min(int(max_commits), 100000))
    args = ["log", f"--max-count={max_commits}", "--name-only", "--pretty=format:%H"]
    if since:
        args.insert(1, f"--since={since}")
    out = _git(repo, *args)

    counts: dict[str, int] = {}
    commits = 0
    seen_in_commit: set[str] = set()
    for line in out.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if len(stripped) == 40 and all(c in "0123456789abcdef" for c in stripped):
            commits += 1
            seen_in_commit = set()
            continue
        if stripped in seen_in_commit:
            continue
        seen_in_commit.add(stripped)
        counts[stripped] = counts.get(stripped, 0) + 1
    return {
        "counts": dict(sorted(counts.items())),
        "commits_read": commits,
        "max_commits": max_commits,
        "since": since,
        "why": (
            "counted from local git history only. A file's count is the number of "
            "commits in this window that touched it, so it is comparable only "
            "against other counts from the same window."
        ),
    }


# -- the structural score ----------------------------------------------------


def _percentile_rank(value: float, ordered: Sequence[float]) -> float:
    """Where ``value`` sits in an already-sorted distribution, in 0 to 1.

    Nearest rank: the share of observations at or below the value. An empty or
    single-valued distribution has no spread to place anything in, so it returns
    0.0 rather than inventing a position.
    """
    if not ordered or ordered[0] == ordered[-1]:
        return 0.0
    below = 0
    for observed in ordered:
        if observed <= value:
            below += 1
        else:
            break
    return round(below / len(ordered), 6)


def _percentile(ordered: Sequence[float], percentile: int) -> float:
    if not ordered:
        return 0.0
    rank = max(1, math.ceil(percentile / 100 * len(ordered)))
    return float(ordered[min(rank, len(ordered)) - 1])


def is_security_sensitive(name: str, path: str = "") -> bool:
    """Whether the published vocabulary matches this symbol's name or path."""
    haystack = f"{name} {path}".lower()
    return any(term in haystack for term in SECURITY_TERMS)


class _RiskModel:
    """Per-symbol factor values and the distributions they are placed against.

    Built once per call so a change set of many symbols pays for the graph walk
    once rather than once per symbol.
    """

    def __init__(self, view: CodeGraphView) -> None:
        self.view = view
        self.in_calls = view.in_adjacency((CALL_PREDICATE,))
        self.tested = view.in_adjacency((TESTED_BY_PREDICATE,))
        self.tested_out = view.out_adjacency((TESTED_BY_PREDICATE,))
        self.neighbours = view.undirected_adjacency(STRUCTURAL_PREDICATES)
        self.communities = view.communities(STRUCTURAL_PREDICATES)
        self.reach = _entry_point_reach(view)

        symbols = view.symbol_ids()
        self._callers = sorted(float(len(self.in_calls.get(s, ()))) for s in symbols)
        self._reach = sorted(float(self.reach.get(s, 0)) for s in symbols)
        self._crossing = sorted(float(self._crossing_count(s)) for s in symbols)

    def _crossing_count(self, node_id: str) -> int:
        own = self.communities.get(node_id)
        return len({self.communities.get(n) for n in self.neighbours.get(node_id, ())} - {own})

    def factors(self, node_id: str) -> dict[str, float]:
        """Each factor normalised into 0 to 1 for one symbol."""
        node = self.view.nodes[node_id]
        has_test = bool(self.tested_out.get(node_id)) or bool(self.tested.get(node_id))
        return {
            "execution_flow_participation": _percentile_rank(
                float(self.reach.get(node_id, 0)), self._reach
            ),
            "caller_count": _percentile_rank(
                float(len(self.in_calls.get(node_id, ()))), self._callers
            ),
            "community_crossing": _percentile_rank(
                float(self._crossing_count(node_id)), self._crossing
            ),
            # Untested is the risky state, so the factor is the ABSENCE of a
            # test edge. Reported as a 0/1 because there is no distribution to
            # place a boolean against.
            "test_coverage": 0.0 if has_test else 1.0,
            "security_sensitive_naming": 1.0 if is_security_sensitive(node.display, node.path) else 0.0,
        }

    def raw(self, node_id: str) -> dict[str, object]:
        node = self.view.nodes[node_id]
        return {
            "callers": len(self.in_calls.get(node_id, ())),
            "entry_points_reaching": self.reach.get(node_id, 0),
            "communities_crossed": self._crossing_count(node_id),
            "has_test_edge": bool(self.tested_out.get(node_id)) or bool(self.tested.get(node_id)),
            "security_terms_matched": sorted(
                t for t in SECURITY_TERMS if t in f"{node.display} {node.path}".lower()
            ),
        }

    def score(self, node_id: str) -> tuple[float, dict[str, float]]:
        """The weighted score and the per-factor contributions that sum to it.

        The score is the SUM OF THE ROUNDED contributions rather than the
        rounded sum, so the breakdown adds up exactly to the reported total and
        a reader checking the arithmetic is never off by a rounding step.
        """
        normalised = self.factors(node_id)
        contributions = {
            name: round(WEIGHTS[name] * value, _PLACES) for name, value in normalised.items()
        }
        return round(sum(contributions.values()), _PLACES), contributions


def _entry_point_reach(view: CodeGraphView) -> dict[str, int]:
    """For each node, how many entry points reach it through call edges.

    Forward breadth-first search from each entry point, iteratively rather than
    recursively so a deep graph cannot exhaust the stack. Entry points are the
    same set the dead-code analysis uses, so the two features cannot disagree
    about what an entry point is.
    """
    out = view.out_adjacency((CALL_PREDICATE,))
    framework_out = view.out_adjacency(("code:routes_to", "code:dispatches"))
    entries = [
        nid
        for nid in view.node_ids()
        if view.nodes[nid].kind in ENTRY_POINT_KINDS
        or view.nodes[nid].display in ENTRY_POINT_NAMES
        or framework_out.get(nid)
    ]
    reach: dict[str, int] = {}
    for entry in entries:
        seen = {entry}
        frontier = [entry]
        while frontier:
            nxt: list[str] = []
            for node in frontier:
                # The union of both edge sets, not the first non-empty one: a
                # symbol that both calls and dispatches must expand through both.
                children = sorted({*out.get(node, ()), *framework_out.get(node, ())})
                for child in children:
                    if child in seen:
                        continue
                    seen.add(child)
                    reach[child] = reach.get(child, 0) + 1
                    nxt.append(child)
            frontier = nxt
    return reach


def _level_cuts(scores: Sequence[float]) -> dict[str, float]:
    ordered = sorted(scores)
    return {name: round(_percentile(ordered, p), _PLACES) for name, p in LEVEL_PERCENTILES}


def _level_for(score: float, cuts: dict[str, float]) -> str:
    level = LEVEL_NAMES[0]
    for name in LEVEL_NAMES:
        if score >= cuts[name]:
            level = name
    return level


def change_risk(
    db: Database,
    *,
    files: Iterable[str] | None = None,
    symbols: Iterable[str] | None = None,
    tenant_id: str = "local",
    repo: str | Path | None = None,
    with_churn: bool = False,
    churn_commits: int = DEFAULT_CHURN_COMMITS,
    max_nodes: int = DEFAULT_MAX_NODES,
    limit: int = 50,
) -> dict:
    """Score a change set given as files, symbols, or both.

    ``with_churn`` is the opt-in git signal. It is off by default, needs a
    ``repo``, is reported in its own block, and can only raise the combined
    score. The structural score is always reported unchanged alongside it.
    """
    view = load_code_graph(db, tenant_id=tenant_id, max_nodes=max_nodes)
    wanted_files = {str(f).strip() for f in (files or ()) if str(f).strip()}
    wanted_symbols = {str(s).strip() for s in (symbols or ()) if str(s).strip()}

    selected = [
        nid
        for nid in view.symbol_ids()
        if view.nodes[nid].path in wanted_files
        or view.nodes[nid].canonical in wanted_symbols
        or view.nodes[nid].display in wanted_symbols
    ]

    model = _RiskModel(view)
    all_scores = [model.score(nid)[0] for nid in view.symbol_ids()]
    cuts = _level_cuts(all_scores)

    scored: list[dict] = []
    for nid in selected:
        node = view.nodes[nid]
        score, contributions = model.score(nid)
        scored.append(
            {
                "canonical": node.canonical,
                "kind": node.kind,
                "path": node.path,
                "structural_score": score,
                "level": _level_for(score, cuts),
                "factors": [
                    {
                        "factor": name,
                        "weight": WEIGHTS[name],
                        "normalised": round(model.factors(nid)[name], _PLACES),
                        "contribution": contributions[name],
                    }
                    for name in sorted(WEIGHTS)
                ],
                "raw": model.raw(nid),
            }
        )

    churn_block: dict | None = None
    if with_churn:
        churn_block = _apply_churn(scored, view, repo, churn_commits, cuts)

    scored.sort(key=lambda s: (-s.get("combined_score", s["structural_score"]), s["canonical"]))
    limit = max(1, min(int(limit), 1000))
    change_score = max(
        (s.get("combined_score", s["structural_score"]) for s in scored), default=0.0
    )

    return {
        "symbols": scored[:limit],
        "symbol_count": len(scored),
        "returned": min(len(scored), limit),
        "change_set": {"files": sorted(wanted_files), "symbols": sorted(wanted_symbols)},
        "change_score": round(change_score, _PLACES),
        "change_level": _level_for(change_score, cuts),
        "levels": {
            "names": list(LEVEL_NAMES),
            "cuts": cuts,
            "derivation": (
                "each cut is the nearest-rank percentile of the scores THIS graph "
                "produces for every symbol in it: "
                + ", ".join(f"{name} at the {p}th" for name, p in LEVEL_PERCENTILES)
                + ". A cut is therefore a score some symbol here actually has, and "
                "it moves with the repository rather than being tuned to one."
            ),
        },
        "weights": dict(WEIGHTS),
        "churn": churn_block,
        "truncated": view.truncated,
        "why": {
            "advisory": (
                "ADVISORY. Every factor is read off a structural, "
                "over-approximate graph: a name-based call edge, a test edge the "
                "parser could connect, a community index from one run. A high "
                "score is a reason to read the change, not a defect."
            ),
            "range": (
                "the weights sum to 1.0 and every factor is normalised into 0 to "
                "1, so the score is in 0 to 1 by construction rather than by a clamp"
            ),
            "breakdown": (
                "the reported score is the sum of the rounded contributions, so "
                "the breakdown adds up to the total exactly"
            ),
            "security_vocabulary": list(SECURITY_TERMS),
            "empty_change_set": (
                "no symbol in the graph matched the change set; the score is 0 "
                "because nothing was scored, not because the change is safe"
                if not scored
                else None
            ),
        },
    }


def _apply_churn(
    scored: list[dict], view: CodeGraphView, repo, churn_commits: int, cuts: dict[str, float]
) -> dict:
    """Add the opt-in churn factor. Raises scores only; never lowers one."""
    if repo is None:
        return {
            "enabled": False,
            "reason": "churn was requested but no repository path was given",
        }
    try:
        churn = file_churn(repo, max_commits=churn_commits)
    except IngestError as e:
        return {"enabled": False, "reason": f"git history unavailable: {e}"}

    counts = churn["counts"]
    graph_paths = {view.nodes[n].path for n in view.symbol_ids() if view.nodes[n].path}
    distribution = sorted(float(counts.get(p, 0)) for p in graph_paths) or [0.0]

    for entry in scored:
        raw_count = float(counts.get(entry["path"], 0))
        normalised = _percentile_rank(raw_count, distribution)
        structural = entry["structural_score"]
        # Applied to the headroom above the structural score, so churn can only
        # raise it and can never carry it past 1.0.
        combined = structural + (1.0 - structural) * CHURN_WEIGHT * normalised
        entry["churn"] = {
            "commits_touching_file": int(raw_count),
            "normalised": round(normalised, _PLACES),
            "raised_by": round(combined - structural, _PLACES),
        }
        entry["combined_score"] = round(combined, _PLACES)
        entry["combined_level"] = _level_for(entry["combined_score"], cuts)

    return {
        "enabled": True,
        "commits_read": churn["commits_read"],
        "window": {"max_commits": churn["max_commits"], "since": churn["since"]},
        "weight": CHURN_WEIGHT,
        "why": (
            "OPT-IN and separate. This factor comes from local git history, not "
            "from the code graph, so it is never enabled by default and never "
            "folded into structural_score, which is reported unchanged. It is "
            "applied to the headroom above the structural score, so it can only "
            "raise a score and can never carry one past 1.0. A file that changes "
            "often is not thereby worse code: it may simply be where the work is."
        ),
    }

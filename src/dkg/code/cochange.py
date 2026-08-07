"""Impact accuracy against a ground truth the graph did not produce.

The existing impact measurement compares the blast radius against a ground truth
derived from the graph's own edges. That is a useful upper bound and a circular
one: it can only ever show that the traversal agrees with the edges it walked.
It cannot show that either matches how the code actually changes.

This module supplies the independent measurement. The ground truth is taken from
local git history: two files that were modified in the same commit were, on that
occasion, changed together. Predicting co-change is not the same as predicting
correctness, and the ways it differs are stated in the result rather than left
for the reader to infer. But it is evidence from outside the graph, which is
exactly what the circular measurement lacks.

Both measurements are published side by side and the independent one is labelled.
When the predictor returns no prediction for any seed, the result is reported NOT
USABLE rather than quoted as a precision of zero: a predictor that says nothing
has not been shown to be wrong, and scoring silence as a failure would make an
empty graph look like a bad one.

Local git only: list arguments, no shell, bounded timeout, bounded commit count,
no network.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable, Sequence
from pathlib import Path

from ..core.db import Database
from ..core.errors import IngestError
from .impact import blast_radius_for_file

_GIT_TIMEOUT = 60
DEFAULT_MAX_COMMITS = 500

# A commit touching hundreds of files (a reformat, a licence header sweep, a
# vendored dependency bump) links every pair in it and would swamp the signal
# with pairs nobody would call related. Commits above this are excluded and the
# exclusion is counted and reported rather than done quietly.
DEFAULT_MAX_COMMIT_FILES = 25

# How many commits a pair must share before it counts as ground truth. One
# shared commit is a coincidence as often as a relationship.
DEFAULT_MIN_SUPPORT = 2

_PLACES = 4


def _git(repo: Path, *args: str) -> str:
    cmd = ["git", "-C", str(repo), *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        raise IngestError(f"git failed to run: {e}") from e
    if proc.returncode != 0:
        raise IngestError(f"git {' '.join(args)} failed (rc={proc.returncode})")
    return proc.stdout


def commit_file_sets(
    repo: str | Path,
    *,
    max_commits: int = DEFAULT_MAX_COMMITS,
    max_commit_files: int = DEFAULT_MAX_COMMIT_FILES,
    exts: Iterable[str] | None = None,
) -> dict:
    """The set of files changed by each commit, bounded and filtered.

    Returns the retained commits' file sets, plus what was excluded and why, so
    the ground truth's construction is visible rather than implied.
    """
    repo = Path(repo)
    max_commits = max(1, min(int(max_commits), 100000))
    wanted = {e.lower() for e in exts} if exts else None
    out = _git(repo, "log", f"--max-count={max_commits}", "--name-only", "--pretty=format:%H")

    sets: list[list[str]] = []
    current: list[str] = []
    read = 0
    too_large = 0

    def flush() -> None:
        nonlocal too_large
        if not current:
            return
        if len(current) > max_commit_files:
            too_large += 1
            return
        if len(current) >= 2:
            sets.append(sorted(set(current)))

    for line in out.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if len(stripped) == 40 and all(c in "0123456789abcdef" for c in stripped):
            flush()
            current = []
            read += 1
            continue
        if wanted is not None and Path(stripped).suffix.lower() not in wanted:
            continue
        current.append(stripped)
    flush()

    return {
        "commit_file_sets": sets,
        "commits_read": read,
        "commits_used": len(sets),
        "commits_excluded_too_large": too_large,
        "max_commit_files": max_commit_files,
    }


def cochange_truth(
    commit_sets: Sequence[Sequence[str]], *, min_support: int = DEFAULT_MIN_SUPPORT
) -> dict[str, set[str]]:
    """file -> files it changed alongside in at least ``min_support`` commits."""
    support: dict[tuple[str, str], int] = {}
    for files in commit_sets:
        unique = sorted(set(files))
        for i, a in enumerate(unique):
            for b in unique[i + 1 :]:
                support[(a, b)] = support.get((a, b), 0) + 1
    truth: dict[str, set[str]] = {}
    for (a, b), count in support.items():
        if count < min_support:
            continue
        truth.setdefault(a, set()).add(b)
        truth.setdefault(b, set()).add(a)
    return truth


def _predicted_files(db: Database, seed: str, *, tenant_id: str, depth: int, max_nodes: int) -> set[str]:
    """Files the structural blast radius reaches from a seed file."""
    result = blast_radius_for_file(
        db, seed, tenant_id=tenant_id, depth=depth, max_nodes=max_nodes
    )
    files: set[str] = set()
    for impacted in result.get("impacted", []):
        canonical = str(impacted.get("canonical", ""))
        path = canonical.split("::", 1)[0] if "::" in canonical else canonical
        if path and path != seed:
            files.add(path)
    return files


def measure_against_cochange(
    db: Database,
    repo: str | Path,
    *,
    tenant_id: str = "local",
    depth: int = 3,
    max_nodes: int = 500,
    max_commits: int = DEFAULT_MAX_COMMITS,
    max_commit_files: int = DEFAULT_MAX_COMMIT_FILES,
    min_support: int = DEFAULT_MIN_SUPPORT,
    exts: Iterable[str] | None = None,
    max_seeds: int = 100,
) -> dict:
    """Measure the structural blast radius against git co-change.

    The graph-derived measurement is reported alongside and labelled circular;
    this one is labelled independent. A run that produced no prediction at all is
    reported not usable.
    """
    history = commit_file_sets(
        repo, max_commits=max_commits, max_commit_files=max_commit_files, exts=exts
    )
    truth = cochange_truth(history["commit_file_sets"], min_support=min_support)

    seeds = sorted(truth)[: max(1, int(max_seeds))]
    per_seed: list[dict] = []
    predicted_total = 0
    tp = fp = fn = 0
    for seed in seeds:
        predicted = _predicted_files(db, seed, tenant_id=tenant_id, depth=depth, max_nodes=max_nodes)
        actual = truth[seed]
        predicted_total += len(predicted)
        hits = predicted & actual
        tp += len(hits)
        fp += len(predicted - actual)
        fn += len(actual - predicted)
        per_seed.append(
            {
                "seed": seed,
                "predicted": len(predicted),
                "cochanged": len(actual),
                "hits": len(hits),
            }
        )

    usable = bool(seeds) and predicted_total > 0
    if not usable:
        reason = (
            "no seed file in the co-change ground truth is present in the code "
            "graph, so nothing could be predicted"
            if seeds
            else "git history yielded no co-change pair above the support threshold"
        )
        independent: dict = {
            "usable": False,
            "reason": reason,
            "seeds": len(seeds),
            "predictions": predicted_total,
            "note": (
                "reported NOT USABLE rather than as a precision of zero. A "
                "predictor that returned nothing has not been shown to be wrong."
            ),
        }
    else:
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        independent = {
            "usable": True,
            "precision": round(precision, _PLACES),
            "recall": round(recall, _PLACES),
            "f1": round(f1, _PLACES),
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
            "seeds": len(seeds),
            "predictions": predicted_total,
            "per_seed": per_seed,
        }

    return {
        "independent_cochange": {
            **independent,
            "label": "INDEPENDENT: ground truth from git history, not from the graph",
            "construction": {
                "commits_read": history["commits_read"],
                "commits_used": history["commits_used"],
                "commits_excluded_too_large": history["commits_excluded_too_large"],
                "max_commit_files": history["max_commit_files"],
                "min_support": min_support,
            },
        },
        "graph_derived": _graph_derived_note(depth),
        "why": {
            "why_independent": (
                "the ground truth is which files a commit modified together. "
                "Nothing about it comes from the code graph, so agreement is "
                "evidence rather than a restatement of the traversal's own edges."
            ),
            "what_it_does_not_measure": (
                "co-change is not correctness. Two files change together because "
                "one calls the other, because they share an owner, because a "
                "release bumps both, or because one commit did two things. A "
                "false positive here may be a real dependency that simply has not "
                "been edited alongside its dependent in this window, and a true "
                "positive may be a coincidence that met the support threshold."
            ),
            "bounds": (
                f"the last {max_commits} commits, excluding any commit touching "
                f"more than {max_commit_files} files, requiring {min_support} "
                "shared commits before a pair counts"
            ),
        },
    }


def _graph_derived_note(depth: int) -> dict:
    return {
        "label": "CIRCULAR: ground truth derived from the same graph the prediction walks",
        "note": (
            "the existing structural precision figure is measured against edges "
            "the graph itself produced, so it is an upper bound on agreement with "
            "those edges and says nothing about the code. It is published because "
            "hiding it would make the independent number look like the only one "
            "ever measured, not because it is the better number."
        ),
        "depth": depth,
    }

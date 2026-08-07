"""Q-09: impact accuracy against a ground truth the graph did not produce.

Real commits, not stubbed history: the whole point of this measurement is that
it comes from outside the graph, and a fabricated history would put it back
inside.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from dkg.code.cochange import (
    cochange_truth,
    commit_file_sets,
    measure_against_cochange,
)

try:
    import tree_sitter  # noqa: F401

    _TS = True
except Exception:
    _TS = False

_GIT = shutil.which("git") is not None

requires_ts = pytest.mark.skipif(not _TS, reason="tree-sitter not installed (the 'code' extra)")
requires_git = pytest.mark.skipif(not _GIT, reason="git is not installed in this environment")

if _TS:
    from dkg.code.graph import write_code_graph
    from dkg.code.parser import parse_source


def _run(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _make_repo(root):
    _run(root, "init", "-q")
    _run(root, "config", "user.email", "test@example.invalid")
    _run(root, "config", "user.name", "Test")
    _run(root, "config", "commit.gpgsign", "false")


def _commit(root, files, message):
    for rel, text in files.items():
        (root / rel).write_text(text, encoding="utf-8")
    _run(root, "add", "-A")
    _run(root, "commit", "-q", "-m", message)


# core.py and caller.py are edited together three times; unrelated.py alone.
def _build(root):
    _make_repo(root)
    for i in range(3):
        _commit(
            root,
            {
                "core.py": f"def core():\n    return {i}\n",
                "caller.py": f"from core import core\n\n\ndef caller():\n    return core() + {i}\n",
            },
            f"paired {i}",
        )
    _commit(root, {"unrelated.py": "def alone():\n    return 0\n"}, "alone")


def _ingest_worktree(db, root, names):
    files = {rel: (root / rel).read_text(encoding="utf-8") for rel in names}
    parsed = [parse_source(rel, text, language="python") for rel, text in files.items()]
    write_code_graph(db, parsed, files, source_uri=f"code://{root}")


# -- the ground truth --------------------------------------------------------


@requires_git
def test_commit_file_sets_reads_real_history(tmp_path):
    _build(tmp_path)

    history = commit_file_sets(tmp_path)

    assert history["commits_read"] == 4
    # The lone commit touched one file, so it forms no pair and is not retained.
    assert history["commits_used"] == 3
    assert all(sorted(s) == ["caller.py", "core.py"] for s in history["commit_file_sets"])


@requires_git
def test_a_sweeping_commit_is_excluded_and_counted(tmp_path):
    _make_repo(tmp_path)
    wide = {f"f{i}.py": "x = 1\n" for i in range(30)}
    _commit(tmp_path, wide, "sweep")

    history = commit_file_sets(tmp_path, max_commit_files=25)

    assert history["commits_excluded_too_large"] == 1
    assert history["commits_used"] == 0
    assert history["max_commit_files"] == 25


def test_support_threshold_keeps_a_coincidence_out():
    sets = [["a.py", "b.py"], ["a.py", "c.py"], ["a.py", "b.py"]]

    at_two = cochange_truth(sets, min_support=2)
    at_one = cochange_truth(sets, min_support=1)

    assert at_two["a.py"] == {"b.py"}, "a-c shared only one commit"
    assert at_one["a.py"] == {"b.py", "c.py"}
    assert at_two["b.py"] == {"a.py"}, "the relation is symmetric"


def test_ground_truth_is_empty_when_nothing_co_changes():
    assert cochange_truth([["only.py"]], min_support=1) == {}


# -- the measurement ---------------------------------------------------------


@requires_ts
@requires_git
def test_a_graph_that_predicts_the_co_change_scores_above_zero(db, tmp_path):
    _build(tmp_path)
    _ingest_worktree(db, tmp_path, ["core.py", "caller.py", "unrelated.py"])

    result = measure_against_cochange(db, tmp_path)
    independent = result["independent_cochange"]

    assert independent["usable"] is True
    assert independent["true_positives"] >= 1, independent
    assert independent["recall"] > 0.0
    assert 0.0 <= independent["precision"] <= 1.0
    assert independent["seeds"] == 2


@requires_ts
@requires_git
def test_the_independent_measurement_is_labelled_and_the_circular_one_published(db, tmp_path):
    _build(tmp_path)
    _ingest_worktree(db, tmp_path, ["core.py", "caller.py"])

    result = measure_against_cochange(db, tmp_path)

    assert result["independent_cochange"]["label"].startswith("INDEPENDENT")
    assert "git history" in result["independent_cochange"]["label"]
    assert result["graph_derived"]["label"].startswith("CIRCULAR")
    assert "upper bound" in result["graph_derived"]["note"]
    assert "co-change is not correctness" in result["why"]["what_it_does_not_measure"]


@requires_git
def test_no_prediction_is_reported_not_usable_rather_than_scored_zero(db, tmp_path):
    """An empty graph predicts nothing. That is silence, not a wrong answer."""
    _build(tmp_path)

    result = measure_against_cochange(db, tmp_path)
    independent = result["independent_cochange"]

    assert independent["usable"] is False
    assert "precision" not in independent, "silence must not be quoted as a precision"
    assert independent["predictions"] == 0
    assert "NOT USABLE" in independent["note"]
    assert independent["reason"]


@requires_git
def test_no_co_change_pair_is_also_reported_not_usable(db, tmp_path):
    _make_repo(tmp_path)
    _commit(tmp_path, {"solo.py": "x = 1\n"}, "one file only")

    result = measure_against_cochange(db, tmp_path)
    independent = result["independent_cochange"]

    assert independent["usable"] is False
    assert "no co-change pair" in independent["reason"]


@requires_ts
@requires_git
def test_the_construction_of_the_ground_truth_is_reported(db, tmp_path):
    _build(tmp_path)
    _ingest_worktree(db, tmp_path, ["core.py", "caller.py"])

    construction = measure_against_cochange(db, tmp_path)["independent_cochange"]["construction"]

    assert construction["commits_read"] == 4
    assert construction["commits_used"] == 3
    assert construction["min_support"] == 2
    assert construction["max_commit_files"] == 25


@requires_ts
@requires_git
def test_the_measurement_is_deterministic(db, tmp_path):
    _build(tmp_path)
    _ingest_worktree(db, tmp_path, ["core.py", "caller.py"])

    first = measure_against_cochange(db, tmp_path)
    second = measure_against_cochange(db, tmp_path)

    assert first["independent_cochange"] == second["independent_cochange"]

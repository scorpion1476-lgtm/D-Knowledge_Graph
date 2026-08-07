"""Q-08: the opt-in change-frequency signal from local git history.

The signal is only meaningful against real commits, so these tests build a small
git repository and make real commits rather than stubbing the history.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from dkg.code.risk import CHURN_WEIGHT, change_risk, file_churn

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
    return root


def _commit(root, files, message):
    for rel, text in files.items():
        (root / rel).write_text(text, encoding="utf-8")
    _run(root, "add", "-A")
    _run(root, "commit", "-q", "-m", message)


# churn.py is edited in every commit; steady.py only in the first.
def _build_history(root):
    _make_repo(root)
    _commit(
        root,
        {
            "churn.py": "def churny():\n    return 0\n",
            "steady.py": "def steady():\n    return 0\n",
        },
        "first",
    )
    for i in range(1, 4):
        _commit(root, {"churn.py": f"def churny():\n    return {i}\n"}, f"edit {i}")


@requires_git
def test_file_churn_counts_commits_per_file_and_reports_its_window(tmp_path):
    _build_history(tmp_path)

    result = file_churn(tmp_path)

    assert result["counts"]["churn.py"] == 4
    assert result["counts"]["steady.py"] == 1
    assert result["commits_read"] == 4
    assert result["max_commits"] >= 4


@requires_git
def test_the_commit_window_bounds_the_read(tmp_path):
    _build_history(tmp_path)

    result = file_churn(tmp_path, max_commits=2)

    assert result["commits_read"] == 2
    assert result["counts"]["churn.py"] == 2
    assert "steady.py" not in result["counts"], "the older commit is outside the window"


@requires_ts
@requires_git
def test_churn_is_off_by_default(db, tmp_path):
    _build_history(tmp_path)
    files = {rel: (tmp_path / rel).read_text(encoding="utf-8") for rel in ("churn.py", "steady.py")}
    parsed = [parse_source(rel, text, language="python") for rel, text in files.items()]
    write_code_graph(db, parsed, files, source_uri=f"code://{tmp_path}")

    result = change_risk(db, files=["churn.py"], repo=tmp_path)

    assert result["churn"] is None
    for symbol in result["symbols"]:
        assert "combined_score" not in symbol
        assert "churn" not in symbol


@requires_ts
@requires_git
def test_churn_raises_the_score_of_an_often_changed_file_and_never_lowers_one(db, tmp_path):
    _build_history(tmp_path)
    files = {rel: (tmp_path / rel).read_text(encoding="utf-8") for rel in ("churn.py", "steady.py")}
    parsed = [parse_source(rel, text, language="python") for rel, text in files.items()]
    write_code_graph(db, parsed, files, source_uri=f"code://{tmp_path}")

    structural = change_risk(db, files=["churn.py", "steady.py"])
    combined = change_risk(db, files=["churn.py", "steady.py"], repo=tmp_path, with_churn=True)

    before = {s["canonical"]: s["structural_score"] for s in structural["symbols"]}
    after = {s["canonical"]: s for s in combined["symbols"]}

    assert combined["churn"]["enabled"] is True
    assert combined["churn"]["weight"] == CHURN_WEIGHT
    for canonical, entry in after.items():
        # The structural score is reported UNCHANGED next to the combined one.
        assert entry["structural_score"] == before[canonical]
        assert entry["combined_score"] >= entry["structural_score"], "churn may only raise"
        assert entry["combined_score"] <= 1.0

    churny = after["churn.py::churny"]
    steady = after["steady.py::steady"]
    assert churny["churn"]["commits_touching_file"] == 4
    assert steady["churn"]["commits_touching_file"] == 1
    assert churny["churn"]["raised_by"] > steady["churn"]["raised_by"]


@requires_ts
def test_churn_without_a_repository_is_reported_disabled_not_guessed(db):
    result = change_risk(db, files=["x.py"], with_churn=True)

    assert result["churn"]["enabled"] is False
    assert "no repository path" in result["churn"]["reason"]


@requires_ts
def test_churn_on_a_directory_that_is_not_a_repository_is_reported_disabled(db, tmp_path):
    result = change_risk(db, files=["x.py"], repo=tmp_path, with_churn=True)

    assert result["churn"]["enabled"] is False
    assert "git history unavailable" in result["churn"]["reason"]


@requires_ts
@requires_git
def test_the_structural_signal_stays_separately_addressable(db, tmp_path):
    """Churn must never be folded into the structural number.

    Both files are ingested because a one-file churn distribution has no spread,
    so nothing could be placed in it and the raise would correctly be zero.
    """
    _build_history(tmp_path)
    files = {rel: (tmp_path / rel).read_text(encoding="utf-8") for rel in ("churn.py", "steady.py")}
    parsed = [parse_source(rel, text, language="python") for rel, text in files.items()]
    write_code_graph(db, parsed, files, source_uri=f"code://{tmp_path}")

    combined = change_risk(db, files=["churn.py"], repo=tmp_path, with_churn=True)
    symbol = combined["symbols"][0]

    assert "structural_score" in symbol and "combined_score" in symbol
    assert symbol["combined_score"] != symbol["structural_score"], (
        "this file churns, so the combined score must differ from the structural one"
    )
    assert "OPT-IN and separate" in combined["churn"]["why"]

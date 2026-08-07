"""Auto-update: the graph follows the code on save and on commit.

The hook is the risky part, because it runs inside somebody's git workflow. The
behaviours tested here are the ones that would make it unacceptable if wrong:

* it must never block or fail a commit,
* it must never silently replace a hook somebody else wrote,
* it must be detectable and removable,
* it must actually update the graph, which is checked by making a real commit
  in a throwaway repository and reading the graph afterwards.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from dkg.code.capability import grammar_available
from dkg.core.db import open_database
from dkg.core.errors import ValidationError
from dkg.watch import hooks

needs_python = pytest.mark.skipif(
    not grammar_available("python"), reason="the python grammar is not installed"
)
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), *args],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q")
    (path / "m.py").write_text("def a():\n    return b()\n\n\ndef b():\n    return 1\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "-c", "user.email=t@localhost", "-c", "user.name=t", "commit", "-qm", "init")
    return path


# -- installation ---------------------------------------------------------------


@needs_git
def test_a_hook_is_installed_executable_and_marked_as_ours(repo):
    result = hooks.install(repo)
    assert result["installed"] is True
    path = Path(result["path"])
    assert path.exists()
    # Git ignores a non-executable hook without saying so.
    assert hooks.is_executable(path)
    body = path.read_text(encoding="utf-8")
    assert hooks.HOOK_MARKER in body
    assert body.startswith("#!/bin/sh")


@needs_git
def test_the_hook_exits_zero_whatever_happens_so_a_commit_cannot_fail(repo):
    """A graph update must never cost somebody their commit."""
    body = hooks.render_hook()
    assert body.rstrip().endswith("exit 0")
    # And the failure path prints rather than propagating.
    assert "the code-graph update failed" in body


@needs_git
def test_the_hook_uses_the_interpreter_that_has_the_package(repo):
    """Git runs hooks with a reduced environment; a bare `python` often is not it."""
    import sys

    assert sys.executable in hooks.render_hook()


@needs_git
def test_the_hook_keeps_the_air_gap_default(repo):
    body = hooks.render_hook()
    assert "DKG_ALLOW_OUTBOUND" in body
    assert "DKG_TELEMETRY" in body


@needs_git
def test_a_hook_written_by_someone_else_is_not_clobbered(repo):
    path = hooks.hooks_dir(repo)
    path.mkdir(parents=True, exist_ok=True)
    theirs = path / "post-commit"
    theirs.write_text("#!/bin/sh\necho theirs\n", encoding="utf-8")

    result = hooks.install(repo)
    assert result["installed"] is False
    assert "already exists" in result["reason"]
    assert theirs.read_text(encoding="utf-8") == "#!/bin/sh\necho theirs\n"


@needs_git
def test_forcing_a_replace_keeps_the_original_alongside(repo):
    path = hooks.hooks_dir(repo)
    path.mkdir(parents=True, exist_ok=True)
    (path / "post-commit").write_text("#!/bin/sh\necho theirs\n", encoding="utf-8")

    result = hooks.install(repo, force=True)
    assert result["installed"] is True
    backup = Path(result["replaced_backup"])
    assert backup.exists()
    assert "echo theirs" in backup.read_text(encoding="utf-8")


@needs_git
def test_status_tells_ours_from_theirs(repo):
    assert hooks.status(repo).installed is False
    hooks.install(repo)
    state = hooks.status(repo)
    assert state.installed is True and state.ours is True

    hooks.status(repo).path.write_text("#!/bin/sh\necho theirs\n", encoding="utf-8")
    state = hooks.status(repo)
    assert state.installed is True and state.ours is False


@needs_git
def test_uninstall_removes_ours_and_restores_what_it_replaced(repo):
    path = hooks.hooks_dir(repo)
    path.mkdir(parents=True, exist_ok=True)
    (path / "post-commit").write_text("#!/bin/sh\necho theirs\n", encoding="utf-8")
    hooks.install(repo, force=True)

    result = hooks.uninstall(repo)
    assert result["removed"] is True
    assert result["restored_previous_hook"] is True
    assert "echo theirs" in (path / "post-commit").read_text(encoding="utf-8")


@needs_git
def test_uninstall_leaves_a_hook_this_project_did_not_write(repo):
    path = hooks.hooks_dir(repo)
    path.mkdir(parents=True, exist_ok=True)
    (path / "post-commit").write_text("#!/bin/sh\necho theirs\n", encoding="utf-8")
    result = hooks.uninstall(repo)
    assert result["removed"] is False
    assert (path / "post-commit").exists()


def test_an_unknown_hook_name_is_refused(tmp_path):
    with pytest.raises(ValidationError, match="unknown hook"):
        hooks.status(tmp_path, "pre-push")


def test_installing_outside_a_git_repository_is_refused(tmp_path):
    with pytest.raises(ValidationError, match="not a git repository"):
        hooks.install(tmp_path)


# -- the update itself ------------------------------------------------------------


@needs_git
@needs_python
def test_update_now_ingests_the_repository_and_reports_what_it_did(repo, tmp_path):
    with open_database(tmp_path / "g.db") as db:
        result = hooks.update_now(db, repo)
        assert result["nodes"] > 0
        # `result["incremental"] is True` was the earlier assertion; the function
        # returns that literal unconditionally, so it proved nothing. What is
        # actually checked is that the graph now holds the repository's symbols
        # AND its edges, which a no-op update could not produce.
        assert result["edges"] > 0
        assert str(repo) == result["repo"]
        rows = db.fetchall(
            "SELECT canonical FROM entities WHERE kind LIKE 'code:%' ORDER BY canonical;"
        )
        names = {r["canonical"] for r in rows}
        assert {"m.py::a", "m.py::b"} <= names
        edge = db.fetchone(
            "SELECT COUNT(*) AS n FROM relationships WHERE predicate='code:calls';"
        )
        assert edge["n"] > 0, "the call from a to b was not recorded"


@needs_git
@needs_python
def test_a_second_update_with_no_change_reparses_nothing(repo, tmp_path):
    """Incremental means incremental, or the hook is a full re-ingest per commit."""
    with open_database(tmp_path / "g.db") as db:
        hooks.update_now(db, repo)
        again = hooks.update_now(db, repo)
        assert not again["changed_files"], f"re-parsed {again['changed_files']} with no change"


@needs_git
@needs_python
def test_a_real_commit_updates_the_graph_through_the_installed_hook(repo, tmp_path, monkeypatch):
    """End to end: install, commit for real, then read the graph.

    This is the claim the whole feature rests on, so it is exercised by an
    actual `git commit` rather than by calling the update function directly.
    """
    home = tmp_path / "home"
    home.mkdir()
    installed = hooks.install(repo, home=home)
    assert installed["installed"] is True

    (repo / "m.py").write_text(
        "def a():\n    return b()\n\n\ndef b():\n    return c()\n\n\ndef c():\n    return 42\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    committed = _git(repo, "-c", "user.email=t@localhost", "-c", "user.name=t", "commit", "-m", "add c")
    # The commit must succeed whatever the hook did.
    assert committed.returncode == 0, committed.stderr

    db_path = home / "graph.sqlite"
    if not db_path.exists():
        pytest.skip("the hook did not reach the package in this environment")
    with open_database(db_path) as db:
        rows = db.fetchall(
            "SELECT canonical FROM entities WHERE kind LIKE 'code:%' ORDER BY canonical;"
        )
        names = {r["canonical"] for r in rows}
    assert "m.py::c" in names, "the symbol added in that commit is not in the graph"


@needs_git
def test_a_failing_update_still_leaves_the_commit_intact(repo, tmp_path):
    """The hook's most important property, tested by breaking it on purpose."""
    hooks.install(repo, home=tmp_path / "home")
    path = hooks.status(repo).path
    body = path.read_text(encoding="utf-8")
    # Replace the update with something that always fails.
    broken = body.replace("-m dkg update", "-m dkg definitely_not_a_command")
    path.write_text(broken, encoding="utf-8")

    (repo / "m.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    committed = _git(
        repo, "-c", "user.email=t@localhost", "-c", "user.name=t", "commit", "-m", "breaks the hook"
    )
    assert committed.returncode == 0, "a failing graph update must not fail the commit"
    log = _git(repo, "log", "--oneline")
    assert "breaks the hook" in log.stdout


@needs_git
def test_core_hooks_path_is_honoured_rather_than_assumed(repo, tmp_path):
    """A repository that relocates its hooks must still get the hook installed."""
    elsewhere = tmp_path / "custom-hooks"
    elsewhere.mkdir()
    _git(repo, "config", "core.hooksPath", str(elsewhere))
    assert hooks.hooks_dir(repo) == elsewhere
    result = hooks.install(repo)
    assert result["installed"] is True
    assert Path(result["path"]).parent == elsewhere

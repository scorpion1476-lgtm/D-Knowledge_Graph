"""N-20: opt-in inclusion of git submodule contents, off by default.

Real submodules, added from a local path so nothing reaches the network.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from dkg.code.changes import list_submodule_files, submodule_paths

try:
    import tree_sitter  # noqa: F401

    _TS = True
except Exception:
    _TS = False

_GIT = shutil.which("git") is not None

requires_ts = pytest.mark.skipif(not _TS, reason="tree-sitter not installed (the 'code' extra)")
requires_git = pytest.mark.skipif(not _GIT, reason="git is not installed in this environment")


def _run(repo, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=check, capture_output=True, text=True
    )


def _init(root):
    root.mkdir(parents=True, exist_ok=True)
    _run(root, "init", "-q")
    _run(root, "config", "user.email", "test@example.invalid")
    _run(root, "config", "user.name", "Test")
    _run(root, "config", "commit.gpgsign", "false")


def _commit(root, files, message="seed"):
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _run(root, "add", "-A")
    _run(root, "commit", "-q", "-m", message)


@pytest.fixture
def repo_with_submodule(tmp_path):
    """An outer repository with a real submodule added from a local path."""
    inner = tmp_path / "inner"
    _init(inner)
    _commit(inner, {"sublib.py": "def from_submodule():\n    return 1\n"})

    outer = tmp_path / "outer"
    _init(outer)
    _commit(outer, {"main.py": "def outer_fn():\n    return 2\n"})
    added = _run(
        outer,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        "-q",
        str(inner),
        "sub",
        check=False,
    )
    if added.returncode != 0:
        pytest.skip(f"this git refuses a local-path submodule: {added.stderr.strip()[:120]}")
    _run(outer, "commit", "-q", "-m", "add submodule")
    return outer


@requires_git
def test_an_initialised_submodule_is_listed(repo_with_submodule):
    assert submodule_paths(repo_with_submodule) == ["sub"]


@requires_git
def test_a_repository_with_no_submodule_lists_none(tmp_path):
    _init(tmp_path)
    _commit(tmp_path, {"a.py": "x = 1\n"})

    assert submodule_paths(tmp_path) == []


@requires_git
def test_submodule_files_are_prefixed_with_the_submodule_path(repo_with_submodule):
    files, read = list_submodule_files(repo_with_submodule, exts={".py"})

    assert files == ["sub/sublib.py"]
    assert read == ["sub"]


@requires_ts
@requires_git
def test_submodule_contents_are_excluded_by_default(db, repo_with_submodule):
    from dkg.code.ingest import ingest_repo

    result = ingest_repo(db, repo_with_submodule)

    canonicals = {
        r["canonical"]
        for r in db.fetchall("SELECT canonical FROM entities WHERE kind LIKE 'code:%';")
    }
    assert "main.py::outer_fn" in canonicals
    assert not any(c.startswith("sub/") for c in canonicals), sorted(canonicals)
    assert result["submodules"]["included"] is False
    assert result["submodules"]["paths"] == []


@requires_ts
@requires_git
def test_the_opt_in_includes_them(db, repo_with_submodule):
    from dkg.code.ingest import ingest_repo

    result = ingest_repo(db, repo_with_submodule, include_submodules=True)

    canonicals = {
        r["canonical"]
        for r in db.fetchall("SELECT canonical FROM entities WHERE kind LIKE 'code:%';")
    }
    assert "main.py::outer_fn" in canonicals
    assert "sub/sublib.py::from_submodule" in canonicals, sorted(canonicals)
    assert result["submodules"]["included"] is True
    assert result["submodules"]["paths"] == ["sub"]


@requires_ts
@requires_git
def test_the_flag_is_what_changes_the_shape_of_the_ingest(db, repo_with_submodule, tmp_path):
    """Same repository, two ingests, different file counts."""
    from dkg.code.ingest import ingest_repo
    from dkg.core.db import open_database

    without = ingest_repo(db, repo_with_submodule)
    with open_database(tmp_path / "second.sqlite") as other:
        with_subs = ingest_repo(other, repo_with_submodule, include_submodules=True)

    assert with_subs["parsed_files"] > without["parsed_files"]

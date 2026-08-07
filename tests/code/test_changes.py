"""Git-based incremental change detection. Skips without the code extra."""

from __future__ import annotations

import subprocess

import pytest

pytest.importorskip("tree_sitter")

from dkg.code.ingest import ingest_repo  # noqa: E402
from dkg.core.db import open_database  # noqa: E402


def _git(cwd, *a):
    subprocess.run(["git", *a], cwd=cwd, capture_output=True)


def _commit(repo, msg):
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=x@x", "-c", "user.name=x", "commit", "-qm", msg)


def test_incremental_reparses_only_changed_file(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (repo / "b.py").write_text("def b():\n    return 2\n", encoding="utf-8")
    _commit(repo, "init")
    with open_database(tmp_path / "g.db") as db:
        full = ingest_repo(db, repo, audit_path=tmp_path / "a.log")
        assert full["mode"] == "git-full"
        assert full["parsed_files"] == 2

        (repo / "b.py").write_text("def b():\n    return 2\ndef c():\n    return 3\n", encoding="utf-8")
        _commit(repo, "change b")
        inc = ingest_repo(db, repo, audit_path=tmp_path / "a.log")
        assert inc["mode"] == "git-incremental"
        assert inc["parsed_files"] == 1
        assert inc["unchanged_files"] == 1
        # the new function was added and the old graph for b.py replaced
        assert db.fetchone("SELECT 1 FROM entities WHERE canonical='b.py::c';") is not None
        # a.py untouched
        assert db.fetchone("SELECT 1 FROM entities WHERE canonical='a.py::a';") is not None


def test_incremental_preserves_inbound_cross_file_edges(tmp_path):
    from dkg.code.impact import blast_radius

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "core.py").write_text("def base():\n    return 1\n", encoding="utf-8")
    (repo / "chain.py").write_text(
        "from core import base\ndef mid():\n    return base()\ndef top():\n    return mid()\n",
        encoding="utf-8",
    )
    _commit(repo, "init")
    with open_database(tmp_path / "g.db") as db:
        ingest_repo(db, repo, audit_path=tmp_path / "a.log")
        assert "mid" in {i["display"] for i in blast_radius(db, "core.py::base")["impacted"]}

        # Change only core.py; chain.py (which calls base) is untouched.
        (repo / "core.py").write_text("def base():\n    return 1\ndef helper():\n    return 2\n", encoding="utf-8")
        _commit(repo, "change core")
        inc = ingest_repo(db, repo, audit_path=tmp_path / "a.log")
        assert inc["mode"] == "git-incremental"
        assert inc["parsed_files"] == 1

        # The inbound edge chain.mid -> core.base must survive the incremental
        # update (it was deleted with core.py and must be rebuilt).
        names = {i["display"] for i in blast_radius(db, "core.py::base")["impacted"]}
        assert "mid" in names
        assert "top" in names

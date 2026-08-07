"""Structural blast-radius. Skips without the code extra."""

from __future__ import annotations

import subprocess

import pytest

pytest.importorskip("tree_sitter")

from dkg.code.impact import blast_radius, blast_radius_for_file  # noqa: E402
from dkg.code.ingest import ingest_repo  # noqa: E402
from dkg.core.db import open_database  # noqa: E402


def _git(cwd, *a):
    subprocess.run(["git", *a], cwd=cwd, capture_output=True)


def _make_chain_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "a.py").write_text(
        "def base():\n    return 1\ndef mid():\n    return base()\ndef top():\n    return mid()\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=x@x", "-c", "user.name=x", "commit", "-qm", "init")
    return repo


def test_blast_radius_transitive_and_labelled(tmp_path):
    repo = _make_chain_repo(tmp_path)
    with open_database(tmp_path / "g.db") as db:
        ingest_repo(db, repo, audit_path=tmp_path / "a.log")
        r = blast_radius(db, "a.py::base")
        names = {i["display"] for i in r["impacted"]}
        assert "mid" in names
        assert "top" in names  # transitive (depth 2)
        assert "over-approximate" in r["why"]["note"]
        assert r["impacted_count"] == len(r["impacted"])


def test_blast_radius_unknown_entity(tmp_path):
    repo = _make_chain_repo(tmp_path)
    with open_database(tmp_path / "g.db") as db:
        ingest_repo(db, repo, audit_path=tmp_path / "a.log")
        r = blast_radius(db, "a.py::does_not_exist")
        assert r["root"] is None
        assert r["impacted"] == []


def test_blast_radius_for_file(tmp_path):
    repo = _make_chain_repo(tmp_path)
    with open_database(tmp_path / "g.db") as db:
        ingest_repo(db, repo, audit_path=tmp_path / "a.log")
        r = blast_radius_for_file(db, "a.py")
        assert "over-approximate" in r["why"]

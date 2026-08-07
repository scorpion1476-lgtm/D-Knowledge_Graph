"""Code graph in the shared store, with edge confidences. Skips without the code extra."""

from __future__ import annotations

import subprocess

import pytest

pytest.importorskip("tree_sitter")

from dkg.code.ingest import ingest_repo  # noqa: E402
from dkg.core.db import open_database  # noqa: E402


def _git(cwd, *a):
    subprocess.run(["git", *a], cwd=cwd, capture_output=True)


def _init_repo(repo):
    repo.mkdir()
    _git(repo, "init", "-q")


def _commit(repo, msg):
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=x@x", "-c", "user.name=x", "commit", "-qm", msg)


def test_graph_nodes_edges_and_confidence(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "a.py").write_text("def base():\n    return 1\ndef mid():\n    return base()\n", encoding="utf-8")
    _commit(repo, "init")
    with open_database(tmp_path / "g.db") as db:
        r = ingest_repo(db, repo, audit_path=tmp_path / "a.log")
        # module + base + mid
        assert r["nodes"] >= 3
        # code entities live in the shared entities table
        n = db.fetchone("SELECT COUNT(*) AS c FROM entities WHERE tenant_id='local' AND kind LIKE 'code:%';")["c"]
        assert n == r["nodes"]
        # a calls edge (mid -> base) with the resolved confidence 0.9
        call_edge = db.fetchone("SELECT weight FROM relationships WHERE predicate='code:calls' LIMIT 1;")
        assert call_edge is not None
        assert abs(call_edge["weight"] - 0.9) < 1e-6
        # defines edges have confidence 1.0
        def_edge = db.fetchone("SELECT weight FROM relationships WHERE predicate='code:defines' LIMIT 1;")
        assert def_edge is not None
        assert abs(def_edge["weight"] - 1.0) < 1e-6
        # provenance recorded for the source
        prov = db.fetchone("SELECT COUNT(*) AS c FROM provenance WHERE subject_id=?;", (r["source_id"],))
        assert prov["c"] >= 1

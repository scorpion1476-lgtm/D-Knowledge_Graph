"""Read-only code MCP tools. Skips without the code extra."""

from __future__ import annotations

import subprocess

import pytest

pytest.importorskip("tree_sitter")

from dkg.code.ingest import ingest_repo  # noqa: E402
from dkg.core.db import open_database  # noqa: E402
from dkg.mcp.tools import build_read_registry  # noqa: E402


def _git(cwd, *a):
    subprocess.run(["git", *a], cwd=cwd, capture_output=True)


def test_read_registry_exposes_exactly_the_expected_tools(tmp_path):
    # Pinning the whole set rather than a count: a bare number says nothing
    # about which tool appeared or vanished, and this registry is the public
    # read-only surface, so an accidental addition matters as much as a loss.
    expected = {
        "dkg.status",
        "dkg.search",
        "dkg.search.keyword",
        "dkg.search.fts",
        "dkg.graph.neighbourhood",
        "dkg.graph.community",
        "dkg.evidence.claim",
        "dkg.facets.source",
        "dkg.code.symbols",
        "dkg.code.languages",
        "dkg.code.search",
        "dkg.code.impact",
        "dkg.code.flow",
        # Graph-analysis surface.
        "dkg.code.hubs",
        "dkg.code.coupling",
        "dkg.code.gaps",
        "dkg.code.questions",
        "dkg.code.architecture",
        "dkg.graph.diff",
        # Named directed relationship queries. One tool per direction rather
        # than one with a direction flag: "who calls this" and "what does this
        # call" are different questions, and a caller that has to pass a
        # direction has to know the edge model before it can ask anything.
        "dkg.code.callers",
        "dkg.code.callees",
        "dkg.code.neighbours",
        "dkg.code.implementations",
        "dkg.code.base_types",
        "dkg.code.importers",
        "dkg.code.tests_for",
        "dkg.code.framework",
        # Answer-shaped slices, bounded traversal, and weighted criticality.
        "dkg.code.slices",
        "dkg.code.traverse",
        "dkg.code.criticality",
        "dkg.code.confidence",
        # Review and impact surface.
        "dkg.code.review_context",
        "dkg.code.impact_radius",
        # Community splitting.
        "dkg.graph.community.split",
        # Read-only inspection. The rename PREVIEW is here; applying a rename is
        # deliberately absent, because it writes source.
        "dkg.code.dead",
        "dkg.code.large",
        "dkg.code.change",
        "dkg.code.refactor",
        "dkg.code.rename.preview",
        "dkg.code.risk",
        # Readers over the precomputed catalogue. Running the stage that WRITES
        # it is command-line only.
        "dkg.code.flows",
        "dkg.code.flow.get",
        "dkg.code.flows.affected",
        "dkg.code.communities",
        "dkg.code.risk.index",
        # Orientation, prompts, docs, repositories, memory.
        "dkg.orient",
        "dkg.prompts.list",
        "dkg.prompts.get",
        "dkg.docs.section",
        "dkg.repos.list",
        "dkg.repos.search",
        "dkg.memory.list",
    }
    with open_database(tmp_path / "g.db") as db:
        reg = build_read_registry(db)
        assert set(reg.tools) == expected
        assert len(reg.tools) == len(expected)


def test_code_symbols_tool_parses_inline(tmp_path):
    with open_database(tmp_path / "g.db") as db:
        reg = build_read_registry(db)
        out = reg.call("dkg.code.symbols", {"path": "m.py", "text": "def f():\n    return 1\n", "language": "python"})
        assert any(s["name"] == "f" for s in out["symbols"])


def test_code_search_and_impact_tools(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "a.py").write_text("def base():\n    return 1\ndef mid():\n    return base()\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=x@x", "-c", "user.name=x", "commit", "-qm", "init")
    with open_database(tmp_path / "g.db") as db:
        ingest_repo(db, repo, audit_path=tmp_path / "a.log")
        reg = build_read_registry(db)
        found = reg.call("dkg.code.search", {"query": "base"})
        assert any(s["display"] == "base" for s in found["symbols"])
        impact = reg.call("dkg.code.impact", {"entity": "a.py::base"})
        assert any(i["display"] == "mid" for i in impact["impacted"])

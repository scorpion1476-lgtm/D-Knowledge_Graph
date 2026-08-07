"""Structural code-analysis report (the consumer action's underlying command).

Uses the code extra; skips without it. Verifies the structural summary, the
changed-file cross-file impact when a base ref is given, and that the report
renders in both markdown and json.
"""

from __future__ import annotations

import subprocess

import pytest

pytest.importorskip("tree_sitter")

from dkg.code.ingest import ingest_repo  # noqa: E402
from dkg.code.report import build_report, render_markdown  # noqa: E402
from dkg.core.db import open_database  # noqa: E402


def _git(cwd, *a):
    subprocess.run(["git", *a], cwd=cwd, capture_output=True)


def _make_two_file_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "lib.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (repo / "app.py").write_text("def run():\n    return helper()\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=x@x", "-c", "user.name=x", "commit", "-qm", "init")
    return repo


def test_report_summary(tmp_path):
    repo = _make_two_file_repo(tmp_path)
    with open_database(tmp_path / "g.db") as db:
        ingest_repo(db, repo, audit_path=tmp_path / "a.log")
        report = build_report(db, repo)
    s = report["summary"]
    assert s["files"] == 2
    assert s["symbols_by_kind"].get("function", 0) >= 2
    assert s["total_edges"] >= 1
    assert report["impact"] is None  # no base ref
    md = render_markdown(report)
    assert "# D-Knowledge_Graph code analysis report" in md
    assert "No base ref supplied" in md


def test_report_change_impact_is_cross_file(tmp_path):
    repo = _make_two_file_repo(tmp_path)
    # Modify a tracked file after the commit; diff against HEAD sees it changed.
    (repo / "lib.py").write_text("def helper():\n    return 2\n", encoding="utf-8")
    with open_database(tmp_path / "g.db") as db:
        ingest_repo(db, repo, audit_path=tmp_path / "a.log")
        report = build_report(db, repo, base="HEAD")
    impact = report["impact"]
    assert impact is not None
    assert impact["changed_files"] == ["lib.py"]
    # Changing lib.helper impacts app.run across files.
    assert impact["impacted_count"] == 1
    assert impact["impacted"][0]["canonical"] == "app.py::run"
    assert "advisory" in impact["advisory"] or "over-approximate" in impact["advisory"]
    md = render_markdown(report)
    assert "Change impact (advisory, structural)" in md


def test_report_json_serialisable(tmp_path):
    import json

    repo = _make_two_file_repo(tmp_path)
    with open_database(tmp_path / "g.db") as db:
        ingest_repo(db, repo, audit_path=tmp_path / "a.log")
        report = build_report(db, repo, base="HEAD")
    # Round-trips through JSON (what the action captures).
    assert json.loads(json.dumps(report))["summary"]["files"] == 2


def test_review_is_absent_unless_it_is_asked_for(tmp_path):
    repo = _make_two_file_repo(tmp_path)
    with open_database(tmp_path / "g.db") as db:
        ingest_repo(db, repo, audit_path=tmp_path / "a.log")
        report = build_report(db, repo, base="HEAD")
    assert "review" not in report


def test_review_carries_every_content_the_comment_needs(tmp_path):
    repo = _make_two_file_repo(tmp_path)
    (repo / "lib.py").write_text("def helper():\n    return 2\n", encoding="utf-8")
    with open_database(tmp_path / "g.db") as db:
        ingest_repo(db, repo, audit_path=tmp_path / "a.log")
        report = build_report(db, repo, base="HEAD", review=True)

    review = report["review"]
    assert review["scope"]["changed_files"] == ["lib.py"]

    # 1: an overall risk level, with the thresholds behind it.
    assert review["risk"]["level"] in ("low", "moderate", "elevated", "high")
    assert 0.0 <= review["risk"]["score"] <= 1.0
    assert set(review["risk"]["levels"]["cuts"]) == {"low", "moderate", "elevated", "high"}

    # 2: changed symbols with locations and coverage status, ordered by risk.
    symbols = review["changed_symbols"]
    assert symbols, "the changed file defines a symbol, so one must be scored"
    assert all(s["path"] == "lib.py" for s in symbols)
    assert [s["score"] for s in symbols] == sorted((s["score"] for s in symbols), reverse=True)
    first = symbols[0]
    assert first["canonical"] == "lib.py::helper"
    assert first["start_line"] >= 1 and first["end_line"] >= first["start_line"]
    assert first["location"].startswith("lib.py:")
    assert first["test_status"] in ("test edge present", "no test edge")

    # 3, 4, 5: flows, test gaps, and the estimated token saving are all present.
    assert isinstance(review["flows"], list)
    assert "untested_hotspots" in review["test_gaps"]
    assert review["test_gaps"]["scoped_to_change_set"] is True
    saving = review["token_saving"]
    assert saving["estimated"] is True
    assert saving["baseline_tokens"] > 0, "the changed file is real, so the baseline is not zero"
    assert saving["saved_tokens"] == saving["baseline_tokens"] - saving["graph_tokens"]

    # 6: the standing advisory caveat travels with the result.
    assert "ADVISORY" in review["why"]["advisory"]


def test_review_orders_symbols_by_risk_then_name(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "core.py").write_text(
        "def alpha():\n    return 1\n\n\ndef beta():\n    return alpha()\n\n\n"
        "def gamma():\n    return alpha()\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=x@x", "-c", "user.name=x", "commit", "-qm", "init")
    (repo / "core.py").write_text(
        "def alpha():\n    return 2\n\n\ndef beta():\n    return alpha()\n\n\n"
        "def gamma():\n    return alpha()\n",
        encoding="utf-8",
    )
    with open_database(tmp_path / "g.db") as db:
        ingest_repo(db, repo, audit_path=tmp_path / "a.log")
        report = build_report(db, repo, base="HEAD", review=True)
    rows = report["review"]["changed_symbols"]
    keys = [(-row["score"], row["canonical"]) for row in rows]
    assert keys == sorted(keys), "the order must be total: risk first, canonical name breaking ties"


def test_review_is_honest_when_there_is_no_change_set(tmp_path):
    repo = _make_two_file_repo(tmp_path)
    with open_database(tmp_path / "g.db") as db:
        ingest_repo(db, repo, audit_path=tmp_path / "a.log")
        report = build_report(db, repo, review=True)
    review = report["review"]
    assert review["scope"]["changed_files"] == []
    assert review["changed_symbols"] == []
    assert "nothing was scored" in review["scope"]["note"]
    assert review["test_gaps"]["scoped_to_change_set"] is False


def test_markdown_report_carries_the_review_when_it_is_built(tmp_path):
    repo = _make_two_file_repo(tmp_path)
    (repo / "lib.py").write_text("def helper():\n    return 3\n", encoding="utf-8")
    with open_database(tmp_path / "g.db") as db:
        ingest_repo(db, repo, audit_path=tmp_path / "a.log")
        plain = build_report(db, repo, base="HEAD")
        reviewed = build_report(db, repo, base="HEAD", review=True)
    assert "### Overall risk" not in render_markdown(plain)
    rendered = render_markdown(reviewed)
    for heading in (
        "### Overall risk",
        "### Changed symbols by risk",
        "### Affected execution flows by criticality",
        "### Test gaps",
        "### Estimated token saving",
    ):
        assert heading in rendered

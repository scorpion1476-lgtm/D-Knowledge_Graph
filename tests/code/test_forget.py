"""N-21: drop named paths from the code graph without a full rebuild."""

from __future__ import annotations

import pytest

from dkg.code.forget import forget_paths

try:
    import tree_sitter  # noqa: F401

    _TS = True
except Exception:
    _TS = False

requires_ts = pytest.mark.skipif(not _TS, reason="tree-sitter not installed (the 'code' extra)")

if _TS:
    from dkg.code.graph import write_code_graph
    from dkg.code.parser import parse_source


FILES = {
    "keep/a.py": "def kept():\n    return helper()\n",
    "drop/b.py": "def helper():\n    return 1\n\n\ndef also_dropped():\n    return 2\n",
    "drop/deep/c.py": "def deep():\n    return 3\n",
}


def _ingest(db):
    parsed = [parse_source(rel, text, language="python") for rel, text in FILES.items()]
    write_code_graph(db, parsed, dict(FILES), source_uri="test://forget")


def _canonicals(db):
    return {
        r["canonical"]
        for r in db.fetchall("SELECT canonical FROM entities WHERE kind LIKE 'code:%';")
    }


def _counts(db):
    return (
        db.fetchone("SELECT COUNT(*) AS n FROM entities WHERE kind LIKE 'code:%';")["n"],
        db.fetchone("SELECT COUNT(*) AS n FROM relationships WHERE predicate LIKE 'code:%';")["n"],
    )


def test_forgetting_nothing_reports_nothing(db):
    result = forget_paths(db, [])

    assert result["resolved_paths"] == []
    assert result["totals"]["files"] == 0
    assert result["applied"] is False


def test_an_unknown_path_is_reported_unmatched_not_silently_ignored(db):
    result = forget_paths(db, ["never/here.py"])

    assert result["unmatched"] == ["never/here.py"]
    assert result["resolved_paths"] == []


@requires_ts
def test_the_default_is_a_dry_run_that_deletes_nothing(db):
    _ingest(db)
    before_entities, before_edges = _counts(db)

    result = forget_paths(db, ["drop/b.py"])

    assert result["dry_run"] is True
    assert result["applied"] is False
    assert _counts(db) == (before_entities, before_edges)
    assert "drop/b.py::helper" in _canonicals(db)


def _rows_for_path(db, path):
    """What the database actually holds for one file, counted independently."""
    ids = [
        r["entity_id"]
        for r in db.fetchall(
            "SELECT entity_id FROM entities WHERE tenant_id='local' AND kind LIKE 'code:%' "
            "AND (canonical=? OR canonical LIKE ?);",
            (path, f"{path}::%"),
        )
    ]
    edges = 0
    if ids:
        placeholders = ",".join("?" * len(ids))
        edges = db.fetchone(
            "SELECT COUNT(*) AS n FROM relationships WHERE tenant_id='local' "
            f"AND (subject_id IN ({placeholders}) OR object_id IN ({placeholders}));",
            (*ids, *ids),
        )["n"]
    documents = [
        r["document_id"]
        for r in db.fetchall(
            "SELECT document_id FROM documents WHERE tenant_id='local' "
            "AND format LIKE 'code:%' AND json_extract(metadata_json,'$.path')=?;",
            (path,),
        )
    ]
    chunks = 0
    if documents:
        placeholders = ",".join("?" * len(documents))
        chunks = db.fetchone(
            f"SELECT COUNT(*) AS n FROM chunks WHERE document_id IN ({placeholders});",
            tuple(documents),
        )["n"]
    return {"symbols": len(ids), "edges": int(edges), "chunks": int(chunks), "documents": len(documents)}


@requires_ts
def test_the_dry_run_reports_exactly_what_the_write_removes(db):
    """The preview and the result must not disagree about scope.

    Comparing the two reports alone would only prove one code path agrees with
    itself, so each per-file figure is also checked against the rows the
    database actually holds, counted by an independent query.
    """
    _ingest(db)
    before_entities, before_edges = _counts(db)

    preview = forget_paths(db, ["drop/b.py"])

    # Per-file, against reality rather than against the other report.
    for entry in preview["per_file"]:
        actual = _rows_for_path(db, entry["path"])
        assert entry["symbols"] == actual["symbols"], entry["path"]
        assert entry["edges"] == actual["edges"], entry["path"]
        assert entry["chunks"] == actual["chunks"], entry["path"]
        assert entry["documents"] == actual["documents"], entry["path"]

    applied = forget_paths(db, ["drop/b.py"], dry_run=False)

    assert preview["totals"] == applied["totals"]
    assert preview["per_file"] == applied["per_file"]
    after_entities, after_edges = _counts(db)
    assert before_entities - after_entities == preview["totals"]["symbols"]
    assert before_edges - after_edges == preview["totals"]["edges"]
    # And the file really is gone from every table it had rows in.
    assert _rows_for_path(db, "drop/b.py") == {
        "symbols": 0,
        "edges": 0,
        "chunks": 0,
        "documents": 0,
    }


@requires_ts
def test_a_directory_means_everything_under_it(db):
    _ingest(db)

    result = forget_paths(db, ["drop"], dry_run=False)

    assert result["resolved_paths"] == ["drop/b.py", "drop/deep/c.py"]
    remaining = _canonicals(db)
    assert "keep/a.py::kept" in remaining
    assert not any(c.startswith("drop/") for c in remaining), sorted(remaining)


@requires_ts
def test_a_trailing_slash_is_accepted_for_a_directory(db):
    _ingest(db)

    result = forget_paths(db, ["drop/"])

    assert result["resolved_paths"] == ["drop/b.py", "drop/deep/c.py"]


@requires_ts
def test_forgetting_removes_the_symbols_the_edges_the_chunks_and_the_document(db):
    _ingest(db)
    document_before = db.fetchone(
        "SELECT COUNT(*) AS n FROM documents WHERE json_extract(metadata_json,'$.path')='drop/b.py';"
    )["n"]
    assert document_before == 1

    result = forget_paths(db, ["drop/b.py"], dry_run=False)

    assert result["totals"]["symbols"] >= 3, "the module node and both functions"
    assert result["totals"]["edges"] >= 1, "the call edge from keep/a.py must be counted"
    assert result["totals"]["documents"] == 1
    assert (
        db.fetchone(
            "SELECT COUNT(*) AS n FROM documents WHERE json_extract(metadata_json,'$.path')='drop/b.py';"
        )["n"]
        == 0
    )


@requires_ts
def test_an_edge_whose_other_end_survives_is_still_removed(db):
    """keep/a.py calls drop/b.py; forgetting the callee must drop the edge."""
    _ingest(db)
    edge_before = db.fetchone(
        "SELECT COUNT(*) AS n FROM relationships r "
        "JOIN entities e ON e.entity_id = r.object_id "
        "WHERE r.predicate='code:calls' AND e.canonical='drop/b.py::helper';"
    )["n"]
    assert edge_before >= 1

    forget_paths(db, ["drop/b.py"], dry_run=False)

    assert "keep/a.py::kept" in _canonicals(db), "the caller itself survives"
    assert (
        db.fetchone(
            "SELECT COUNT(*) AS n FROM relationships r "
            "JOIN entities e ON e.entity_id = r.object_id "
            "WHERE r.predicate='code:calls' AND e.canonical='drop/b.py::helper';"
        )["n"]
        == 0
    )


@requires_ts
def test_forgetting_does_not_touch_the_rest_of_the_graph(db):
    _ingest(db)
    keep_before = {c for c in _canonicals(db) if c.startswith("keep/")}

    forget_paths(db, ["drop"], dry_run=False)

    assert {c for c in _canonicals(db) if c.startswith("keep/")} == keep_before


@requires_ts
def test_forgetting_is_idempotent(db):
    _ingest(db)
    forget_paths(db, ["drop/b.py"], dry_run=False)

    second = forget_paths(db, ["drop/b.py"], dry_run=False)

    assert second["resolved_paths"] == []
    assert second["unmatched"] == ["drop/b.py"]
    assert second["totals"]["symbols"] == 0


@requires_ts
def test_the_audit_log_records_an_applied_forget(db, tmp_path):
    _ingest(db)
    audit = tmp_path / "audit.log"

    forget_paths(db, ["drop/b.py"], dry_run=False, audit_path=audit)

    rows = db.fetchall("SELECT action FROM audit_log WHERE action='code.forget';")
    assert len(rows) == 1

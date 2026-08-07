"""Execution-flow tracing: reachability, chains, recursion, over-approximation.

Gated on tree-sitter (the 'code' extra); skips honestly when absent.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

try:
    import tree_sitter  # noqa: F401

    _TS = True
except Exception:
    _TS = False

requires_ts = pytest.mark.skipif(not _TS, reason="tree-sitter not installed (the 'code' extra)")

if _TS:
    from dkg.code.flow import execution_flow
    from dkg.code.graph import write_code_graph
    from dkg.code.parser import parse_source


def _ingest(db, path, text, lang):
    pf = parse_source(path, text, language=lang)
    write_code_graph(db, [pf], {path: text}, source_uri=f"test://{path}")


@requires_ts
def test_flow_reaches_callees_not_unrelated(db):
    code = (
        "def leaf():\n    return 1\n"
        "def mid():\n    return leaf()\n"
        "def entry():\n    return mid() + leaf()\n"
        "def unrelated():\n    return 42\n"
    )
    _ingest(db, "app.py", code, "python")
    r = execution_flow(db, "app.py::entry")
    reached = {x["canonical"] for x in r["reached"]}
    assert reached == {"app.py::mid", "app.py::leaf"}
    assert "app.py::unrelated" not in reached


@requires_ts
def test_flow_enumerates_chains(db):
    code = (
        "def leaf():\n    return 1\n"
        "def mid():\n    return leaf()\n"
        "def entry():\n    return mid() + leaf()\n"
    )
    _ingest(db, "app.py", code, "python")
    chains = execution_flow(db, "app.py::entry")["chains"]
    assert ["app.py::entry", "app.py::mid", "app.py::leaf"] in chains
    assert ["app.py::entry", "app.py::leaf"] in chains


@requires_ts
def test_flow_terminates_on_recursion(db):
    code = (
        "def base():\n    return 1\n"
        "def recurse():\n    base()\n    return recurse()\n"
    )
    _ingest(db, "app.py", code, "python")
    r = execution_flow(db, "app.py::recurse")
    # Recursion must not loop forever; the callee set is finite.
    reached = {x["canonical"] for x in r["reached"]}
    assert "app.py::base" in reached


@requires_ts
def test_flow_over_approximates_on_ambiguous_names(db):
    # Two methods share a short name; name-based resolution links a call to both.
    # This documents the honest structural over-approximation.
    code = (
        "class A:\n    def save(self):\n        return 1\n"
        "class B:\n    def save(self):\n        return 2\n"
        "def caller(x):\n    return x.save()\n"
    )
    _ingest(db, "app.py", code, "python")
    reached = {x["canonical"] for x in execution_flow(db, "app.py::caller")["reached"]}
    assert "app.py::A.save" in reached
    assert "app.py::B.save" in reached  # over-approximate: only one is truly called


@requires_ts
def test_flow_entity_not_found(db):
    r = execution_flow(db, "app.py::does_not_exist")
    assert r["root"] is None
    assert r["reached"] == []


@requires_ts
def test_flow_accuracy_corpus_meets_bar():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import flow_accuracy

    summary = flow_accuracy.run()
    for lang, m in summary["per_language"].items():
        # Structural tracing on the unambiguous corpus should resolve every edge.
        assert m["edge_precision"] >= 0.9, (lang, m)
        assert m["edge_recall"] >= 0.9, (lang, m)

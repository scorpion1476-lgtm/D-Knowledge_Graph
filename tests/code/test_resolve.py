"""Type-aware resolution and dataflow: dataflow, taint, language server, corpus.

Dataflow and taint need only tree-sitter. The language-server tests skip when the
server is not staged. The corpus measurement asserts resolved is at least as
precise as structural; it never forces a green.
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
    from dkg.code.dataflow import dataflow_resolutions, taint_findings
    from dkg.code.graph import resolve_edges
    from dkg.code.lsp import resolution_available
    from dkg.code.parser import parse_source
    from dkg.code.resolve import resolve_parsed_files

_PY_SERVER = _TS and resolution_available("python")
_JS_SERVER = _TS and resolution_available("javascript")
requires_py_server = pytest.mark.skipif(not _PY_SERVER, reason="python language server not staged")

_POLY = (
    "class Dog:\n    def speak(self):\n        return 1\n"
    "class Cat:\n    def speak(self):\n        return 2\n"
    "def caller():\n    x = Dog()\n    return x.speak()\n"
)


@requires_ts
def test_structural_over_links_ambiguous_method():
    pf = parse_source("m.py", _POLY, language="python")
    targets = {e.to_qualified for e in resolve_edges([pf]) if e.predicate == "calls"}
    # Structural name matching links the call to both same-named methods.
    assert "m.py::Dog.speak" in targets
    assert "m.py::Cat.speak" in targets


@requires_ts
def test_dataflow_resolves_polymorphism_without_a_server():
    pf = parse_source("m.py", _POLY, language="python")
    res = dataflow_resolutions([pf], {"m.py": _POLY})
    assert res.get(("m.py::caller", "speak")) == "m.py::Dog.speak"
    targets = {e.to_qualified for e in resolve_edges([pf], res) if e.predicate == "calls"}
    assert "m.py::Dog.speak" in targets
    assert "m.py::Cat.speak" not in targets  # spurious edge suppressed


@requires_ts
def test_resolution_confidence_is_higher():
    pf = parse_source("m.py", _POLY, language="python")
    res = dataflow_resolutions([pf], {"m.py": _POLY})
    conf = {e.to_qualified: e.confidence for e in resolve_edges([pf], res) if e.predicate == "calls"}
    assert conf["m.py::Dog.speak"] == 0.95  # type-resolved, above name-match 0.6


@requires_ts
def test_taint_flags_source_to_sink():
    src = "def handler():\n    q = input()\n    return exec(q)\n"
    pf = parse_source("t.py", src, language="python")
    findings = taint_findings([pf], {"t.py": src})
    assert any(f["sink"] == "exec" and "q" in f["tainted_args"] for f in findings)


@requires_ts
def test_taint_clean_when_untainted():
    src = "def handler():\n    q = 1\n    return exec(q)\n"
    pf = parse_source("t.py", src, language="python")
    assert taint_findings([pf], {"t.py": src}) == []


@requires_py_server
def test_language_server_resolves_polymorphism():
    pf = parse_source("m.py", _POLY, language="python")
    res = resolve_parsed_files([pf], {"m.py": _POLY})
    assert res.get(("m.py::caller", "speak")) == "m.py::Dog.speak"


@pytest.mark.skipif(not (_PY_SERVER and _JS_SERVER), reason="python and javascript servers not both staged")
def test_resolved_precision_meets_or_beats_structural_on_corpus():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import resolution_accuracy

    summary = resolution_accuracy.run()
    for lang in ("python", "javascript"):
        m = summary["per_language"][lang]
        for metric in ("blast_radius", "execution_flow"):
            s = m[metric]["structural"]
            r = m[metric]["resolved"]
            assert r["precision"] >= s["precision"], (lang, metric, m)
            assert r["recall"] >= s["recall"], (lang, metric, m)
        # On this corpus resolution strictly improves precision.
        assert m["blast_radius"]["resolved"]["precision"] > m["blast_radius"]["structural"]["precision"]

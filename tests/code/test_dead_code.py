"""Q-10: candidate dead code, and the entry-point evidence that keeps it honest.

The analysis itself needs no parser, so the empty-graph and hand-built-graph
cases run in a core-only environment. The end-to-end case is gated on the code
extra and skips honestly when it is absent.
"""

from __future__ import annotations

import pytest

from dkg.code.analysis import CodeEdge, CodeGraphView, CodeNode
from dkg.code.deadcode import (
    FALSE_POSITIVE_SOURCES,
    candidates_from_view,
    dead_code_candidates,
)

try:
    import tree_sitter  # noqa: F401

    _TS = True
except Exception:
    _TS = False

requires_ts = pytest.mark.skipif(not _TS, reason="tree-sitter not installed (the 'code' extra)")

if _TS:
    from dkg.code.graph import write_code_graph
    from dkg.code.parser import parse_source


def _node(canonical, kind="code:function", path="a.py", display=None, lines=(1, 3)):
    return CodeNode(
        entity_id=f"ent-{canonical}",
        canonical=canonical,
        display=display if display is not None else canonical.split("::")[-1],
        kind=kind,
        path=path,
        language="python",
        start_line=lines[0],
        end_line=lines[1],
    )


def _view(nodes, edges=()):
    return CodeGraphView({n.entity_id: n for n in nodes}, list(edges))


def test_unreferenced_symbol_is_a_candidate_and_referenced_one_is_not():
    used = _node("a.py::used")
    unused = _node("a.py::unused")
    caller = _node("a.py::caller")
    edges = [CodeEdge(caller.entity_id, "code:calls", used.entity_id, 0.9)]
    result = candidates_from_view(_view([used, unused, caller], edges))

    names = [c["canonical"] for c in result["candidates"]]
    assert "a.py::unused" in names
    assert "a.py::used" not in names
    # The caller itself is unreferenced, so it is a candidate too. This is the
    # over-approximation the module documents, and asserting it keeps the test
    # from passing on a filter that silently drops roots.
    assert "a.py::caller" in names
    assert result["candidate_count"] == 2


def test_containment_alone_does_not_rescue_a_symbol():
    """A method's parent edge is not a reference.

    If code:defines counted, no nested definition could ever be reported, so
    this asserts the specific edge that must NOT rescue it.
    """
    parent = _node("a.py::Klass", kind="code:class")
    method = _node("a.py::Klass.method", kind="code:method", display="method")
    edges = [CodeEdge(parent.entity_id, "code:defines", method.entity_id, 1.0)]
    result = candidates_from_view(_view([parent, method], edges))

    assert "a.py::Klass.method" in [c["canonical"] for c in result["candidates"]]


def test_framework_inbound_edge_marks_an_entry_point_not_dead_code():
    handler = _node("app.py::handler", display="handler")
    route = _node("app.py::GET /x", kind="code:route", display="GET /x")
    edges = [CodeEdge(route.entity_id, "code:routes_to", handler.entity_id, 0.9)]
    result = candidates_from_view(_view([handler, route], edges))

    assert "app.py::handler" not in [c["canonical"] for c in result["candidates"]]
    reasons = {e["canonical"]: e["entry_point_reason"] for e in result["entry_points_excluded"]}
    assert "app.py::handler" in reasons
    assert "framework" in reasons["app.py::handler"]
    # The route node itself is an entry point by kind, not by an inbound edge.
    assert "node kind code:route" in reasons["app.py::GET /x"]


def test_entry_point_name_and_test_kind_are_excluded():
    main = _node("cli.py::main", display="main")
    a_test = _node("t.py::test_thing", kind="code:test", display="test_thing")
    ordinary = _node("lib.py::helper", display="helper")
    result = candidates_from_view(_view([main, a_test, ordinary]))

    assert [c["canonical"] for c in result["candidates"]] == ["lib.py::helper"]
    assert result["entry_points_excluded_count"] == 2


def test_modules_are_excluded_by_default_and_included_on_request():
    module = _node("a.py", kind="code:module", display="a.py")
    fn = _node("a.py::fn", display="fn")
    view = _view([module, fn])

    assert [c["canonical"] for c in candidates_from_view(view)["candidates"]] == ["a.py::fn"]
    widened = candidates_from_view(view, include_modules=True)
    assert "a.py" in [c["canonical"] for c in widened["candidates"]]


def test_candidates_are_sorted_longest_first_then_by_name():
    short = _node("a.py::short", lines=(1, 2))
    long_a = _node("a.py::zeta", lines=(10, 40))
    long_b = _node("a.py::alpha", lines=(50, 80))
    result = candidates_from_view(_view([short, long_a, long_b]))

    assert [c["canonical"] for c in result["candidates"]] == [
        "a.py::alpha",
        "a.py::zeta",
        "a.py::short",
    ]
    assert result["candidates"][0]["lines"] == 31


def test_every_documented_false_positive_source_is_reported():
    result = candidates_from_view(_view([_node("a.py::x")]))
    reported = {f["source"] for f in result["false_positive_sources"]}

    assert reported == {f["source"] for f in FALSE_POSITIVE_SOURCES}
    assert reported == {
        "dynamic dispatch",
        "reflection",
        "framework registration",
        "exported public interface",
    }
    assert all(f["detail"] for f in result["false_positive_sources"])
    assert "ADVISORY" in result["why"]["advisory"]


def test_empty_graph_returns_normally(db):
    result = dead_code_candidates(db)

    assert result["candidates"] == []
    assert result["candidate_count"] == 0
    assert result["summary"]["considered"] == 0


@requires_ts
def test_end_to_end_over_a_parsed_repository(db):
    source = (
        "def reached():\n"
        "    return 1\n"
        "\n"
        "def orphan():\n"
        "    return 2\n"
        "\n"
        "def main():\n"
        "    return reached()\n"
    )
    parsed = [parse_source("app.py", source, language="python")]
    write_code_graph(db, parsed, {"app.py": source}, source_uri="test://dead")

    result = dead_code_candidates(db)
    names = [c["canonical"] for c in result["candidates"]]

    assert "app.py::orphan" in names
    assert "app.py::reached" not in names, "reached() is called by main()"
    assert "app.py::main" not in names, "main is entry-point evidence"
    orphan = next(c for c in result["candidates"] if c["canonical"] == "app.py::orphan")
    assert orphan["path"] == "app.py"
    assert orphan["lines"] == 2


@requires_ts
def test_a_symbol_stops_being_a_candidate_once_it_is_called(db):
    """The finding must depend on the edge, not on the name.

    Ingesting the same symbol with and without a caller and asserting the list
    changes is what stops this suite passing on a hard-coded answer.
    """
    without = "def only():\n    return 1\n"
    parsed = [parse_source("x.py", without, language="python")]
    write_code_graph(db, parsed, {"x.py": without}, source_uri="test://dead2")
    before = [c["canonical"] for c in dead_code_candidates(db)["candidates"]]
    assert "x.py::only" in before

    with_caller = "def only():\n    return 1\n\ndef user():\n    return only()\n"
    parsed2 = [parse_source("x.py", with_caller, language="python")]
    write_code_graph(
        db,
        parsed2,
        {"x.py": with_caller},
        source_uri="test://dead2",
        replace_paths={"x.py"},
    )
    after = [c["canonical"] for c in dead_code_candidates(db)["candidates"]]
    assert "x.py::only" not in after
    assert "x.py::user" in after

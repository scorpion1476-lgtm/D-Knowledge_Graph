"""Q-12: query symbols above a caller-supplied line-count threshold."""

from __future__ import annotations

import pytest

from dkg.code.analysis import CodeGraphView, CodeNode
from dkg.code.size import large_symbols, large_symbols_from_view

try:
    import tree_sitter  # noqa: F401

    _TS = True
except Exception:
    _TS = False

requires_ts = pytest.mark.skipif(not _TS, reason="tree-sitter not installed (the 'code' extra)")

if _TS:
    from dkg.code.graph import write_code_graph
    from dkg.code.parser import parse_source


def _node(canonical, *, kind="code:function", path="a.py", lines=(1, 10)):
    return CodeNode(
        entity_id=f"ent-{canonical}",
        canonical=canonical,
        display=canonical.split("::")[-1],
        kind=kind,
        path=path,
        language="python",
        start_line=lines[0],
        end_line=lines[1],
    )


def _view(nodes):
    return CodeGraphView({n.entity_id: n for n in nodes}, [])


def test_threshold_is_the_callers_and_is_inclusive():
    view = _view(
        [
            _node("a.py::tiny", lines=(1, 5)),  # 5 lines
            _node("a.py::exact", lines=(10, 19)),  # 10 lines
            _node("a.py::big", lines=(30, 79)),  # 50 lines
        ]
    )
    result = large_symbols_from_view(view, min_lines=10)

    assert [s["canonical"] for s in result["symbols"]] == ["a.py::big", "a.py::exact"]
    assert result["symbols"][0]["lines"] == 50
    assert result["symbols"][1]["lines"] == 10, "the threshold is inclusive"
    assert result["filters"]["min_lines"] == 10


def test_raising_the_threshold_shrinks_the_answer():
    """The result must depend on the threshold, not on a fixed ranking."""
    view = _view([_node("a.py::x", lines=(1, 20)), _node("a.py::y", lines=(1, 100))])

    assert large_symbols_from_view(view, min_lines=10)["match_count"] == 2
    assert large_symbols_from_view(view, min_lines=25)["match_count"] == 1
    assert large_symbols_from_view(view, min_lines=500)["match_count"] == 0


def test_kind_filter_accepts_both_prefixed_and_bare_forms():
    view = _view(
        [
            _node("a.py::fn", kind="code:function", lines=(1, 30)),
            _node("a.py::Klass", kind="code:class", lines=(1, 40)),
            _node("a.py::Klass.m", kind="code:method", lines=(5, 35)),
        ]
    )

    bare = large_symbols_from_view(view, min_lines=1, kinds=["class"])
    prefixed = large_symbols_from_view(view, min_lines=1, kinds=["code:class"])
    assert [s["canonical"] for s in bare["symbols"]] == ["a.py::Klass"]
    assert [s["canonical"] for s in prefixed["symbols"]] == ["a.py::Klass"]

    two = large_symbols_from_view(view, min_lines=1, kinds=["function", "method"])
    assert sorted(s["canonical"] for s in two["symbols"]) == ["a.py::Klass.m", "a.py::fn"]


def test_path_prefix_filter_narrows_to_a_subtree():
    view = _view(
        [
            _node("src/a.py::keep", path="src/a.py", lines=(1, 20)),
            _node("tests/b.py::drop", path="tests/b.py", lines=(1, 20)),
        ]
    )
    result = large_symbols_from_view(view, min_lines=1, path_prefix="src/")

    assert [s["canonical"] for s in result["symbols"]] == ["src/a.py::keep"]
    assert result["filters"]["path_prefix"] == "src/"


def test_module_nodes_are_not_symbols():
    view = _view(
        [
            _node("a.py", kind="code:module", path="a.py", lines=(1, 400)),
            _node("a.py::fn", lines=(1, 12)),
        ]
    )
    result = large_symbols_from_view(view, min_lines=1)

    assert [s["canonical"] for s in result["symbols"]] == ["a.py::fn"]


def test_unknown_span_is_reported_not_ranked_as_zero():
    view = _view([_node("a.py::nospan", lines=(0, 0)), _node("a.py::real", lines=(1, 4))])
    result = large_symbols_from_view(view, min_lines=0)

    assert [s["canonical"] for s in result["symbols"]] == ["a.py::real"]
    assert result["distribution"]["unknown_span"] == 1
    assert result["distribution"]["measured_symbols"] == 1
    assert result["distribution"]["scanned"] == 2


def test_percentiles_are_nearest_rank_values_some_symbol_actually_has():
    lengths = [(1, 10), (1, 20), (1, 30), (1, 100)]  # 10, 20, 30, 100 lines
    view = _view([_node(f"a.py::s{i}", lines=span) for i, span in enumerate(lengths)])
    percentiles = large_symbols_from_view(view, min_lines=1)["distribution"]["percentiles"]

    observed = {10, 20, 30, 100}
    assert set(percentiles.values()) <= observed
    assert percentiles["50"] == 20
    assert percentiles["99"] == 100


def test_ties_break_on_canonical_name():
    view = _view([_node("a.py::zeta", lines=(1, 10)), _node("a.py::alpha", lines=(20, 29))])
    result = large_symbols_from_view(view, min_lines=1)

    assert [s["canonical"] for s in result["symbols"]] == ["a.py::alpha", "a.py::zeta"]


def test_empty_graph_returns_normally(db):
    result = large_symbols(db, min_lines=10)

    assert result["symbols"] == []
    assert result["distribution"]["percentiles"]["90"] == 0


@requires_ts
def test_end_to_end_over_a_parsed_repository(db):
    body = "\n".join(f"    x{i} = {i}" for i in range(40))
    source = f"def big():\n{body}\n    return 0\n\ndef small():\n    return 1\n"
    parsed = [parse_source("m.py", source, language="python")]
    write_code_graph(db, parsed, {"m.py": source}, source_uri="test://size")

    result = large_symbols(db, min_lines=20)
    names = [s["canonical"] for s in result["symbols"]]

    assert names == ["m.py::big"]
    big = result["symbols"][0]
    assert big["lines"] >= 40
    assert big["start_line"] == 1
    assert big["end_line"] >= 41
    assert "m.py::small" not in names

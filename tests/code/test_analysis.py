"""Shared code-graph analysis view: loading, adjacency, degrees, communities.

Gated on tree-sitter (the 'code' extra); skips honestly when absent.
"""

from __future__ import annotations

import pytest

try:
    import tree_sitter  # noqa: F401

    _TS = True
except Exception:
    _TS = False

requires_ts = pytest.mark.skipif(not _TS, reason="tree-sitter not installed (the 'code' extra)")

from dkg.code.analysis import (  # noqa: E402
    STRUCTURAL_PREDICATES,
    CodeGraphView,
    load_code_graph,
)

if _TS:
    from dkg.code.graph import write_code_graph
    from dkg.code.parser import parse_source


def _ingest(db, files):
    parsed = [parse_source(path, text, language=lang) for path, text, lang in files]
    texts = {path: text for path, text, _lang in files}
    write_code_graph(db, parsed, texts, source_uri="test://analysis")


CHAIN = (
    "app.py",
    "def leaf():\n    return 1\n"
    "def mid():\n    return leaf()\n"
    "def entry():\n    return mid()\n"
    "def lonely():\n    return 0\n",
    "python",
)


@requires_ts
def test_load_reads_only_code_entities_and_edges(db):
    _ingest(db, [CHAIN])
    view = load_code_graph(db)
    assert not view.is_empty
    assert all(n.kind.startswith("code:") for n in view.nodes.values())
    assert all(e.predicate.startswith("code:") for e in view.edges)
    canonicals = {n.canonical for n in view.nodes.values()}
    assert "app.py::entry" in canonicals
    assert "app.py" in canonicals  # the module node
    # metadata carries the plane facts the analysis features need
    entry = next(n for n in view.nodes.values() if n.canonical == "app.py::entry")
    assert entry.language == "python"
    assert entry.path == "app.py"


@requires_ts
def test_adjacency_directions_and_degrees(db):
    _ingest(db, [CHAIN])
    view = load_code_graph(db)
    ids = {n.canonical: n.entity_id for n in view.nodes.values()}
    out = view.out_adjacency(("code:calls",))
    inn = view.in_adjacency(("code:calls",))
    # entry -> mid -> leaf
    assert out[ids["app.py::entry"]] == [ids["app.py::mid"]]
    assert out[ids["app.py::mid"]] == [ids["app.py::leaf"]]
    assert inn[ids["app.py::leaf"]] == [ids["app.py::mid"]]
    # mid sits between two neighbours; entry and leaf have one each
    assert view.degree(ids["app.py::mid"], ("code:calls",)) == 2
    assert view.degree(ids["app.py::entry"], ("code:calls",)) == 1
    assert view.out_degree(ids["app.py::leaf"], ("code:calls",)) == 0
    assert view.in_degree(ids["app.py::entry"], ("code:calls",)) == 0
    # a function nothing calls and that calls nothing is isolated on calls
    assert view.degree(ids["app.py::lonely"], ("code:calls",)) == 0


@requires_ts
def test_adjacency_is_deterministic_and_sorted(db):
    _ingest(db, [CHAIN])
    view = load_code_graph(db)
    out = view.out_adjacency()
    for node, targets in out.items():
        assert targets == sorted(targets), node
    assert view.node_ids() == tuple(sorted(view.node_ids()))
    # repeated calls return the cached structure, identical every time
    assert view.out_adjacency() == out
    assert load_code_graph(db).node_ids() == view.node_ids()


@requires_ts
def test_weighted_undirected_edges_merge_parallel_pairs(db):
    # entry calls mid twice in source; the graph keeps one edge, and the
    # undirected projection must still yield exactly one pair.
    _ingest(
        db,
        [("m.py", "def mid():\n    return 1\ndef entry():\n    return mid() + mid()\n", "python")],
    )
    view = load_code_graph(db)
    pairs = view.weighted_undirected_edges(("code:calls",))
    assert len(pairs) == 1
    u, v, w = pairs[0]
    assert u < v  # deterministic pair ordering
    assert w > 0


@requires_ts
def test_symbol_ids_exclude_modules_and_languages_reported(db):
    _ingest(db, [CHAIN, ("util.js", "function helper() { return 2; }\n", "javascript")])
    view = load_code_graph(db)
    kinds = {view.nodes[n].kind for n in view.symbol_ids()}
    assert "code:module" not in kinds
    assert view.languages() == ["javascript", "python"]


@requires_ts
def test_communities_assign_every_node_including_isolated(db):
    _ingest(db, [CHAIN])
    view = load_code_graph(db)
    assignment = view.communities(("code:calls",))
    # every node gets a community, isolated ones included as singletons
    assert set(assignment) == set(view.node_ids())
    ids = {n.canonical: n.entity_id for n in view.nodes.values()}
    # the connected chain shares a community; the isolated function does not
    assert assignment[ids["app.py::entry"]] == assignment[ids["app.py::leaf"]]
    assert assignment[ids["app.py::lonely"]] != assignment[ids["app.py::entry"]]


@requires_ts
def test_node_cap_marks_truncation_and_drops_dangling_edges(db):
    _ingest(db, [CHAIN])
    view = load_code_graph(db, max_nodes=2)
    assert view.truncated is True
    assert len(view) == 2
    # no edge may reference a node that fell outside the cap
    for e in view.edges:
        assert e.subject_id in view.nodes
        assert e.object_id in view.nodes


def test_empty_view_is_safe():
    view = CodeGraphView({}, [])
    assert view.is_empty
    assert view.node_ids() == ()
    assert view.languages() == []
    assert view.weighted_undirected_edges() == []
    assert view.communities() == {}
    assert view.label("missing") == "missing"


@requires_ts
def test_structural_predicates_are_the_default_selection(db):
    _ingest(db, [CHAIN])
    view = load_code_graph(db)
    default_edges = {(e.subject_id, e.predicate, e.object_id) for e in view.edges_for()}
    explicit = {(e.subject_id, e.predicate, e.object_id) for e in view.edges_for(STRUCTURAL_PREDICATES)}
    assert default_edges == explicit
    # containment edges exist in the view but are not in the structural default
    assert any(e.predicate == "code:defines" for e in view.edges)
    assert all(e.predicate != "code:defines" for e in view.edges_for())

"""Hub, bridge, and chokepoint detection over the code graph.

The graph-shaped cases go through real ingestion and are gated on tree-sitter
(the 'code' extra); they skip honestly when it is absent. The maths itself is
pinned separately against hand-built adjacency dicts whose answers are stated by
hand, so a regression in Brandes or in the low-link pass is caught even in an
environment with no parser installed.
"""

from __future__ import annotations

import pytest

from dkg.code.centrality import (
    articulation_points_and_bridges,
    betweenness_centrality,
    connected_components,
    hubs_and_bridges,
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


def _ingest(db, files):
    parsed = [parse_source(path, text, language=lang) for path, text, lang in files]
    texts = {path: text for path, text, _lang in files}
    write_code_graph(db, parsed, texts, source_uri="test://centrality")


def _by_canonical(records):
    return {r["canonical"]: r for r in records}


# entry -> mid -> leaf, plus a function nothing touches.
CHAIN = (
    "app.py",
    "def leaf():\n    return 1\n"
    "def mid():\n    return leaf()\n"
    "def entry():\n    return mid()\n"
    "def lonely():\n    return 0\n",
    "python",
)

# a, b, c all call hub and nothing else.
STAR = (
    "star.py",
    "def hub():\n    return 0\n"
    "def a():\n    return hub()\n"
    "def b():\n    return hub()\n"
    "def c():\n    return hub()\n",
    "python",
)

# ring -> loop -> spin -> ring.
CYCLE = (
    "ring.py",
    "def ring():\n    return loop()\n"
    "def loop():\n    return spin()\n"
    "def spin():\n    return ring()\n",
    "python",
)


# -- ingestion-backed graphs whose answers are known by hand ----------------


@requires_ts
def test_chain_middle_is_the_only_cut_vertex_and_the_top_hub(db):
    _ingest(db, [CHAIN])
    result = hubs_and_bridges(db)

    # Five nodes: the module, plus leaf, mid, entry, lonely.
    assert result["totals"]["nodes"] == 5
    assert result["totals"]["edge_pairs"] == 2

    hubs = _by_canonical(result["hubs"])
    # mid sits between entry and leaf, so it carries the one ordered pair in
    # each direction: 2 / ((5-1) * (5-2)) = 0.166667.
    assert hubs["app.py::mid"]["betweenness"] == pytest.approx(2 / 12, abs=1e-6)
    assert hubs["app.py::entry"]["betweenness"] == 0.0
    assert hubs["app.py::leaf"]["betweenness"] == 0.0
    # 0.6 * 0.166667 + 0.4 * (2 / 4) = 0.3
    assert hubs["app.py::mid"]["hub_score"] == pytest.approx(0.3, abs=1e-6)
    assert result["hubs"][0]["canonical"] == "app.py::mid"

    assert hubs["app.py::mid"]["degree"] == 2
    assert hubs["app.py::mid"]["in_degree"] == 1
    assert hubs["app.py::mid"]["out_degree"] == 1
    assert hubs["app.py::entry"]["in_degree"] == 0
    assert hubs["app.py::leaf"]["out_degree"] == 0

    cuts = [r["canonical"] for r in result["bridges"]["articulation_points"]]
    assert cuts == ["app.py::mid"]

    # A path has no cycle, so every edge on it is a bridge.
    edges = [(b["from"], b["to"]) for b in result["bridges"]["bridge_edges"]]
    assert edges == [("app.py::entry", "app.py::mid"), ("app.py::mid", "app.py::leaf")]
    assert all(b["predicate"] == "code:calls" for b in result["bridges"]["bridge_edges"])

    assert [r["canonical"] for r in result["chokepoints"]] == ["app.py::mid"]


@requires_ts
def test_star_centre_is_the_top_hub_and_a_chokepoint(db):
    _ingest(db, [STAR])
    result = hubs_and_bridges(db)

    top = result["hubs"][0]
    assert top["canonical"] == "star.py::hub"
    assert top["degree"] == 3
    assert top["in_degree"] == 3
    assert top["out_degree"] == 0
    # Six ordered pairs among the three leaves all pass through the centre:
    # 6 / ((5-1) * (5-2)) = 0.5. It is not 1.0 because the isolated module node
    # counts in n, exactly as the documented normalization says it does.
    assert top["betweenness"] == pytest.approx(0.5, abs=1e-6)

    assert [r["canonical"] for r in result["bridges"]["articulation_points"]] == ["star.py::hub"]
    assert [r["canonical"] for r in result["chokepoints"]] == ["star.py::hub"]
    # Every spoke of a star is a bridge.
    assert result["totals"]["bridge_edges"] == 3


@requires_ts
def test_cycle_has_no_cut_vertex_and_no_bridge(db):
    _ingest(db, [CYCLE])
    result = hubs_and_bridges(db)

    assert result["totals"]["edge_pairs"] == 3
    assert result["bridges"]["articulation_points"] == []
    assert result["bridges"]["bridge_edges"] == []
    assert result["chokepoints"] == []
    # On a triangle no node lies between any other two.
    assert all(r["betweenness"] == 0.0 for r in result["hubs"])
    assert {r["canonical"] for r in result["hubs"]} == {
        "ring.py::ring",
        "ring.py::loop",
        "ring.py::spin",
    }


@requires_ts
def test_isolated_symbol_is_never_a_hub_or_a_chokepoint(db):
    _ingest(db, [CHAIN])
    result = hubs_and_bridges(db)

    canonicals = {r["canonical"] for r in result["hubs"]}
    assert "app.py::lonely" not in canonicals
    assert "app.py" not in canonicals  # the module node has no structural edge either
    assert all(r["canonical"] != "app.py::lonely" for r in result["bridges"]["articulation_points"])
    assert all(r["canonical"] != "app.py::lonely" for r in result["chokepoints"])
    # It is still counted, so the totals do not quietly shrink the graph.
    assert result["totals"]["nodes"] == 5
    # The chain, the module node, and the untouched function are three islands.
    assert result["totals"]["components"] == 3


@requires_ts
def test_disconnected_files_are_both_analysed(db):
    _ingest(
        db,
        [
            ("one.py", "def one_leaf():\n    return 1\ndef one_root():\n    return one_leaf()\n", "python"),
            ("two.py", "def two_leaf():\n    return 2\ndef two_root():\n    return two_leaf()\n", "python"),
        ],
    )
    result = hubs_and_bridges(db)

    assert result["totals"]["nodes"] == 6
    # Two call pairs plus two isolated module nodes.
    assert result["totals"]["components"] == 4
    edges = [(b["from"], b["to"]) for b in result["bridges"]["bridge_edges"]]
    assert edges == [("one.py::one_root", "one.py::one_leaf"), ("two.py::two_root", "two.py::two_leaf")]
    # A two-node component has no cut vertex: removing either end leaves a
    # single node, which is still connected.
    assert result["bridges"]["articulation_points"] == []
    assert {r["canonical"] for r in result["hubs"]} == {
        "one.py::one_root",
        "one.py::one_leaf",
        "two.py::two_root",
        "two.py::two_leaf",
    }


@requires_ts
def test_result_is_deterministic_across_runs(db):
    _ingest(db, [CHAIN, STAR, CYCLE])
    assert hubs_and_bridges(db) == hubs_and_bridges(db)


@requires_ts
def test_limit_caps_the_lists_but_not_the_totals(db):
    _ingest(db, [CHAIN])
    result = hubs_and_bridges(db, limit=1)
    assert len(result["hubs"]) == 1
    assert result["hubs"][0]["canonical"] == "app.py::mid"
    assert len(result["bridges"]["bridge_edges"]) == 1
    assert result["totals"]["bridge_edges"] == 2
    assert result["totals"]["nodes"] == 5


@requires_ts
def test_single_symbol_graph_does_not_raise(db):
    _ingest(db, [("solo.py", "def solo():\n    return 1\n", "python")])
    result = hubs_and_bridges(db)
    assert result["hubs"] == []
    assert result["bridges"]["articulation_points"] == []
    assert result["bridges"]["bridge_edges"] == []
    assert result["chokepoints"] == []
    assert result["totals"]["edge_pairs"] == 0
    assert result["truncated"] is False


@requires_ts
def test_predicate_selection_is_reported_and_narrows_the_projection(db):
    _ingest(db, [CHAIN])
    calls_only = hubs_and_bridges(db, predicates=("code:calls",))
    assert calls_only["why"]["predicates"] == ["code:calls"]
    assert calls_only["totals"]["edge_pairs"] == 2
    # An empty selection is a legal request for no projection at all.
    none = hubs_and_bridges(db, predicates=())
    assert none["totals"]["edge_pairs"] == 0
    assert none["hubs"] == []


def test_empty_graph_returns_empty_lists(db):
    result = hubs_and_bridges(db)
    assert result["hubs"] == []
    assert result["bridges"]["articulation_points"] == []
    assert result["bridges"]["bridge_edges"] == []
    assert result["chokepoints"] == []
    assert result["totals"] == {
        "nodes": 0,
        # edge_pairs counts distinct unordered node pairs; stored_edges counts
        # the underlying relationship rows. They differ whenever two symbols are
        # joined by more than one predicate, so both are reported.
        "edge_pairs": 0,
        "stored_edges": 0,
        "components": 0,
        "articulation_points": 0,
        "bridge_edges": 0,
        "chokepoints": 0,
    }
    assert result["truncated"] is False
    assert "approximate" in result["why"]["note"]


def test_why_block_declares_the_approximation_and_the_normalization(db):
    why = hubs_and_bridges(db)["why"]
    assert "Brandes" in why["algorithms"]["betweenness"]
    assert "low-link" in why["algorithms"]["articulation_points"]
    assert "(n-1)*(n-2)" in why["normalization"]
    assert "undirected" in why["projection"]
    assert "over-approximate" in why["note"]


# -- the maths, pinned against hand-built graphs ---------------------------


def test_betweenness_of_a_path_of_three():
    # a - b - c: b is on the only path between a and c, and a connected graph of
    # three nodes lets the middle reach the normalized maximum.
    scores = betweenness_centrality({"a": {"b"}, "b": {"a", "c"}, "c": {"b"}})
    assert scores == {"a": 0.0, "b": 1.0, "c": 0.0}


def test_betweenness_of_a_star_and_a_triangle():
    star = betweenness_centrality({"c": {"x", "y", "z"}, "x": {"c"}, "y": {"c"}, "z": {"c"}})
    assert star["c"] == pytest.approx(1.0, abs=1e-12)
    assert star["x"] == 0.0 and star["y"] == 0.0 and star["z"] == 0.0

    triangle = betweenness_centrality({"a": {"b", "c"}, "b": {"a", "c"}, "c": {"a", "b"}})
    assert set(triangle.values()) == {0.0}


def test_betweenness_of_a_four_cycle_splits_evenly():
    # a-b-c-d-a. For any node the only pair it can serve is the opposite pair,
    # which has two equal shortest paths, so each node earns half a pair:
    # 0.5 * 2 (both orderings) / ((4-1) * (4-2)) = 1/6.
    cycle = {"a": {"b", "d"}, "b": {"a", "c"}, "c": {"b", "d"}, "d": {"c", "a"}}
    scores = betweenness_centrality(cycle)
    for node in cycle:
        assert scores[node] == pytest.approx(1 / 6, abs=1e-12)


def test_betweenness_is_zero_on_graphs_too_small_to_have_a_middle():
    assert betweenness_centrality({}) == {}
    assert betweenness_centrality({"a": set()}) == {"a": 0.0}
    assert betweenness_centrality({"a": {"b"}, "b": {"a"}}) == {"a": 0.0, "b": 0.0}


def test_betweenness_symmetrises_and_ignores_self_loops_and_unknown_targets():
    # Only a names b, b never names a, and c points at a node outside the map.
    scores = betweenness_centrality({"a": {"b", "a"}, "b": set(), "c": {"b", "ghost"}})
    assert scores == {"a": 0.0, "b": 1.0, "c": 0.0}


def test_sampled_betweenness_scales_by_the_source_count():
    # a-b-c-d. Exact: b and c each serve two unordered pairs, so 4 / ((4-1) * (4-2)).
    path = {"a": {"b"}, "b": {"a", "c"}, "c": {"b", "d"}, "d": {"c"}}
    exact = betweenness_centrality(path)
    assert exact["b"] == pytest.approx(4 / 6, abs=1e-12)
    assert exact["c"] == pytest.approx(4 / 6, abs=1e-12)
    assert betweenness_centrality(path, sources=sorted(path)) == exact

    # Seeding from "a" alone sees b on two of its paths and c on one, then
    # scales by n/1. The result over-states b, which is exactly why a sampled
    # run is reported as an estimate rather than as an exact score.
    sampled = betweenness_centrality(path, sources=["a"])
    assert sampled["b"] == pytest.approx(2 * 4 / 6, abs=1e-12)
    assert sampled["c"] == pytest.approx(1 * 4 / 6, abs=1e-12)
    assert sampled["a"] == 0.0 and sampled["d"] == 0.0


def test_articulation_points_and_bridges_of_a_path():
    path = {"a": {"b"}, "b": {"a", "c"}, "c": {"b", "d"}, "d": {"c"}}
    cuts, bridges = articulation_points_and_bridges(path)
    assert cuts == ["b", "c"]
    assert bridges == [("a", "b"), ("b", "c"), ("c", "d")]


def test_bowtie_has_one_cut_vertex_and_no_bridge():
    # Two triangles sharing node c. Removing c splits the graph, but every edge
    # lies on a cycle so no single edge is a bridge.
    bowtie = {
        "a": {"b", "c"},
        "b": {"a", "c"},
        "c": {"a", "b", "d", "e"},
        "d": {"c", "e"},
        "e": {"c", "d"},
    }
    cuts, bridges = articulation_points_and_bridges(bowtie)
    assert cuts == ["c"]
    assert bridges == []


def test_low_link_pass_covers_every_component():
    graph = {"a": {"b"}, "b": {"a"}, "x": set(), "y": {"z"}, "z": {"y"}}
    cuts, bridges = articulation_points_and_bridges(graph)
    assert cuts == []
    assert bridges == [("a", "b"), ("y", "z")]
    assert connected_components(graph) == [["a", "b"], ["y", "z"], ["x"]]


def test_low_link_pass_handles_a_graph_deeper_than_the_recursion_limit():
    # A recursive depth-first pass would raise RecursionError well before this
    # depth; the iterative stack must not.
    depth = 3000
    names = [f"n{i:05d}" for i in range(depth)]
    chain: dict[str, set[str]] = {name: set() for name in names}
    for left, right in zip(names, names[1:]):
        chain[left].add(right)
        chain[right].add(left)
    cuts, bridges = articulation_points_and_bridges(chain)
    assert len(cuts) == depth - 2  # every node except the two endpoints
    assert len(bridges) == depth - 1
    assert connected_components(chain) == [names]


def test_connected_components_are_ordered_and_count_isolated_nodes():
    graph = {"a": {"b"}, "b": {"a"}, "c": {"b"}, "solo": set(), "pair1": {"pair2"}, "pair2": {"pair1"}}
    components = connected_components(graph)
    assert components == [["a", "b", "c"], ["pair1", "pair2"], ["solo"]]
    assert connected_components({}) == []


def test_articulation_pass_is_safe_on_trivial_graphs():
    assert articulation_points_and_bridges({}) == ([], [])
    assert articulation_points_and_bridges({"a": set()}) == ([], [])
    assert articulation_points_and_bridges({"a": {"b"}, "b": {"a"}}) == ([], [("a", "b")])

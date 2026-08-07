"""Q-15: refactoring suggestions derived from communities and coupling.

A suggestion is a stronger claim than a question, so these tests hold it to the
three things the requirement asks for: the symbols are named, the measurement
that produced it is present, and its own reason for possibly being wrong is
carried with it.
"""

from __future__ import annotations

import pytest

from dkg.code.refactor import (
    KIND_DECOUPLE,
    KIND_MERGE,
    KIND_MOVE,
    KIND_SPLIT,
    MERGE_MIN_CROSSING,
    MOVE_MAJORITY_MIN,
    RISKS,
    refactor_suggestions,
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


def _ingest(db, files, uri="test://refactor"):
    parsed = [parse_source(rel, text, language="python") for rel, text in files.items()]
    write_code_graph(db, parsed, dict(files), source_uri=uri)


def _two_clusters_with_a_stray():
    """Two dense clusters plus a symbol wired mostly into the far one."""
    cluster_a = "\n".join(
        f"def a{i}():\n    return " + " + ".join(f"a{j}()" for j in range(4) if j != i) + "\n"
        for i in range(4)
    )
    cluster_b = "\n".join(
        f"def b{i}():\n    return " + " + ".join(f"b{j}()" for j in range(4) if j != i) + "\n"
        for i in range(4)
    )
    stray = "def stray():\n    return b0() + b1() + b2()\n"
    return {"a.py": cluster_a, "b.py": cluster_b + "\n" + stray}


def test_empty_graph_returns_normally(db):
    result = refactor_suggestions(db)

    assert result["suggestions"] == []
    assert result["suggestion_count"] == 0
    assert result["totals"]["nodes"] == 0


@requires_ts
def test_every_suggestion_names_symbols_measurement_and_its_own_risk(db):
    _ingest(db, _two_clusters_with_a_stray())

    result = refactor_suggestions(db)

    assert result["suggestions"], "this shape must produce at least one suggestion"
    for suggestion in result["suggestions"]:
        assert suggestion["kind"] in {KIND_MOVE, KIND_SPLIT, KIND_MERGE, KIND_DECOUPLE}
        assert suggestion["symbols"], suggestion["title"]
        assert all(isinstance(s, str) and s for s in suggestion["symbols"])
        assert suggestion["measurement"], suggestion["title"]
        assert suggestion["why_it_may_be_wrong"] == RISKS[suggestion["kind"]]
        assert len(suggestion["why_it_may_be_wrong"]) > 60


@requires_ts
def test_every_suggestion_is_worded_as_a_suggestion(db):
    _ingest(db, _two_clusters_with_a_stray())

    result = refactor_suggestions(db)

    for suggestion in result["suggestions"]:
        assert suggestion["title"].startswith("Consider "), suggestion["title"]
        assert "Consider" in suggestion["suggestion"]
        # A suggestion states a proposal, not a verdict.
        lowered = suggestion["suggestion"].lower()
        assert "must " not in lowered and "is wrong" not in lowered


@requires_ts
def test_no_move_is_proposed_when_the_partition_agrees_with_the_neighbourhoods(db):
    """The signal is a DISAGREEMENT, so it must be silent when there is none."""
    _ingest(db, _two_clusters_with_a_stray())

    result = refactor_suggestions(db, resolution=1.0)

    assert [s for s in result["suggestions"] if s["kind"] == KIND_MOVE] == []


@requires_ts
def test_a_symbol_the_partition_isolates_from_its_neighbours_produces_a_move(db):
    """At this resolution the optimizer splits the stray into its own community
    while all three of its neighbours stay together, which is exactly the
    disagreement a move is for."""
    _ingest(db, _two_clusters_with_a_stray())

    result = refactor_suggestions(db, resolution=2.0)
    moves = [s for s in result["suggestions"] if s["kind"] == KIND_MOVE]

    assert moves, "the isolated symbol should suggest a move"
    assert moves[0]["symbols"] == ["b.py::stray"]
    measurement = moves[0]["measurement"]
    assert measurement["neighbours"] >= 3
    assert measurement["pull"] > 0.5, "a move needs a majority pull, not a tie"
    assert measurement["neighbours_in_target_community"] > measurement[
        "neighbours_in_own_community"
    ]
    assert measurement["own_community_index"] != measurement["target_community_index"]
    assert measurement["pull"] >= measurement["pull_cut"]


@requires_ts
def test_thresholds_are_reported_with_their_derivation(db):
    _ingest(db, _two_clusters_with_a_stray())

    thresholds = refactor_suggestions(db)["thresholds"]

    assert "nearest-rank percentile" in thresholds["derivation"]
    assert thresholds["move_pull_percentile"] == 75
    assert thresholds["split_percentiles"] == {"size": 90, "density": 50}
    assert thresholds["merge_traffic_percentile"] == 90
    # Every cut APPLIED is published, derived or not, so a reader can check them
    # all rather than only the ones that happen to come from a distribution.
    for key in (
        "move_pull_cut",
        "split_size_cut",
        "split_density_cut",
        "merge_traffic_cut",
        "min_neighbours_for_move",
        "move_majority_min",
        "merge_min_crossing",
    ):
        assert key in thresholds, key
        assert isinstance(thresholds[key], (int, float)), key
    assert thresholds["move_majority_min"] == MOVE_MAJORITY_MIN
    assert thresholds["merge_min_crossing"] == MERGE_MIN_CROSSING


@requires_ts
def test_every_derived_cut_is_a_value_the_graph_actually_produced(db):
    """The property that separates a derived cut from a tuned constant.

    A type check on the cut would pass for any hardcoded number. Nearest rank
    means the cut IS an observed value, so the observed distributions are
    reconstructed here and the cuts are looked up in them.
    """
    from dkg.code.analysis import STRUCTURAL_PREDICATES, load_code_graph
    from dkg.code.refactor import _density

    _ingest(db, _two_clusters_with_a_stray())
    thresholds = refactor_suggestions(db, resolution=2.0)["thresholds"]

    view = load_code_graph(db)
    communities = view.communities(STRUCTURAL_PREDICATES, resolution=2.0)
    neighbours = view.undirected_adjacency(STRUCTURAL_PREDICATES)
    members: dict[int, list[str]] = {}
    for node_id, index in communities.items():
        members.setdefault(index, []).append(node_id)

    sizes = {float(len(m)) for m in members.values()}
    densities = {_density(m, neighbours) for m in members.values() if len(m) >= 2}

    assert thresholds["split_size_cut"] in sizes, (thresholds["split_size_cut"], sorted(sizes))
    assert round(thresholds["split_density_cut"], 4) in {round(d, 4) for d in densities}

    # The move cut, reconstructed the same way: it must be a pull some node
    # in this graph actually has.
    pulls = set()
    for node_id in view.node_ids():
        near = neighbours.get(node_id, set())
        if len(near) < 3:
            continue
        own = communities.get(node_id)
        counts: dict[int | None, int] = {}
        for other in near:
            counts[communities.get(other)] = counts.get(communities.get(other), 0) + 1
        foreign = {c: n for c, n in counts.items() if c != own}
        if foreign:
            pulls.add(round(max(foreign.values()) / len(near), 4))
    assert pulls, "the fixture must produce at least one pull"
    assert round(thresholds["move_pull_cut"], 4) in pulls, (
        thresholds["move_pull_cut"],
        sorted(pulls),
    )


@requires_ts
def test_the_cuts_move_when_the_graph_moves(db):
    """A constant would not change. A distribution-derived cut has to."""
    _ingest(db, _two_clusters_with_a_stray())
    before = refactor_suggestions(db)["thresholds"]

    bigger = "\n".join(
        f"def z{i}():\n    return " + " + ".join(f"z{j}()" for j in range(9) if j != i) + "\n"
        for i in range(9)
    )
    _ingest(db, {"z.py": bigger}, uri="test://refactor")
    after = refactor_suggestions(db)["thresholds"]

    assert after["split_size_cut"] != before["split_size_cut"], (before, after)


def _synthetic(neighbour_count: int):
    """A node whose neighbours ALL sit in another community.

    Built by hand, with the partition supplied rather than detected, because a
    modularity optimizer will never place a leaf apart from its only neighbour:
    the shape the min-neighbour rule guards against cannot be reached through
    the detector, so the rule has to be exercised where it lives.
    """
    from dkg.code.analysis import CodeGraphView, CodeNode
    from dkg.code.refactor import _moves

    nodes = {}
    for name in ["subject", *[f"peer{i}" for i in range(neighbour_count)]]:
        nodes[f"ent-{name}"] = CodeNode(
            entity_id=f"ent-{name}",
            canonical=f"m.py::{name}",
            display=name,
            kind="code:function",
            path="m.py",
            language="python",
            start_line=1,
            end_line=2,
        )
    view = CodeGraphView(nodes, [])
    # Community 0 holds the subject alone; community 1 holds every peer.
    communities = {n: (0 if n == "ent-subject" else 1) for n in nodes}
    neighbours = {n: set() for n in nodes}
    for i in range(neighbour_count):
        neighbours["ent-subject"].add(f"ent-peer{i}")
        neighbours[f"ent-peer{i}"].add("ent-subject")
    members: dict[int, list[str]] = {}
    for node_id, index in communities.items():
        members.setdefault(index, []).append(node_id)
    moves, cut = _moves(view, communities, neighbours, members, 10)
    return {m["symbols"][0] for m in moves}, cut


def test_a_symbol_with_too_few_neighbours_never_produces_a_move():
    """One or two neighbours elsewhere is an edge, not a pull.

    Every other guard passes in this shape: the pull is 1.0, so it clears the
    majority rule and any derived cut. The neighbour count is the only thing
    that can stop it.
    """
    from dkg.code.refactor import MIN_NEIGHBOURS_FOR_MOVE

    assert MIN_NEIGHBOURS_FOR_MOVE == 3
    for count in range(1, MIN_NEIGHBOURS_FOR_MOVE):
        moved, _cut = _synthetic(count)
        assert moved == set(), f"{count} neighbour(s) must not produce a move"


def test_at_the_neighbour_threshold_a_move_is_produced():
    """The other half of the rule: the guard must not block everything."""
    from dkg.code.refactor import MIN_NEIGHBOURS_FOR_MOVE

    moved, _cut = _synthetic(MIN_NEIGHBOURS_FOR_MOVE)

    assert moved == {"m.py::subject"}


@requires_ts
def test_a_split_neighbourhood_is_not_a_pull(db):
    """At or below half, the neighbourhood is not pulling anywhere."""
    _ingest(db, _two_clusters_with_a_stray())

    result = refactor_suggestions(db, resolution=2.0)

    for suggestion in result["suggestions"]:
        if suggestion["kind"] != KIND_MOVE:
            continue
        assert suggestion["measurement"]["pull"] > MOVE_MAJORITY_MIN, suggestion["title"]


def _two_communities_joined_by(edge_count: int):
    """Two communities joined by exactly ``edge_count`` crossing edges.

    Built by hand so the crossing count is the only variable. When every pair in
    the graph crosses the same number of times the derived cut equals that
    number, so the derived cut alone cannot reject a single edge; only the
    plural-traffic rule can.
    """
    from dkg.code.analysis import CodeGraphView, CodeNode
    from dkg.code.refactor import _merges

    names = [f"a{i}" for i in range(3)] + [f"b{i}" for i in range(3)]
    nodes = {
        f"ent-{n}": CodeNode(
            entity_id=f"ent-{n}",
            canonical=f"m.py::{n}",
            display=n,
            kind="code:function",
            path="m.py",
            language="python",
            start_line=1,
            end_line=2,
        )
        for n in names
    }
    view = CodeGraphView(nodes, [])
    communities = {f"ent-{n}": (0 if n.startswith("a") else 1) for n in names}
    neighbours: dict[str, set[str]] = {f"ent-{n}": set() for n in names}

    def join(x, y):
        neighbours[f"ent-{x}"].add(f"ent-{y}")
        neighbours[f"ent-{y}"].add(f"ent-{x}")

    # Dense inside each community.
    for i in range(3):
        for j in range(i + 1, 3):
            join(f"a{i}", f"a{j}")
            join(f"b{i}", f"b{j}")
    for i in range(edge_count):
        join(f"a{i}", f"b{i}")

    members: dict[int, list[str]] = {}
    for node_id, index in communities.items():
        members.setdefault(index, []).append(node_id)
    merges, cut = _merges(view, communities, neighbours, members, 10)
    return merges, cut


def test_a_single_crossing_edge_is_not_traffic():
    """A merge needs plural traffic, not one reference."""
    merges, _cut = _two_communities_joined_by(1)

    assert merges == [], "one crossing edge is a reference, not traffic"


def test_two_crossing_edges_are_traffic():
    """The other half: the rule must not reject everything."""
    merges, _cut = _two_communities_joined_by(2)

    assert merges, "two crossing edges should be able to suggest a merge"
    assert merges[0]["measurement"]["crossing_edges"] >= MERGE_MIN_CROSSING


@requires_ts
def test_every_reported_merge_clears_the_plural_traffic_rule(db):
    _ingest(db, _two_clusters_with_a_stray())

    result = refactor_suggestions(db, resolution=2.0)
    merges = [s for s in result["suggestions"] if s["kind"] == KIND_MERGE]

    for suggestion in merges:
        assert suggestion["measurement"]["crossing_edges"] >= MERGE_MIN_CROSSING, suggestion["title"]


@requires_ts
def test_a_move_carries_its_own_reason_for_possibly_being_wrong(db):
    """Checked at the resolution that actually produces a move.

    The general assertion runs where no move is produced, so without this the
    move kind's risk text would never be looked at.
    """
    _ingest(db, _two_clusters_with_a_stray())

    moves = [s for s in refactor_suggestions(db, resolution=2.0)["suggestions"] if s["kind"] == KIND_MOVE]

    assert moves, "this resolution must produce a move"
    for suggestion in moves:
        assert suggestion["why_it_may_be_wrong"] == RISKS[KIND_MOVE]
        assert len(suggestion["why_it_may_be_wrong"]) > 60


@requires_ts
def test_suggestions_are_deterministic_across_runs(db):
    _ingest(db, _two_clusters_with_a_stray())

    first = refactor_suggestions(db)
    second = refactor_suggestions(db)

    assert first["suggestions"] == second["suggestions"]
    assert first["by_kind"] == second["by_kind"]


@requires_ts
def test_the_per_kind_limit_is_honoured(db):
    _ingest(db, _two_clusters_with_a_stray())

    result = refactor_suggestions(db, per_kind=1, limit=100)

    for kind, count in result["by_kind"].items():
        assert count <= 1, kind


@requires_ts
def test_the_standing_caveats_travel_with_the_result(db):
    _ingest(db, _two_clusters_with_a_stray())

    why = refactor_suggestions(db)["why"]

    assert "SUGGESTIONS, not findings" in why["advisory"]
    assert "never compare them across runs" in why["community_indices"]
    assert set(why["per_kind_risks"]) == {KIND_MOVE, KIND_SPLIT, KIND_MERGE, KIND_DECOUPLE}


@requires_ts
def test_suggestions_change_when_the_graph_changes(db):
    """The output must be a function of the graph, not a fixed list."""
    _ingest(db, _two_clusters_with_a_stray())
    with_stray = refactor_suggestions(db)

    db.execute("DELETE FROM relationships;")
    db.execute("DELETE FROM entities;")
    _ingest(db, {"c.py": "def alone():\n    return 1\n"}, uri="test://refactor-2")
    without = refactor_suggestions(db)

    assert with_stray["suggestion_count"] > 0
    assert without["suggestion_count"] == 0

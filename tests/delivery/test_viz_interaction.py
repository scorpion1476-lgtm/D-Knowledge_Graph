"""Offline viewer interaction: search, community legend, degree-scaled nodes (R-21).

Every assertion here is about what the exported file actually contains, not
about what the viewer intends. The layout is computed in Python before the file
is written, so these tests can check the drawn geometry directly rather than
running a browser, and they can check that two exports of the same graph are
byte-identical, which a browser-side simulation could never be.

Seeds the graph directly, so no optional extra is needed.
"""

from __future__ import annotations

import json
import math
import re

from dkg.core.db import open_database
from dkg.export.graphdata import (
    COMMUNITY_COLOURS,
    MAX_NODE_RADIUS,
    MIN_NODE_RADIUS,
    community_groups,
    degree_map,
    layout_positions,
    load_graph,
    radius_for_degree,
)
from dkg.export.viz import render_html

# Four disjoint triangles give a partition a detector must find; a hub with six
# leaves gives degrees from 1 to 6; one isolated node covers the degree-zero case.
TRIANGLES = 4
HUB_LEAVES = 6
KINDS = ("code:class", "code:function", "code:module", "code:method", "code:type", "code:test")


def _seed(db):
    def entity(eid, kind, canonical, display):
        db.execute(
            "INSERT INTO entities(entity_id, tenant_id, kind, canonical, display, metadata_json) "
            "VALUES (?,?,?,?,?,?);",
            (eid, "local", kind, canonical, display, "{}"),
        )

    edges = 0

    def edge(subject, obj):
        nonlocal edges
        db.execute(
            "INSERT INTO relationships(relationship_id, tenant_id, subject_id, predicate, object_id, "
            "support, weight, evidence_json, metadata_json) VALUES (?,?,?,?,?,?,?,?,?);",
            (f"r{edges:03d}", "local", subject, "code:calls", obj, "supports", 0.9, "{}", "{}"),
        )
        edges += 1

    for t in range(TRIANGLES):
        ids = [f"t{t}n{i}" for i in range(3)]
        for i, eid in enumerate(ids):
            entity(eid, KINDS[(t * 3 + i) % len(KINDS)], f"pkg{t}::sym{i}", f"tri{t}sym{i}")
        for a, b in ((0, 1), (1, 2), (2, 0)):
            edge(ids[a], ids[b])
    entity("hub", "code:module", "hubpkg::hub", "hubnode")
    for leaf in range(HUB_LEAVES):
        entity(f"leaf{leaf}", "code:function", f"hubpkg::leaf{leaf}", f"leafnode{leaf}")
        edge("hub", f"leaf{leaf}")
    entity("zlone", "entity", "lonely::alone", "lonelynode")


def _render(tmp_path, name="g.db"):
    with open_database(tmp_path / name) as db:
        _seed(db)
        g = load_graph(db)
        return render_html(g), g


def _payload(html):
    match = re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S)
    assert match, "the inlined data script is missing"
    # The payload is unicode-escaped for safe inlining; json.loads reverses it.
    return json.loads(match.group(1))


# --------------------------------------------------------------------------
# In-page search
# --------------------------------------------------------------------------


def test_search_control_exists_and_is_labelled(tmp_path):
    html, _ = _render(tmp_path)
    assert '<input id="q" type="search"' in html
    assert '<label for="q">' in html
    assert 'id="q-clear"' in html


def test_every_node_carries_a_search_index_covering_name_kind_and_community(tmp_path):
    html, g = _render(tmp_path)
    data = _payload(html)
    labels = {n["label"] for n in g.nodes}
    kinds = {n["kind"] for n in g.nodes}
    assert len(data["nodes"]) == len(g.nodes)
    for record in data["nodes"]:
        assert record["q"], "a node has an empty search index, so search could never match it"
        assert record["q"] == record["q"].lower()
        assert record["label"].lower() in record["q"]
    # Each seeded label and each seeded kind is reachable by a search string.
    indexes = [n["q"] for n in data["nodes"]]
    for label in labels:
        assert any(label.lower() in q for q in indexes), f"{label!r} is not searchable"
    for kind in kinds:
        assert any(kind.lower() in q for q in indexes), f"{kind!r} is not searchable"
    # A search index that matched everything would be useless: a specific
    # query must select strictly fewer nodes than the whole graph.
    hits = [q for q in indexes if "leafnode1" in q]
    assert len(hits) == 1


def test_search_filters_and_focuses_are_wired_to_the_control(tmp_path):
    html, _ = _render(tmp_path)
    script = html.split('<script id="data"')[1]
    assert "function applyFilter" in script
    assert "nodes[j].q.indexOf(needle)" in script, "the search index must be what is matched"
    assert "focusNode(firstVisible())" in script, "Enter must focus the first match"
    assert 'search.addEventListener("input"' in script
    assert "function refresh()" in script


# --------------------------------------------------------------------------
# Community legend
# --------------------------------------------------------------------------


def test_community_legend_has_one_toggle_per_community_covering_every_node(tmp_path):
    html, g = _render(tmp_path)
    assignment, groups = community_groups(g.nodes, g.edges)
    assert len(groups) >= 2, "the seeded graph has a real community structure to show"
    buttons = re.findall(r'<button type="button" class="lg" id="lg(\d+)" data-c="(\d+)" aria-pressed="true"', html)
    assert len(buttons) == len(groups)
    assert [int(a) for a, _ in buttons] == list(range(len(groups)))
    # Every node is accounted for by exactly one legend entry.
    assert sum(int(grp["count"]) for grp in groups) == len(g.nodes)
    assert set(assignment) == {n["id"] for n in g.nodes}
    for grp in groups:
        assert f'style="background:{grp["colour"]}"' in html
        assert grp["colour"] in COMMUNITY_COLOURS


def test_legend_toggle_changes_visibility_and_pressed_state(tmp_path):
    html, _ = _render(tmp_path)
    script = html.split('<script id="data"')[1]
    assert 'btn.setAttribute("aria-pressed"' in script
    assert "groupOn[c]=next" in script
    assert "groupOn[m.c]!==false" in script, "a toggled-off community must hide its nodes"


def test_detected_communities_match_the_seeded_structure(tmp_path):
    with open_database(tmp_path / "g.db") as db:
        _seed(db)
        g = load_graph(db)
    assignment, _groups = community_groups(g.nodes, g.edges)
    # Each seeded triangle must be one co-membership set, and no triangle may
    # share a community with the hub component.
    for t in range(TRIANGLES):
        members = {assignment[f"t{t}n{i}"] for i in range(3)}
        assert len(members) == 1, f"triangle {t} was split across communities"
    triangle_groups = {assignment[f"t{t}n0"] for t in range(TRIANGLES)}
    assert len(triangle_groups) == TRIANGLES, "distinct triangles were merged"
    assert assignment["hub"] not in triangle_groups


# --------------------------------------------------------------------------
# Node size scaled by degree
# --------------------------------------------------------------------------


def test_node_radius_is_scaled_by_degree_not_constant(tmp_path):
    html, g = _render(tmp_path)
    data = _payload(html)
    degrees = degree_map(g.nodes, g.edges)
    by_index = {n["i"]: n for n in data["nodes"]}
    radii = {n["r"] for n in data["nodes"]}
    assert len(radii) >= 3, "every node was drawn at (nearly) the same size"

    ids = [n["i"] for n in data["nodes"]]
    assert len(ids) == len(set(ids))
    # Radius must be a monotone non-decreasing function of degree, checked over
    # every pair rather than by relying on sort stability.
    index_to_id = {i: node["id"] for i, node in enumerate(g.nodes)}
    pairs = [(degrees[index_to_id[n["i"]]], n["r"]) for n in by_index.values()]
    for deg_a, rad_a in pairs:
        for deg_b, rad_b in pairs:
            if deg_a < deg_b:
                assert rad_a <= rad_b, "a lower-degree node was drawn larger"
            if deg_a == deg_b:
                assert rad_a == rad_b, "equal-degree nodes were drawn at different sizes"

    max_degree = max(degrees.values())
    hub_radius = radius_for_degree(max_degree, max_degree)
    leaf_radius = radius_for_degree(1, max_degree)
    assert hub_radius == MAX_NODE_RADIUS
    assert leaf_radius > MIN_NODE_RADIUS
    assert hub_radius > leaf_radius * 1.5


def test_isolated_node_gets_the_minimum_radius(tmp_path):
    _html, g = _render(tmp_path)
    degrees = degree_map(g.nodes, g.edges)
    assert degrees["zlone"] == 0
    assert radius_for_degree(0, max(degrees.values())) == MIN_NODE_RADIUS


def test_drawn_marks_use_the_scaled_radius(tmp_path):
    """The radius in the data and the radius actually drawn must agree."""
    html, _ = _render(tmp_path)
    data = _payload(html)
    rings = {float(r) for r in re.findall(r'<circle class="ring" cx="[-\d.]+" cy="[-\d.]+" r="([\d.]+)"', html)}
    expected = {round(float(n["r"]) + 4.0, 2) for n in data["nodes"]}
    assert rings == expected
    assert len(rings) >= 3


# --------------------------------------------------------------------------
# Force-directed layout, computed in Python at export time
# --------------------------------------------------------------------------


def test_the_exported_coordinates_come_from_the_python_layout(tmp_path):
    """Every drawn position is the Python layout's output, not something else.

    This is what ties the exported file to the layout the other tests probe: a
    viewer that placed nodes any other way, in Python or in the browser, would
    disagree here.
    """
    html, g = _render(tmp_path)
    data = _payload(html)
    expected = layout_positions(g.nodes, g.edges, width=data["width"], height=data["height"])
    ids = [n["id"] for n in g.nodes]
    for record in data["nodes"]:
        assert (record["x"], record["y"]) == expected[ids[record["i"]]]


def test_the_layout_responds_to_the_edges(tmp_path):
    """A layout that ignored the edges would not be force-directed at all.

    The same nodes are laid out twice, once with the seeded edges and once with
    none. A placement driven only by node identity (a circle, a grid, a hash)
    gives the same coordinates both times; a force-directed one cannot.
    """
    _html, g = _render(tmp_path)
    with_edges = layout_positions(g.nodes, g.edges, width=1100, height=720)
    without_edges = layout_positions(g.nodes, [], width=1100, height=720)
    assert with_edges.keys() == without_edges.keys()
    moved = [nid for nid in with_edges if with_edges[nid] != without_edges[nid]]
    assert len(moved) > len(with_edges) / 2, "the edges barely moved anything, so nothing attracts"

    def mean_linked(pos):
        pairs = [math.dist(pos[e["source"]], pos[e["target"]]) for e in g.edges]
        return sum(pairs) / len(pairs)

    assert mean_linked(with_edges) < mean_linked(without_edges), (
        "linked nodes are no closer with the edges present than without them"
    )


def test_layout_is_force_directed_connected_nodes_sit_closer(tmp_path):
    html, _ = _render(tmp_path)
    data = _payload(html)
    pos = {n["i"]: (n["x"], n["y"]) for n in data["nodes"]}
    linked = [
        math.dist(pos[e["s"]], pos[e["t"]])
        for e in data["edges"]
    ]
    every_pair = [
        math.dist(pos[a], pos[b])
        for a in pos
        for b in pos
        if a < b
    ]
    assert linked, "the seeded graph has edges"
    mean_linked = sum(linked) / len(linked)
    mean_all = sum(every_pair) / len(every_pair)
    # A force-directed layout pulls linked nodes together. A random or
    # circular placement would put linked pairs at the average distance.
    assert mean_linked < 0.6 * mean_all, (
        f"linked nodes average {mean_linked:.1f} apart against {mean_all:.1f} for all pairs, "
        "which is not a force-directed layout"
    )


def test_layout_stays_inside_the_canvas(tmp_path):
    html, _ = _render(tmp_path)
    data = _payload(html)
    for n in data["nodes"]:
        assert 0 <= n["x"] <= data["width"]
        assert 0 <= n["y"] <= data["height"]


def test_render_is_byte_identical_for_the_same_graph(tmp_path):
    """Determinism: nothing about the drawing is decided in the browser."""
    first, _ = _render(tmp_path, "one.db")
    second, _ = _render(tmp_path, "two.db")
    assert first == second
    # And rendering the very same GraphData twice is stable too.
    with open_database(tmp_path / "three.db") as db:
        _seed(db)
        g = load_graph(db)
        assert render_html(g) == render_html(g)


def test_no_unseeded_randomness_reaches_the_layout(tmp_path):
    """The same graph inserted in a different row order still draws the same.

    Node order comes from an explicit ORDER BY and the layout is seeded by node
    id, so insertion order cannot change a single coordinate.
    """
    html_a, _ = _render(tmp_path, "a.db")
    with open_database(tmp_path / "b.db") as db:
        # Same graph, entities inserted in reverse.
        rows = []
        for t in range(TRIANGLES):
            for i in range(3):
                rows.append((f"t{t}n{i}", KINDS[(t * 3 + i) % len(KINDS)], f"pkg{t}::sym{i}", f"tri{t}sym{i}"))
        rows.append(("hub", "code:module", "hubpkg::hub", "hubnode"))
        for leaf in range(HUB_LEAVES):
            rows.append((f"leaf{leaf}", "code:function", f"hubpkg::leaf{leaf}", f"leafnode{leaf}"))
        rows.append(("zlone", "entity", "lonely::alone", "lonelynode"))
        for eid, kind, canonical, display in reversed(rows):
            db.execute(
                "INSERT INTO entities(entity_id, tenant_id, kind, canonical, display, metadata_json) "
                "VALUES (?,?,?,?,?,?);",
                (eid, "local", kind, canonical, display, "{}"),
            )
        edges = 0
        for t in range(TRIANGLES):
            ids = [f"t{t}n{i}" for i in range(3)]
            for a, b in ((0, 1), (1, 2), (2, 0)):
                db.execute(
                    "INSERT INTO relationships(relationship_id, tenant_id, subject_id, predicate, "
                    "object_id, support, weight, evidence_json, metadata_json) VALUES (?,?,?,?,?,?,?,?,?);",
                    (f"r{edges:03d}", "local", ids[a], "code:calls", ids[b], "supports", 0.9, "{}", "{}"),
                )
                edges += 1
        for leaf in range(HUB_LEAVES):
            db.execute(
                "INSERT INTO relationships(relationship_id, tenant_id, subject_id, predicate, "
                "object_id, support, weight, evidence_json, metadata_json) VALUES (?,?,?,?,?,?,?,?,?);",
                (f"r{edges:03d}", "local", "hub", "code:calls", f"leaf{leaf}", "supports", 0.9, "{}", "{}"),
            )
            edges += 1
        html_b = render_html(load_graph(db))
    assert html_a == html_b


def test_the_page_stays_self_contained_after_the_interaction_work(tmp_path):
    html, _ = _render(tmp_path)
    for forbidden in ("http://", "https://", "src=", "<link", "@import", "url(", "fetch("):
        assert forbidden not in html, f"the viewer now references something external: {forbidden!r}"

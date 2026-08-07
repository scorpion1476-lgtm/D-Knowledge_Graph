"""Mnemosyne community detection: correctness, determinism, and DB integration."""

from __future__ import annotations

from dkg.graph.community import communities_from_db, detect_communities


def test_recovers_two_planted_cliques():
    nodes = [f"A{i}" for i in range(4)] + [f"B{i}" for i in range(4)]
    edges = []
    for grp in ("A", "B"):
        for i in range(4):
            for j in range(i + 1, 4):
                edges.append((f"{grp}{i}", f"{grp}{j}", 1.0))
    edges.append(("A0", "B0", 1.0))  # single bridge
    res = detect_communities(nodes, edges)
    assert res["num_communities"] == 2
    assert res["modularity"] > 0.3
    a = {res["assignment"][f"A{i}"] for i in range(4)}
    b = {res["assignment"][f"B{i}"] for i in range(4)}
    assert len(a) == 1 and len(b) == 1 and a != b


def test_resolution_increases_community_count():
    # Two cliques joined by a bridge; a higher resolution splits more finely.
    nodes = [f"A{i}" for i in range(6)] + [f"B{i}" for i in range(6)]
    edges = []
    for grp in ("A", "B"):
        for i in range(6):
            for j in range(i + 1, 6):
                edges.append((f"{grp}{i}", f"{grp}{j}", 1.0))
    edges.append(("A0", "B0", 1.0))
    low = detect_communities(nodes, edges, resolution=1.0)["num_communities"]
    high = detect_communities(nodes, edges, resolution=4.0)["num_communities"]
    assert high >= low


def test_deterministic():
    nodes = [f"A{i}" for i in range(4)] + [f"B{i}" for i in range(4)]
    edges = [("A0", "A1", 1.0), ("A1", "A2", 1.0), ("A2", "A3", 1.0), ("A3", "A0", 1.0),
             ("B0", "B1", 1.0), ("B1", "B2", 1.0), ("B2", "B3", 1.0), ("B3", "B0", 1.0),
             ("A0", "B0", 1.0)]
    assert detect_communities(nodes, edges) == detect_communities(nodes, edges)


def test_empty_and_single():
    assert detect_communities([], [])["num_communities"] == 0
    assert detect_communities(["x"], [])["num_communities"] == 1


def _add_entity(db, eid):
    db.execute(
        "INSERT OR IGNORE INTO entities(entity_id, tenant_id, kind, canonical, display) VALUES (?,?,?,?,?);",
        (eid, "local", "other", eid.lower(), eid),
    )


def _add_edge(db, i, s, o, w):
    db.execute(
        "INSERT INTO relationships(relationship_id, tenant_id, subject_id, predicate, object_id, weight) "
        "VALUES (?,?,?,?,?,?);",
        (f"rel_{i}", "local", s, "relates_to", o, w),
    )


def test_communities_from_db(db):
    for grp in ("A", "B"):
        for i in range(4):
            _add_entity(db, f"{grp}{i}")
    idx = 0
    for grp in ("A", "B"):
        for i in range(4):
            for j in range(i + 1, 4):
                _add_edge(db, idx, f"{grp}{i}", f"{grp}{j}", 1.0)
                idx += 1
    _add_edge(db, idx, "A0", "B0", 1.0)

    result = communities_from_db(db, resolution=1.0)
    assert result["algorithm"] == "mnemosyne"
    assert result["method"] == "modularity-optimization"
    assert result["num_communities"] == 2
    assert result["modularity"] > 0.3
    assert 0.0 <= result["coverage"] <= 1.0
    # Members carry display names and the two communities partition the 8 nodes.
    sizes = sorted(c["size"] for c in result["communities"])
    assert sizes == [4, 4]


def test_communities_from_empty_graph(db):
    result = communities_from_db(db)
    assert result["num_communities"] == 0
    assert result["communities"] == []

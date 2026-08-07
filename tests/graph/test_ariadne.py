"""Ariadne refinement detector: refinement, semantic weighting, and fallback.

The embedding-dependent tests skip with an honest reason when the model is not
pre-staged. The structural and fallback tests always run.
"""

from __future__ import annotations

import sys

import pytest

from dkg.adapters.embedding import Model2VecEmbeddingAdapter
from dkg.ariadne import detect_communities_ariadne
from dkg.mcp.tools import build_read_registry

_EMB_OK, _EMB_WHY = Model2VecEmbeddingAdapter().available()
requires_embeddings = pytest.mark.skipif(not _EMB_OK, reason=f"embedding model unavailable: {_EMB_WHY}")


def _entity(db, eid, display=None):
    db.execute(
        "INSERT OR IGNORE INTO entities(entity_id,tenant_id,kind,canonical,display) VALUES(?,?,?,?,?);",
        (eid, "local", "other", (display or eid).lower(), display or eid),
    )


def _edge(db, i, s, o, w=1.0):
    db.execute(
        "INSERT INTO relationships(relationship_id,tenant_id,subject_id,predicate,object_id,weight)"
        " VALUES(?,?,?,?,?,?);",
        (f"rel_{i}", "local", s, "relates_to", o, w),
    )


def _two_cliques(db):
    for grp in ("A", "B"):
        for i in range(4):
            _entity(db, f"{grp}{i}")
    idx = 0
    for grp in ("A", "B"):
        for i in range(4):
            for j in range(i + 1, 4):
                _edge(db, idx, f"{grp}{i}", f"{grp}{j}")
                idx += 1
    _edge(db, idx, "A0", "B0")


def _members_connected(community, edge_pairs):
    """Every returned community must be internally connected in the structural graph."""
    ids = [m["entity_id"] for m in community["members"]]
    if len(ids) <= 1:
        return True
    idset = set(ids)
    adj = {n: set() for n in ids}
    for u, v in edge_pairs:
        if u in idset and v in idset:
            adj[u].add(v)
            adj[v].add(u)
    seen = {ids[0]}
    stack = [ids[0]]
    while stack:
        x = stack.pop()
        for y in adj[x]:
            if y not in seen:
                seen.add(y)
                stack.append(y)
    return seen == idset


def test_ariadne_structural_recovers_planted(db):
    _two_cliques(db)
    result = detect_communities_ariadne(db, use_embeddings=False)
    assert result["algorithm"] == "ariadne"
    assert result["method"] == "modularity-optimization-with-refinement"
    assert result["num_communities"] == 2
    assert result["modularity"] > 0.3


def test_ariadne_communities_are_internally_connected(db):
    _two_cliques(db)
    result = detect_communities_ariadne(db, use_embeddings=False)
    edge_pairs = [("A0", "A1"), ("A0", "A2"), ("A0", "A3"), ("A1", "A2"), ("A1", "A3"), ("A2", "A3"),
                  ("B0", "B1"), ("B0", "B2"), ("B0", "B3"), ("B1", "B2"), ("B1", "B3"), ("B2", "B3"),
                  ("A0", "B0")]
    for c in result["communities"]:
        assert _members_connected(c, edge_pairs), c


def test_ariadne_empty_graph(db):
    result = detect_communities_ariadne(db)
    assert result["num_communities"] == 0
    assert result["communities"] == []


@requires_embeddings
def test_ariadne_semantic_weighting_recovers_topics(db):
    # A symmetric ring over two topics: structure alone cannot separate them, but
    # semantic edge weighting can.
    ents = {
        "db1": "database query planner", "db2": "sql index tuning", "db3": "query execution engine",
        "as1": "red giant star", "as2": "white dwarf remnant", "as3": "stellar core fusion",
    }
    for eid, disp in ents.items():
        _entity(db, eid, disp)
    ring = [("db1", "db2"), ("db2", "db3"), ("db3", "as1"), ("as1", "as2"), ("as2", "as3"), ("as3", "db1")]
    for i, (s, o) in enumerate(ring):
        _edge(db, i, s, o)
    result = detect_communities_ariadne(db, use_embeddings=True, label=True)
    assert result["embeddings_used"] is True
    assert result["num_communities"] == 2
    groups = [{m["entity_id"] for m in c["members"]} for c in result["communities"]]
    assert {"db1", "db2", "db3"} in groups
    assert {"as1", "as2", "as3"} in groups
    assert all("label" in c for c in result["communities"])


def test_mcp_community_ariadne_falls_back_when_module_absent(db, monkeypatch):
    _two_cliques(db)
    # Simulate the Ariadne module being absent. It ships in the wheel, so this
    # is capability detection against a broken install, not against a build
    # that leaves it out: nothing is excluded from the build.
    monkeypatch.setitem(sys.modules, "dkg.ariadne", None)
    reg = build_read_registry(db)
    out = reg.call("dkg.graph.community", {"detector": "ariadne"})
    assert out["algorithm"] == "mnemosyne"
    assert "fallback" in out
    assert out["num_communities"] == 2

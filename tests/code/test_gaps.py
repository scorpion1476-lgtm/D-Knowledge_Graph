"""Structural knowledge-gap analysis: isolated symbols, untested hotspots, thin communities.

Ingestion is gated on tree-sitter (the 'code' extra) and skips honestly when it is
absent. ``dkg.code.gaps`` itself needs no parser, so it is imported unconditionally
and the empty-graph case is still exercised in a core-only environment.
"""

from __future__ import annotations

import pytest

from dkg.code.gaps import knowledge_gaps

try:
    import tree_sitter  # noqa: F401

    _TS = True
except Exception:
    _TS = False

requires_ts = pytest.mark.skipif(not _TS, reason="tree-sitter not installed (the 'code' extra)")

if _TS:
    from dkg.code.analysis import load_code_graph
    from dkg.code.graph import write_code_graph
    from dkg.code.parser import parse_source


def _ingest(db, files):
    parsed = [parse_source(path, text, language=lang) for path, text, lang in files]
    texts = {path: text for path, text, _lang in files}
    write_code_graph(db, parsed, texts, source_uri="test://gaps")


def _entity_id(db, canonical):
    row = db.fetchone(
        "SELECT entity_id FROM entities WHERE tenant_id=? AND canonical=?;",
        ("local", canonical),
    )
    assert row is not None, canonical
    return row["entity_id"]


def _add_call_edge(db, caller_canonical, callee_canonical):
    """Insert one code:calls edge the resolver cannot produce on its own.

    ``resolve_edges`` only ever points a call edge at a function or method, so a
    ``code:test`` node can never accumulate inbound call pressure through normal
    ingestion. Building that shape by hand is the only way to exercise the guard
    that keeps a test out of the untested-hotspot list.
    """
    subject = _entity_id(db, caller_canonical)
    obj = _entity_id(db, callee_canonical)
    db.execute(
        "INSERT INTO relationships(relationship_id, tenant_id, subject_id, predicate, object_id, weight) VALUES (?,?,?,?,?,?);",
        (f"rel-synthetic-{subject}-{obj}", "local", subject, "code:calls", obj, 0.9),
    )


# core is called by three peers; solo is attached to nothing.
HOT = (
    "hot.py",
    "def core():\n    return 1\n\n"
    "def alpha():\n    return core()\n\n"
    "def beta():\n    return core()\n\n"
    "def gamma():\n    return core()\n\n"
    "def solo():\n    return 0\n",
    "python",
)

# A file whose stem contains "test": every function in it parses as code:test,
# and its call to core produces the code:tested_by edge back to core.
HOT_TESTS = (
    "test_hot.py",
    "def test_core():\n    return core()\n",
    "python",
)

# Four functions in a line: 4 nodes, 3 internal pairs, density 3/6 = 0.5.
CHAIN = (
    "chain.py",
    "def n1():\n    return n2()\n\n"
    "def n2():\n    return n3()\n\n"
    "def n3():\n    return n4()\n\n"
    "def n4():\n    return 0\n",
    "python",
)

# Five functions each referencing every other: 5 nodes, all 10 pairs, density 1.0.
CLIQUE = (
    "clique.py",
    "def c1():\n    return c2() + c3() + c4() + c5()\n\n"
    "def c2():\n    return c3() + c4() + c5()\n\n"
    "def c3():\n    return c4() + c5()\n\n"
    "def c4():\n    return c5()\n\n"
    "def c5():\n    return 0\n",
    "python",
)


@requires_ts
def test_unreferenced_definition_is_isolated_and_modules_are_not(db):
    _ingest(db, [HOT])
    result = knowledge_gaps(db)
    canonicals = [row["canonical"] for row in result["isolated"]]
    # solo neither calls nor is called; every other function sits on a call edge.
    assert canonicals == ["hot.py::solo"]
    # the file node is unattached too, but a module is not a definition gap
    assert "hot.py" not in canonicals
    assert result["summary"]["isolated_count"] == 1
    assert result["isolated"][0]["kind"] == "code:function"
    assert result["isolated"][0]["path"] == "hot.py"
    assert result["isolated"][0]["language"] == "python"


@requires_ts
def test_called_symbol_with_no_test_is_an_untested_hotspot(db):
    _ingest(db, [HOT])
    result = knowledge_gaps(db)
    hotspots = {row["canonical"]: row for row in result["untested_hotspots"]}
    # Observed positive inbound counts are [3] (core only), so the upper-quartile
    # cut is 3 and core is the single symbol at or above it.
    assert result["thresholds"]["inbound_calls_min"] == 3
    assert result["thresholds"]["inbound_observed_symbols"] == 1
    assert list(hotspots) == ["hot.py::core"]
    assert hotspots["hot.py::core"]["inbound_calls"] == 3
    assert hotspots["hot.py::core"]["callers"] == ["hot.py::alpha", "hot.py::beta", "hot.py::gamma"]
    # the callers themselves carry no inbound pressure
    assert "hot.py::alpha" not in hotspots


@requires_ts
def test_a_test_edge_clears_the_hotspot_and_raises_coverage(db):
    _ingest(db, [HOT])
    before = knowledge_gaps(db)
    assert [r["canonical"] for r in before["untested_hotspots"]] == ["hot.py::core"]
    assert before["summary"]["tested_symbols"] == 0
    assert before["summary"]["tested_symbol_fraction"] == 0.0

    # Reference resolution only sees the files handed to one ingest call, so the
    # test file has to arrive alongside the code it exercises for the
    # code:tested_by edge to exist at all.
    _ingest(db, [HOT, HOT_TESTS])
    after = knowledge_gaps(db)
    # core now carries a code:tested_by edge, so it is no longer a gap
    assert [r["canonical"] for r in after["untested_hotspots"]] == []
    assert after["summary"]["tested_symbols"] == 1
    # five testable symbols (test_core is a test and cannot test itself): 1/5
    assert after["summary"]["testable_symbols"] == 5
    assert after["summary"]["test_symbols"] == 1
    assert after["summary"]["tested_symbol_fraction"] == 0.2
    assert after["summary"]["tested_symbol_fraction"] > before["summary"]["tested_symbol_fraction"]


@requires_ts
def test_a_test_symbol_is_never_reported_as_an_untested_hotspot(db):
    _ingest(db, [HOT, HOT_TESTS])
    # Give the test node four inbound callers: as much pressure as core has, and
    # at the derived cut, so only the is_test guard can keep it out.
    for caller in ("hot.py::alpha", "hot.py::beta", "hot.py::gamma", "hot.py::solo"):
        _add_call_edge(db, caller, "test_hot.py::test_core")

    view = load_code_graph(db)
    ids = {n.canonical: n.entity_id for n in view.nodes.values()}
    assert view.nodes[ids["test_hot.py::test_core"]].kind == "code:test"
    assert view.in_degree(ids["test_hot.py::test_core"], ("code:calls",)) == 4
    assert view.in_degree(ids["hot.py::core"], ("code:calls",)) == 4

    result = knowledge_gaps(db)
    reported = {row["canonical"] for row in result["untested_hotspots"]}
    assert "test_hot.py::test_core" not in reported
    assert all(row["kind"] != "code:test" for row in result["untested_hotspots"])
    # core is tested, the test node is excluded by kind: nothing is left
    assert result["untested_hotspots"] == []


@requires_ts
def test_thin_community_reported_and_dense_one_is_not(db):
    _ingest(db, [CHAIN, CLIQUE])
    result = knowledge_gaps(db)
    # Two communities have two or more members: the 4-node chain and the 5-node
    # clique. Median size is 4 and median density 0.5, so the chain is at or
    # below both cuts and the clique is above both.
    assert result["summary"]["communities_analyzed"] == 2
    assert result["thresholds"]["community_size_max"] == 4
    assert result["thresholds"]["community_density_max"] == 0.5

    assert len(result["thin_communities"]) == 1
    thin = result["thin_communities"][0]
    assert thin["size"] == 4
    assert thin["internal_edges"] == 3
    assert thin["density"] == 0.5
    assert thin["members"] == ["chain.py::n1", "chain.py::n2", "chain.py::n3", "chain.py::n4"]
    assert thin["members_truncated"] is False
    # the dense clique is nowhere in the thin set
    reported_members = {m for c in result["thin_communities"] for m in c["members"]}
    assert not any(m.startswith("clique.py::") for m in reported_members)
    # singleton communities (the two module nodes) are not restated as thin
    assert result["summary"]["community_count"] == 4


@requires_ts
def test_output_is_deterministic_across_runs(db):
    _ingest(db, [HOT, HOT_TESTS, CHAIN, CLIQUE])
    first = knowledge_gaps(db)
    second = knowledge_gaps(db)
    assert first == second
    # and stable under a fresh load of the same store
    assert knowledge_gaps(db) == first
    for row in first["isolated"]:
        assert isinstance(row["canonical"], str)
    for community in first["thin_communities"]:
        assert community["members"] == sorted(community["members"])


@requires_ts
def test_graph_with_no_test_symbols_reports_zero_coverage(db):
    _ingest(db, [CHAIN, CLIQUE])
    result = knowledge_gaps(db)
    assert result["summary"]["test_symbols"] == 0
    assert result["summary"]["tested_symbols"] == 0
    assert result["summary"]["tested_symbol_fraction"] == 0.0
    assert result["summary"]["testable_symbols"] == 9
    assert result["truncated"] is False


def test_empty_graph_returns_without_raising(db):
    result = knowledge_gaps(db)
    assert result["isolated"] == []
    assert result["untested_hotspots"] == []
    assert result["thin_communities"] == []
    assert result["summary"]["total_symbols"] == 0
    assert result["summary"]["testable_symbols"] == 0
    # no denominator, so coverage is reported as zero rather than as complete
    assert result["summary"]["tested_symbol_fraction"] == 0.0
    assert result["summary"]["community_count"] == 0
    assert result["truncated"] is False
    assert result["why"]["predicates"] == ["code:calls", "code:imports", "code:inherits"]


def test_why_block_states_the_advisory_structural_limit(db):
    why = knowledge_gaps(db)["why"]
    assert "advisory" in why["note"]
    assert "structural" in why["note"]
    # the honest claim: an absent test edge, not a proven absence of testing
    assert "not that the symbol is" in why["untested_hotspots"]
    assert why["community_method"] == "modularity optimization"

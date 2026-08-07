"""Unexpected-coupling scoring: named signals, derived thresholds, determinism.

Every corpus here is small enough that the expected answer is worked out by hand
in the test's own comments, so a failure points at the implementation rather than
at an opaque fixture.

Gated on tree-sitter (the 'code' extra); skips honestly when absent. The
degenerate-graph cases that need no parsing are not gated, because the scorer
itself carries no optional dependency and must hold up in a bare core install.
"""

from __future__ import annotations

import pytest

from dkg.code.coupling import (
    SIGNAL_CROSS_COMMUNITY,
    SIGNAL_CROSS_LANGUAGE,
    SIGNAL_PERIPHERY_TO_HUB,
    SIGNAL_WEIGHTS,
    unexpected_coupling,
)

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
    write_code_graph(db, parsed, texts, source_uri="test://coupling")


def _pairs(result):
    return [(c["from"], c["to"]) for c in result["couplings"]]


def _named(coupling):
    return [s["name"] for s in coupling["signals"]]


def _find(result, frm, to):
    for c in result["couplings"]:
        if c["from"] == frm and c["to"] == to:
            return c
    return None


# Two complete four-function clusters joined by exactly one call, a1 -> b1.
# Undirected degrees on calls: a1 and b1 have 4 (three siblings plus the bridge),
# every other function has 3. The module nodes have no structural edge at all.
CLUSTER_A = (
    "alpha.py",
    "def a4():\n    return 4\n\n"
    "def a3():\n    return a4()\n\n"
    "def a2():\n    return a3() + a4()\n\n"
    "def a1():\n    return a2() + a3() + a4() + b1()\n",
    "python",
)
CLUSTER_B = (
    "beta.py",
    "def b4():\n    return 40\n\n"
    "def b3():\n    return b4()\n\n"
    "def b2():\n    return b3() + b4()\n\n"
    "def b1():\n    return b2() + b3() + b4()\n",
    "python",
)
TWO_CLUSTERS = [CLUSTER_A, CLUSTER_B]

# A Python and a JavaScript function share the short name "handler", so the
# name-based resolver emits both as candidates for the JavaScript call and one of
# the two edges crosses the language boundary.
CROSS_LANGUAGE = [
    ("svc.py", "def handler():\n    return 1\n", "python"),
    ("svc.js", "function handler() { return 2; }\nfunction dispatch() { return handler(); }\n", "javascript"),
]

# One function called by nine others: a pure star. Nine tenths of the connected
# degrees are 1, so the hub quantile lands on the periphery and the hub band has
# to be raised to the next observed degree, 9.
_STAR_CALLERS = "".join(f"def c{i}():\n    return hub()\n\n" for i in range(1, 9))
STAR = ("hub.py", "def hub():\n    return 1\n\n" + _STAR_CALLERS + "def leaf():\n    return hub()\n", "python")

# A four-function call cycle: every connected node has degree exactly 2, so the
# graph offers no periphery and no hub to separate.
UNIFORM_DEGREE = (
    "cyc.py",
    "def f1():\n    return f2()\n\n"
    "def f2():\n    return f3()\n\n"
    "def f3():\n    return f4()\n\n"
    "def f4():\n    return f1()\n",
    "python",
)

SOLO = ("solo.py", "def only():\n    return 1\n", "python")


@requires_ts
def test_bridge_between_clusters_fires_cross_community_and_outranks_the_rest(db):
    _ingest(db, TWO_CLUSTERS)
    result = unexpected_coupling(db, limit=50)

    top = result["couplings"][0]
    assert (top["from"], top["to"]) == ("alpha.py::a1", "beta.py::b1")
    assert _named(top) == [SIGNAL_CROSS_COMMUNITY]
    assert top["score"] == pytest.approx(SIGNAL_WEIGHTS[SIGNAL_CROSS_COMMUNITY])
    assert top["from_community"] != top["to_community"]
    # The bridge is the only edge that leaves a cluster; nothing else may claim it.
    assert [c for c in result["couplings"] if SIGNAL_CROSS_COMMUNITY in _named(c)] == [top]
    # It strictly outranks every within-cluster edge that scored at all.
    assert all(top["score"] > c["score"] for c in result["couplings"][1:])


@requires_ts
def test_cross_language_edge_fires_and_reports_both_languages(db):
    _ingest(db, CROSS_LANGUAGE)
    result = unexpected_coupling(db, limit=50)

    crossing = _find(result, "svc.js::dispatch", "svc.py::handler")
    assert crossing is not None
    assert SIGNAL_CROSS_LANGUAGE in _named(crossing)
    assert crossing["from_language"] == "javascript"
    assert crossing["to_language"] == "python"
    # The same-language sibling edge to svc.js::handler must not claim the signal.
    sibling = _find(result, "svc.js::dispatch", "svc.js::handler")
    assert sibling is not None
    assert SIGNAL_CROSS_LANGUAGE not in _named(sibling)
    assert sibling["from_language"] == sibling["to_language"] == "javascript"
    # The crossing outranks its same-language twin purely on the extra signal.
    assert crossing["score"] > sibling["score"]
    detail = next(s["detail"] for s in crossing["signals"] if s["name"] == SIGNAL_CROSS_LANGUAGE)
    assert "name-based" in detail  # the honest caveat travels with the signal


@requires_ts
def test_leaf_into_heavily_called_hub_fires_periphery_to_hub(db):
    _ingest(db, [STAR])
    result = unexpected_coupling(db, limit=50)

    thresholds = result["thresholds"]
    # Nine callers of degree 1 and one hub of degree 9: the raw hub quantile is 1,
    # equal to the peripheral band, so it is raised to the next observed degree.
    assert thresholds["sample_size"] == 10
    assert thresholds["peripheral_degree"] == 1
    assert thresholds["hub_degree"] == 9
    assert thresholds["percentiles"]["degree_at_hub_quantile"] == 1
    assert thresholds["percentiles"]["hub_raised_to_next_observed_degree"] is True
    assert thresholds["degree_spread"] is True

    leaf = _find(result, "hub.py::leaf", "hub.py::hub")
    assert leaf is not None
    # A pure star is one community in one language, so this is the lone signal.
    assert _named(leaf) == [SIGNAL_PERIPHERY_TO_HUB]
    assert leaf["score"] == pytest.approx(SIGNAL_WEIGHTS[SIGNAL_PERIPHERY_TO_HUB])
    assert leaf["from_community"] == leaf["to_community"]
    detail = next(s["detail"] for s in leaf["signals"] if s["name"] == SIGNAL_PERIPHERY_TO_HUB)
    assert "degree 1" in detail and "degree 9" in detail


@requires_ts
def test_edge_with_no_signal_is_absent(db):
    _ingest(db, TWO_CLUSTERS)
    result = unexpected_coupling(db, limit=50)

    # 13 call edges exist. Only 7 score: the bridge, and the three sibling calls
    # out of each of a1 and b1 (degree 4) into a degree-3 sibling. The limit is
    # well above 7, so absence below is absence, not clipping.
    assert result["totals"]["edges"] == 13
    assert result["totals"]["scored_edges"] == 7
    assert len(result["couplings"]) == 7
    # a2 -> a3 sits inside one cluster, one language, degree 3 to degree 3.
    assert _find(result, "alpha.py::a2", "alpha.py::a3") is None
    assert _find(result, "alpha.py::a2", "alpha.py::a4") is None
    assert _find(result, "alpha.py::a3", "alpha.py::a4") is None
    assert _find(result, "beta.py::b2", "beta.py::b3") is None
    assert all(c["signals"] for c in result["couplings"])
    assert all(c["score"] > 0.0 for c in result["couplings"])


@requires_ts
@pytest.mark.parametrize("files", [TWO_CLUSTERS, CROSS_LANGUAGE, [STAR], [UNIFORM_DEGREE]])
def test_scores_are_bounded_and_equal_the_sum_of_their_contributions(db, files):
    _ingest(db, files)
    result = unexpected_coupling(db, limit=1000)

    assert result["couplings"], "each corpus is built to produce at least one flagged edge"
    for c in result["couplings"]:
        assert 0.0 <= c["score"] <= 1.0
        assert c["score"] == pytest.approx(sum(s["contribution"] for s in c["signals"]))
        for s in c["signals"]:
            assert s["name"] in SIGNAL_WEIGHTS
            assert s["contribution"] == pytest.approx(SIGNAL_WEIGHTS[s["name"]])
            assert s["detail"]
        # No signal is credited twice on one edge.
        assert len(_named(c)) == len(set(_named(c)))


@requires_ts
def test_repeated_runs_return_equal_results(db):
    _ingest(db, TWO_CLUSTERS)
    assert unexpected_coupling(db, limit=50) == unexpected_coupling(db, limit=50)


@requires_ts
def test_ties_are_broken_by_name_not_insertion_order(db):
    _ingest(db, [STAR])
    result = unexpected_coupling(db, limit=50)

    # All nine star edges score identically, so ordering is decided entirely by
    # the tie-break. Content-addressed entity ids put the stored edges in an
    # order unrelated to the names, which is what makes this test mean something.
    scores = {c["score"] for c in result["couplings"]}
    assert len(scores) == 1
    stored_order = [(load_code_graph(db).label(e.subject_id)) for e in load_code_graph(db).edges_for()]
    assert stored_order != sorted(stored_order), "fixture no longer exercises the tie-break"

    assert _pairs(result) == sorted(_pairs(result))
    assert [c["from"] for c in result["couplings"]] == [
        "hub.py::c1",
        "hub.py::c2",
        "hub.py::c3",
        "hub.py::c4",
        "hub.py::c5",
        "hub.py::c6",
        "hub.py::c7",
        "hub.py::c8",
        "hub.py::leaf",
    ]


@requires_ts
def test_uniform_degree_graph_reports_no_spread_and_never_fires_the_hub_signal(db):
    _ingest(db, [UNIFORM_DEGREE])
    result = unexpected_coupling(db, limit=50)

    thresholds = result["thresholds"]
    assert thresholds["sample_size"] == 4
    assert thresholds["peripheral_degree"] == thresholds["hub_degree"] == 2
    assert thresholds["degree_spread"] is False
    # With no periphery and no hub to separate, the signal must stay silent
    # rather than fire on every edge because both bands accept every node.
    assert all(SIGNAL_PERIPHERY_TO_HUB not in _named(c) for c in result["couplings"])


@requires_ts
def test_single_node_graph_returns_without_raising(db):
    _ingest(db, [SOLO])
    result = unexpected_coupling(db)

    assert result["couplings"] == []
    assert result["totals"]["edges"] == 0
    assert result["totals"]["scored_edges"] == 0
    assert result["thresholds"]["sample_size"] == 0
    assert result["thresholds"]["degree_spread"] is False


@requires_ts
def test_limit_clips_the_list_but_not_the_totals(db):
    _ingest(db, [STAR])
    result = unexpected_coupling(db, limit=3)

    assert len(result["couplings"]) == 3
    assert result["totals"]["scored_edges"] == 9
    assert [c["from"] for c in result["couplings"]] == ["hub.py::c1", "hub.py::c2", "hub.py::c3"]


@requires_ts
def test_predicate_selection_is_reported_and_honoured(db):
    _ingest(db, TWO_CLUSTERS)
    result = unexpected_coupling(db, predicates=["code:calls"], limit=50)

    assert result["why"]["predicates"] == ["code:calls"]
    assert all(c["predicate"] == "code:calls" for c in result["couplings"])


def test_empty_graph_returns_without_raising(db):
    result = unexpected_coupling(db)

    assert result["couplings"] == []
    assert result["totals"] == {"nodes": 0, "edges": 0, "scored_edges": 0, "communities": 0}
    assert result["thresholds"]["peripheral_degree"] == 0
    assert result["thresholds"]["hub_degree"] == 0
    assert result["thresholds"]["degree_spread"] is False
    assert result["truncated"] is False


def test_weights_are_additive_and_sum_to_one(db):
    result = unexpected_coupling(db)

    assert result["weights"] == dict(SIGNAL_WEIGHTS)
    assert sum(SIGNAL_WEIGHTS.values()) == pytest.approx(1.0)
    assert set(SIGNAL_WEIGHTS) == {SIGNAL_CROSS_COMMUNITY, SIGNAL_CROSS_LANGUAGE, SIGNAL_PERIPHERY_TO_HUB}
    assert all(w > 0.0 for w in SIGNAL_WEIGHTS.values())


def test_why_block_labels_the_output_advisory(db):
    why = unexpected_coupling(db)["why"]

    assert "advisory" in why["note"]
    assert "over-approximate" in why["note"]
    assert why["signals"] == [SIGNAL_CROSS_COMMUNITY, SIGNAL_CROSS_LANGUAGE, SIGNAL_PERIPHERY_TO_HUB]
    assert why["resolution"] == 1.0
    assert "not tuned constants" in why["thresholds_derived_from"]

"""U-16: per-community and per-flow summaries and a per-symbol risk index.

The point of precomputing is that the common answers stop costing a walk over
the whole graph. These tests hold two things: the stored answer AGREES with the
live computation (so the cheap path is not a different answer), and it is read
from the small table rather than recomputed (so it is actually cheaper).
"""

from __future__ import annotations

import pytest

from dkg.code.catalogue import community_summary, list_flows, symbol_risk
from dkg.code.postprocess import run_postprocess

try:
    import tree_sitter  # noqa: F401

    _TS = True
except Exception:
    _TS = False

requires_ts = pytest.mark.skipif(not _TS, reason="tree-sitter not installed (the 'code' extra)")

if _TS:
    from dkg.code.graph import write_code_graph
    from dkg.code.parser import parse_source


FILES = {
    "core.py": "def hub():\n    return 1\n\n\ndef quiet():\n    return 0\n",
    "a.py": "from core import hub\n\n\ndef one():\n    return hub()\n",
    "b.py": "from core import hub\n\n\ndef two():\n    return hub()\n",
    "c.py": "from core import hub\n\n\ndef main():\n    return hub()\n",
}


def _ingest(db):
    parsed = [parse_source(rel, text, language="python") for rel, text in FILES.items()]
    write_code_graph(db, parsed, dict(FILES), source_uri="test://precomputed")


# -- not built yet ------------------------------------------------------------


def test_community_summaries_report_when_nothing_is_precomputed(db):
    result = community_summary(db)

    assert result["communities"] == []
    assert result["source"] == "not precomputed"
    assert "dkg code-postprocess" in result["reason"]


def test_symbol_risk_reports_when_nothing_is_precomputed(db):
    result = symbol_risk(db)

    assert result["symbols"] == []
    assert result["source"] == "not precomputed"


# -- community summaries ------------------------------------------------------


@requires_ts
def test_community_summaries_are_stored_with_their_members_and_density(db):
    _ingest(db)
    run_postprocess(db, level="minimal")

    result = community_summary(db)

    assert result["source"] == "precomputed"
    assert result["communities"]
    for community in result["communities"]:
        assert community["members"] >= 1
        assert 0.0 <= community["density"] <= 1.0
        assert len(community["member_names"]) <= community["members"]
        assert isinstance(community["entry_points"], list)
        assert isinstance(community["file_paths"], list)
    assert "never compare an index across runs" in result["why"].lower()


@requires_ts
def test_a_stored_community_summary_agrees_with_the_live_partition(db):
    """The cheap answer must be the same answer."""
    from dkg.code.analysis import STRUCTURAL_PREDICATES, load_code_graph

    _ingest(db)
    run_postprocess(db, level="minimal")

    view = load_code_graph(db)
    live = view.communities(STRUCTURAL_PREDICATES)
    live_sizes = sorted(
        len([n for n, c in live.items() if c == index]) for index in set(live.values())
    )
    stored_sizes = sorted(c["members"] for c in community_summary(db, limit=1000)["communities"])

    assert stored_sizes == live_sizes


@requires_ts
def test_one_community_can_be_addressed_by_index(db):
    _ingest(db)
    run_postprocess(db, level="minimal")
    everything = community_summary(db, limit=1000)["communities"]
    wanted = everything[0]["community_index"]

    one = community_summary(db, wanted)

    assert len(one["communities"]) == 1
    assert one["communities"][0]["community_index"] == wanted


@requires_ts
def test_the_summary_is_read_from_the_table_not_recomputed(db, monkeypatch):
    """If the reader recomputed, deleting the graph would change the answer."""
    _ingest(db)
    run_postprocess(db, level="minimal")
    before = community_summary(db, limit=1000)["communities"]

    db.execute("DELETE FROM relationships;")
    db.execute("DELETE FROM entities;")

    after = community_summary(db, limit=1000)["communities"]
    assert after == before, "the stored summary survives the graph it came from"
    assert community_summary(db)["current"] is False, "and is honestly reported stale"


# -- symbol risk index --------------------------------------------------------


@requires_ts
def test_the_risk_index_stores_every_symbol_with_its_factors(db):
    _ingest(db)
    run_postprocess(db, level="full")

    result = symbol_risk(db, limit=1000)

    assert result["source"] == "precomputed"
    canonicals = {s["canonical"] for s in result["symbols"]}
    assert "core.py::hub" in canonicals
    for symbol in result["symbols"]:
        assert 0.0 <= symbol["score"] <= 1.0
        assert symbol["level"]
        assert "contributions" in symbol
        assert "raw" in symbol


@requires_ts
def test_a_stored_risk_score_agrees_with_the_live_computation(db):
    from dkg.code.risk import change_risk

    _ingest(db)
    run_postprocess(db, level="full")

    stored = symbol_risk(db, "core.py::hub")["symbols"][0]
    live = change_risk(db, symbols=["core.py::hub"])["symbols"][0]

    assert stored["score"] == live["structural_score"]
    assert stored["level"] == live["level"]


@requires_ts
def test_one_symbol_can_be_addressed_directly(db):
    _ingest(db)
    run_postprocess(db, level="full")

    result = symbol_risk(db, "core.py::hub")

    assert len(result["symbols"]) == 1
    assert result["symbols"][0]["canonical"] == "core.py::hub"


@requires_ts
def test_the_index_is_ordered_by_score_so_the_top_answer_is_the_first_row(db):
    _ingest(db)
    run_postprocess(db, level="full")

    scores = [s["score"] for s in symbol_risk(db, limit=1000)["symbols"]]

    assert scores == sorted(scores, reverse=True)


@requires_ts
def test_the_churn_signal_is_never_precomputed(db):
    _ingest(db)
    report = run_postprocess(db, level="full")

    risk_stage = next(s for s in report["stages"] if s["stage"] == "risk")
    assert "churn signal is never precomputed" in risk_stage["note"]
    stored = symbol_risk(db, "core.py::hub")["symbols"][0]
    assert "churn" not in stored
    assert "combined_score" not in stored


# -- flow summaries -----------------------------------------------------------


@requires_ts
def test_flow_summaries_are_read_from_the_table(db):
    _ingest(db)
    run_postprocess(db, level="standard")

    result = list_flows(db)

    assert result["source"] == "precomputed"
    assert result["flows"]
    for flow in result["flows"]:
        assert flow["steps"] >= 2
        assert flow["files"] >= 1


@requires_ts
def test_every_precomputed_reader_reports_its_source_and_currency(db):
    _ingest(db)
    run_postprocess(db, level="full")

    for result in (community_summary(db), symbol_risk(db), list_flows(db)):
        assert result["source"] == "precomputed"
        assert result["current"] is True
        assert result["computed_at"]
        assert result["graph_revision"]

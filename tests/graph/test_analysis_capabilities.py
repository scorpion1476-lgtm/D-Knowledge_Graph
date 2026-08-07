"""Three-tier confidence, community splitting, flow criticality, traversal, memory.

Each of these has one claim that is easy to assert and easy to break silently,
so each gets a test that would actually catch the break.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dkg.code.capability import grammar_available
from dkg.code.graph import write_code_graph
from dkg.code.model import (
    CONF_DEFINES,
    CONF_NAME_MATCH,
    CONF_RESOLVED,
    CONF_UNRESOLVED,
    FRAMEWORK_PREDICATES,
    PREDICATE_EXPLANATIONS,
    STRUCTURAL_PREDICATES,
    TIER_AMBIGUOUS,
    TIER_EXTRACTED,
    TIER_INFERRED,
    confidence_record,
    confidence_tier,
)
from dkg.code.parser import parse_source
from dkg.context.memory import AnsweredQuestion, ingest_answers, list_answers, write_answer
from dkg.core.db import open_database
from dkg.core.errors import ValidationError
from dkg.graph.split import DEFAULT_OVERSIZE_SHARE, split_oversized

needs_python = pytest.mark.skipif(
    not grammar_available("python"), reason="the python grammar is not installed"
)


# -- three-tier edge confidence -------------------------------------------------


def test_each_tier_matches_the_constant_that_produces_it():
    """The tiers are derived from the confidence constants, not chosen apart."""
    assert confidence_tier(CONF_DEFINES) == TIER_EXTRACTED
    assert confidence_tier(CONF_RESOLVED) == TIER_INFERRED
    assert confidence_tier(CONF_NAME_MATCH) == TIER_AMBIGUOUS
    assert confidence_tier(CONF_UNRESOLVED) == TIER_AMBIGUOUS
    # Values BETWEEN the constants, so this is not an identity check against the
    # same numbers the boundaries are defined from. TIER_EXTRACTED_MIN equals
    # CONF_DEFINES, so testing only the constants proves nothing about the
    # function's behaviour anywhere else on the scale.
    assert confidence_tier(0.999) == TIER_INFERRED
    assert confidence_tier(0.75) == TIER_INFERRED
    # The inferred floor sits just above the name-match band, so a value either
    # side of it must land on the correct side.
    assert confidence_tier(0.61) == TIER_INFERRED
    assert confidence_tier(0.609) == TIER_AMBIGUOUS
    assert confidence_tier(0.60) == TIER_AMBIGUOUS
    assert confidence_tier(0.0) == TIER_AMBIGUOUS
    assert confidence_tier(1.0) == TIER_EXTRACTED


def test_a_missing_confidence_is_ambiguous_not_certain():
    """An edge that lost its weight is exactly the one not to trust."""
    assert confidence_tier(None) == TIER_AMBIGUOUS


def test_the_tier_never_travels_without_its_number_and_reason():
    # A value that is not one of the constants, so the confidence field cannot
    # pass by echoing something the test already had to hand.
    record = confidence_record(0.42)
    assert record["tier"] == TIER_AMBIGUOUS
    assert record["confidence"] == pytest.approx(0.42)
    assert "over-approximate" in record["why"]
    # A missing weight must still produce a usable record, not a crash.
    empty = confidence_record(None)
    assert empty["tier"] == TIER_AMBIGUOUS
    assert empty["confidence"] is None
    assert empty["why"]


def test_tiers_are_ordered_by_confidence():
    ordering = [confidence_tier(w) for w in (1.0, 0.9, 0.6, 0.3)]
    assert ordering == [TIER_EXTRACTED, TIER_INFERRED, TIER_AMBIGUOUS, TIER_AMBIGUOUS]


# -- framework edge vocabulary --------------------------------------------------


def test_framework_predicates_are_distinct_from_the_structural_ones():
    """Flattening a route into a call is what this vocabulary exists to stop."""
    assert not set(FRAMEWORK_PREDICATES) & set(STRUCTURAL_PREDICATES)
    assert "routes_to" in FRAMEWORK_PREDICATES
    assert "renders" in FRAMEWORK_PREDICATES
    assert "relates_to" in FRAMEWORK_PREDICATES


def test_every_predicate_explains_itself():
    for predicate in STRUCTURAL_PREDICATES + FRAMEWORK_PREDICATES:
        assert PREDICATE_EXPLANATIONS.get(predicate), predicate


# -- oversized community splitting ----------------------------------------------


def _two_cliques(size: int = 6) -> tuple[list[str], list[tuple[str, str, float]]]:
    nodes = [f"n{i}" for i in range(size * 2)]
    edges: list[tuple[str, str, float]] = []
    for group in (range(size), range(size, size * 2)):
        members = list(group)
        for i in members:
            for j in members:
                if i < j:
                    edges.append((f"n{i}", f"n{j}", 1.0))
    edges.append(("n0", f"n{size}", 0.1))  # one weak bridge
    return nodes, edges


def test_an_oversized_community_is_split_into_its_real_structure():
    nodes, edges = _two_cliques()
    everything_in_one = {n: 0 for n in nodes}
    result = split_oversized(nodes, edges, everything_in_one, oversize_share=0.25)
    assert result["split"], "a community holding the whole graph must be split"
    sizes = sorted(
        sum(1 for c in result["assignment"].values() if c == cid)
        for cid in set(result["assignment"].values())
    )
    assert sizes == [6, 6]


def test_a_split_that_does_not_help_is_rejected_with_its_numbers():
    """Splitting for its own sake trades one useless answer for several."""
    nodes, edges = _two_cliques()
    everything_in_one = {n: 0 for n in nodes}
    result = split_oversized(nodes, edges, everything_in_one, oversize_share=0.25)
    assert result["rejected"], "further splitting a clique must be refused"
    for entry in result["rejected"]:
        assert entry["reason"]
        if "modularity" in entry["reason"]:
            assert entry["modularity_after"] <= entry["modularity_before"]


def test_the_threshold_is_reported_with_the_node_count_it_resolved_to():
    nodes, edges = _two_cliques()
    result = split_oversized(nodes, edges, {n: 0 for n in nodes}, oversize_share=0.25)
    threshold = result["threshold"]
    assert threshold["oversize_share"] == 0.25
    assert threshold["oversized_above_nodes"] == 3
    assert threshold["derivation"]


def test_a_partition_with_nothing_oversized_is_left_alone():
    nodes, edges = _two_cliques()
    already_split = {n: (0 if int(n[1:]) < 6 else 1) for n in nodes}
    result = split_oversized(nodes, edges, already_split, oversize_share=DEFAULT_OVERSIZE_SHARE)
    assert result["split"] == []
    assert result["assignment"] == already_split


def test_an_empty_partition_is_handled_rather_than_crashing():
    assert split_oversized([], [], {})["split"] == []


# -- flow criticality and bounded traversal ------------------------------------

CODE = {
    "core.py": (
        "def hub(v):\n    return v\n\n"
        "def leaf(v):\n    return v\n"
    ),
    "app.py": (
        "from core import hub, leaf\n\n"
        "def entry(v):\n    return middle(v)\n\n"
        "def middle(v):\n    return hub(v) + leaf(v)\n\n"
        "def other(v):\n    return hub(v)\n\n"
        # Reached by nothing and tested by nothing, so a flow from it exercises
        # the untested branch of the criticality score. Without it every flow in
        # this fixture is tested and the branch is never taken.
        "def untested_entry(v):\n    return other(v)\n"
    ),
    "test_app.py": ("from app import entry\n\ndef test_entry():\n    return entry(1)\n"),
}


@pytest.fixture
def db(tmp_path):
    parsed = [parse_source(name, text) for name, text in CODE.items()]
    with open_database(tmp_path / "g.db") as database:
        write_code_graph(
            database, parsed, CODE, source_uri="code://analysis-test", tenant_id="local"
        )
        yield database


@needs_python
def test_flow_criticality_scores_every_flow_and_shows_its_components(db):
    from dkg.code.criticality import flow_criticality

    result = flow_criticality(db, "app.py::entry", depth=5)
    assert result["found"]
    assert result["flows"]
    for flow in result["flows"]:
        assert set(flow["components"]) == {
            "depth",
            "peak_fan_in",
            "files_touched",
            "mean_edge_confidence",
            "untested",
        }
        # The total must be exactly the sum of its published parts, or the
        # components are decoration rather than an explanation.
        assert flow["criticality"] == pytest.approx(sum(flow["components"].values()), abs=1e-4)
    assert result["weights"]["depth"]
    assert "over-approximate" in result["why"]


@needs_python
def test_flows_are_ordered_by_criticality_and_the_order_is_stable(db):
    from dkg.code.criticality import flow_criticality

    runs = [flow_criticality(db, "app.py::entry", depth=5) for _ in range(3)]
    orders = [[f["path"] for f in r["flows"]] for r in runs]
    assert orders[0] == orders[1] == orders[2]
    scores = [f["criticality"] for f in runs[0]["flows"]]
    assert scores == sorted(scores, reverse=True)


@needs_python
def test_an_untested_path_scores_above_the_same_path_tested(db):
    """The penalty is a bonus, deliberately: untested is riskier.

    An earlier version asserted `0.0 if tested else W_UNTESTED_BONUS` over a
    fixture in which every flow was tested, so the else branch never ran and the
    assertion reduced to `0.0 == 0.0`. It would have passed with the constant set
    to zero, or with the branch deleted. Both branches are now required to occur.
    """
    from dkg.code.criticality import W_UNTESTED_BONUS, flow_criticality

    tested = flow_criticality(db, "app.py::entry", depth=5)["flows"]
    untested = flow_criticality(db, "app.py::untested_entry", depth=5)["flows"]
    assert tested and untested

    assert all(f["tested"] for f in tested), "the tested branch is not exercised"
    assert not any(f["tested"] for f in untested), "the untested branch is not exercised"

    assert all(f["components"]["untested"] == pytest.approx(0.0) for f in tested)
    assert all(
        f["components"]["untested"] == pytest.approx(W_UNTESTED_BONUS) for f in untested
    )
    # And the bonus is not zero, or the two branches would be indistinguishable.
    assert W_UNTESTED_BONUS > 0


@needs_python
def test_traversal_is_bounded_on_depth_and_tokens_and_says_which_bit(db):
    from dkg.code.criticality import traverse

    deep = traverse(db, "app.py::entry", depth=1, token_budget=None)
    assert deep["totals"]["truncated_by_depth"] is True
    assert deep["totals"]["truncated_by_token_budget"] is False

    tight = traverse(db, "app.py::entry", depth=5, token_budget=12)
    assert tight["totals"]["truncated_by_token_budget"] is True
    assert tight["totals"]["tokens_used"] <= 12 + 20  # the seed alone may exceed


@needs_python
def test_breadth_and_depth_orders_both_work_and_stay_bounded(db):
    from dkg.code.criticality import traverse

    for order in ("breadth", "depth"):
        result = traverse(db, "app.py::entry", order=order, depth=3, token_budget=None)
        assert result["found"]
        assert result["nodes"][0]["canonical"] == "app.py::entry"
        assert all(n["distance"] <= 3 for n in result["nodes"])


@needs_python
def test_an_unknown_traversal_order_or_direction_is_refused(db):
    from dkg.code.criticality import traverse

    with pytest.raises(ValidationError, match="unknown order"):
        traverse(db, "app.py::entry", order="sideways")
    with pytest.raises(ValidationError, match="unknown direction"):
        traverse(db, "app.py::entry", direction="upwards")


@needs_python
def test_traversal_edges_carry_their_confidence_tier(db):
    from dkg.code.criticality import traverse

    result = traverse(db, "app.py::entry", depth=2, token_budget=None)
    reached = [n for n in result["nodes"] if n["distance"] > 0]
    assert reached
    for node in reached:
        assert node["confidence"]["tier"] in (TIER_EXTRACTED, TIER_INFERRED, TIER_AMBIGUOUS)


# -- the memory loop -------------------------------------------------------------


def test_an_answer_is_written_as_an_ordinary_markdown_document():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        path = write_answer(
            home,
            AnsweredQuestion(
                question="What calls hub?",
                answer="middle and other call it.",
                sources=["core.py::hub"],
                method="dkg.code.slices",
                graph_revision="rev1",
            ),
            now=datetime(2026, 8, 6, tzinfo=timezone.utc),
        )
        body = path.read_text(encoding="utf-8")
        assert path.suffix == ".md"
        assert "middle and other call it." in body
        assert "core.py::hub" in body
        assert "rev1" in body


def test_a_recorded_answer_says_it_is_not_a_live_one():
    """A cached answer offered as current is worse than no cache."""
    with tempfile.TemporaryDirectory() as tmp:
        path = write_answer(
            Path(tmp), AnsweredQuestion(question="Q?", answer="A.", method="m")
        )
        body = path.read_text(encoding="utf-8")
        assert "RECORDED ANSWER, not a live one" in body
        assert "may have changed since" in body


def test_re_answering_supersedes_rather_than_accumulating():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        first = write_answer(home, AnsweredQuestion(question="What calls hub?", answer="one"))
        # Different spacing and case: the same question.
        second = write_answer(home, AnsweredQuestion(question="  what CALLS   hub? ", answer="two"))
        assert first == second
        assert len(list_answers(home)) == 1
        assert "two" in second.read_text(encoding="utf-8")


def test_an_empty_question_or_answer_is_refused():
    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(ValidationError):
            write_answer(Path(tmp), AnsweredQuestion(question="  ", answer="a"))
        with pytest.raises(ValidationError):
            write_answer(Path(tmp), AnsweredQuestion(question="q", answer="  "))


def test_recorded_answers_ingest_through_the_ordinary_document_path(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    write_answer(
        home,
        AnsweredQuestion(question="What calls hub?", answer="middle and other.", method="m"),
    )
    with open_database(tmp_path / "g.db") as db:
        result = ingest_answers(db, home)
        assert result["ingested"]
        assert result["failed"] == []
        chunks = db.fetchone("SELECT COUNT(*) AS n FROM chunks;")["n"]
        assert chunks > 0
        # Searchable alongside everything else, which is the point of the loop.
        rows = db.fetchall("SELECT text FROM chunks;")
        assert any("middle and other." in r["text"] for r in rows)

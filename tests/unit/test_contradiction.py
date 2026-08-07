import pytest

from dkg.evidence.contradiction import compare_claims


def test_antonym_pair_flagged():
    s = compare_claims("open", "closed")
    assert s.score >= 0.5


def test_negation_flag():
    s = compare_claims("supported by the maintainer", "not supported by the maintainer")
    assert s.score >= 0.3


def test_numeric_mismatch():
    s = compare_claims("released 42 patches", "released 7 patches")
    assert s.score >= 0.5


def test_identical_not_a_contradiction():
    s = compare_claims("same", "same")
    assert s.score == 0.0


# -- comparability ----------------------------------------------------------
# These exercise the grouping rules directly, which is where both real defects
# lived. compare_claims above was never the problem.

from dkg.evidence.contradiction import _identifiers, _topic  # noqa: E402


def test_subject_identifiers_are_taken_from_the_subject_only():
    assert _identifiers("The cache TTL for service 0") == frozenset({"0"})
    assert _identifiers("Service 0") == frozenset({"0"})
    assert _identifiers("The payment gateway") == frozenset()


def test_identifier_bearing_token_is_kept_whole():
    assert _identifiers("The layer_0_gateway handler") == frozenset({"layer_0_gateway"})


def test_object_numbers_are_left_out_of_the_topic_but_subject_numbers_are_not():
    topic = _topic("The cache TTL for service 0", "30 seconds")
    assert "0" in topic
    assert "30" not in topic


def test_paraphrased_subjects_produce_nested_topics():
    a = _topic("The cache TTL for service 0", "30 seconds")
    b = _topic("Service 0", "a cache TTL of 300 seconds, chosen to reduce load")
    assert a <= b


def test_different_attributes_of_one_subject_do_not_nest():
    a = _topic("The error rate for service 0", "5 percent")
    b = _topic("Service 0", "a cache TTL of 300 seconds")
    assert not (a <= b or b <= a)


def test_regular_plural_is_folded_but_double_s_is_not():
    assert "second" in _topic("A", "10 seconds")
    assert "class" in _topic("The class", "small")


# -- bounds and determinism -------------------------------------------------
# Both were found by an adversarial review of the first version of this fix.
# The scan reported a truncation flag that only one of its two routes honoured,
# and it ordered its output by claim id, which descends from the ingest path and
# therefore differs between runs over the same corpus.

import shutil  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

from dkg.core.db import open_database  # noqa: E402
from dkg.evidence.contradiction import scan_contradictions  # noqa: E402
from dkg.ingest.base import ingest_path, ingest_text  # noqa: E402

_CORPUS = Path(__file__).resolve().parents[2] / "tests" / "code" / "corpus" / "large" / "docs"


def _many_claims(db, n):
    for i in range(n):
        ingest_text(db, f"The widget is {i} units.", display_name=f"d{i}")


def test_a_tiny_comparison_budget_truncates_both_routes(db):
    _many_claims(db, 40)
    report = scan_contradictions(db, max_comparisons=1)
    assert report.truncated is True
    assert report.comparisons == 1
    # The point of the bound: the answer is bounded too, not just the counter.
    assert len(report.signals) <= 1


def test_the_signal_count_is_bounded_independently_of_the_comparison_budget(db):
    _many_claims(db, 40)
    report = scan_contradictions(db, max_signals=5)
    assert len(report.signals) == 5
    assert report.truncated is True


def test_an_unbounded_scan_of_a_small_graph_reports_no_truncation(db):
    _many_claims(db, 5)
    report = scan_contradictions(db)
    assert report.truncated is False
    assert report.comparisons > 0


def test_the_same_corpus_produces_the_same_report_from_a_different_ingest_path():
    """Claim ids carry the ingest path, so ordering on them made the published
    artifact differ between runs over identical input."""
    runs = []
    for _ in range(3):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staged = root / "corpus"
            staged.mkdir()
            for path in sorted(_CORPUS.glob("*.md")):
                shutil.copy(path, staged / path.name)
            with open_database(root / "graph.sqlite") as db:
                ingest_path(db, staged, tenant_id="local", audit_path=root / "audit.log")
                report = scan_contradictions(db)
                runs.append(
                    [
                        (s["route"], s["score"], s["reason"], s["left"]["object_text"])
                        for s in report.signals
                    ]
                )
    assert runs[0], "the corpus produced no signals; this test would prove nothing"
    assert runs[0] == runs[1] == runs[2]


# -- unit-aware numeric comparison ------------------------------------------
# Added after a review found "5 seconds" against "5000 milliseconds" reported as
# a numeric disagreement of 5 against 5000. A second review then found the first
# fix had introduced two regressions of its own, both covered below.


@pytest.mark.parametrize(
    ("left", "right", "score", "why"),
    [
        ("30 seconds", "a cache TTL of 300 seconds", 0.7, "same unit, different value"),
        ("10 megabytes", "a maximum upload size of 25 megabytes", 0.7, "size units"),
        ("30 seconds", "5 minutes", 0.7, "different scales of one dimension disagree"),
        ("5 seconds", "5000 milliseconds", 0.0, "the same quantity written two ways"),
        ("1 hour", "60 minutes", 0.0, "the same quantity written two ways"),
        ("10 megabytes", "10 seconds", 0.0, "different dimensions cannot disagree"),
        ("3 nodes", "5 replica nodes", 0.7, "no recognised unit: compare bare numbers"),
        ("released 42 patches", "released 7 patches", 0.7, "no recognised unit"),
        # One side united, one bare. Converting the united side and comparing it
        # against the other's raw number invented conflicts and lost real ones.
        ("5 minutes", "5 nodes", 0.0, "must not convert against a bare number"),
        ("2 hours", "7200 requests", 0.7, "must still compare the numbers as written"),
        # Equal quantities must not short-circuit the other two tests.
        ("enabled for 5 seconds", "disabled for 5000 milliseconds", 0.8, "antonym still tested"),
        ("available after 5 seconds", "not available after 5000 milliseconds", 0.5, "negation still tested"),
        ("true for 2 days", "false for 48 hours", 0.8, "antonym still tested"),
    ],
)
def test_unit_aware_numeric_comparison(left, right, score, why):
    assert compare_claims(left, right).score == score, why


def test_ambiguous_unit_abbreviations_are_not_treated_as_units():
    """'m' could be metres or minutes and 'b' could be bits or bytes. Guessing
    would convert a number by a factor the document never stated."""
    from dkg.evidence.contradiction import _UNITS

    for ambiguous in ("m", "b", "s", "h", "k"):
        assert ambiguous not in _UNITS

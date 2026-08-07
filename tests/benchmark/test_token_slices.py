"""The published token-efficiency numbers must be honest about what they are.

This does not re-run the benchmark: that stages two repositories and ingests
several hundred files, which is a build step rather than a test. What it does is
hold the COMMITTED artifact to the claims made about it, so a number can never
drift away from its caveats, and check the properties that make the measurement
meaningful in the first place:

* every figure was produced by a real tokenizer, on both sides of the comparison,
* the strong baseline (grep and read) is present, not just the naive one,
* a reduction is only quoted for a question the route answered COMPLETELY,
* completeness is judged over the symbols the route actually returned, not over
  incidental text matches,
* the self-measurement limitation of the real-code corpus is published.

A benchmark whose caveats can be deleted without a test failing is a benchmark
whose caveats will eventually be deleted.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "test-evidence" / "token_slices.json"


@pytest.fixture(scope="module")
def evidence() -> dict:
    if not EVIDENCE.exists():
        pytest.skip(
            "test-evidence/token_slices.json is not present; run "
            "scripts/token_slices_benchmark.py to generate it"
        )
    return json.loads(EVIDENCE.read_text(encoding="utf-8"))


def test_the_numbers_came_from_a_real_tokenizer_and_say_which(evidence):
    tokenizer = evidence["tokenizer"]
    assert tokenizer["is_real_tokenizer"] is True, (
        "these are estimator counts and must not be published as token counts"
    )
    assert tokenizer["name"] and tokenizer["name"] != "in-repo-estimator"


def test_both_corpora_are_present_and_one_of_them_is_real_code(evidence):
    labels = [c["label"] for c in evidence["corpora"]]
    assert len(labels) == 2
    assert any("synthetic" in label for label in labels)
    assert any("real code" in label for label in labels)


def test_the_strong_baseline_is_measured_not_only_the_naive_one(evidence):
    """Beating a whole-corpus dump is not the claim worth making."""
    for corpus in evidence["corpora"]:
        for m in corpus["measurements"]:
            if "skipped" in m:
                continue
            assert m["grep_and_read"]["tokens"] > 0
            assert m["naive_whole_corpus"]["tokens"] > 0
            # The strong baseline must actually be strong: far cheaper than naive.
            assert m["grep_and_read"]["tokens"] < m["naive_whole_corpus"]["tokens"]


def test_a_reduction_is_only_quoted_for_a_question_answered_completely(evidence):
    """A saving bought by losing part of the answer must never reach a headline."""
    for corpus in evidence["corpora"]:
        for m in corpus["measurements"]:
            if "skipped" in m:
                continue
            if m["cheapest_complete_answer"] is None:
                assert m["reduction_vs_grep_and_read"] is None
                assert m["reduction_vs_naive"] is None
            else:
                detail = m["cheapest_complete_answer"]["detail"]
                measured = m["graph_slices_by_detail"][detail]
                assert measured["recall_of_required_by_returned_symbol"] >= 1.0


def test_completeness_is_judged_over_symbols_actually_returned(evidence):
    """The stricter of the two recall measures is the one that decides."""
    for corpus in evidence["corpora"]:
        for m in corpus["measurements"]:
            if "skipped" in m:
                continue
            for measured in m["graph_slices_by_detail"].values():
                assert "recall_of_required_by_returned_symbol" in measured
                assert "recall_of_required" in measured


def test_the_summary_counts_agree_with_the_measurements(evidence):
    for corpus in evidence["corpora"]:
        measurements = [m for m in corpus["measurements"] if "skipped" not in m]
        summary = corpus["summary"]
        assert summary["questions"] == len(measurements)
        complete = [m for m in measurements if m["cheapest_complete_answer"] is not None]
        assert summary["questions_answered_completely"] == len(complete)
        wins = [m for m in complete if m["reduction_vs_grep_and_read"] > 0]
        assert summary["questions_cheaper_than_grep_and_read"] == len(wins)


def test_every_question_had_a_non_empty_ground_truth_to_be_scored_against(evidence):
    """A vacuous required set would make recall trivially 1.0."""
    for corpus in evidence["corpora"]:
        for m in corpus["measurements"]:
            if "skipped" in m:
                continue
            assert m["required_symbols"] > 0, f"{m['name']} has no ground truth"


def test_the_caveats_survive_in_the_artifact(evidence):
    caveats = " ".join(evidence["caveats"]).lower()
    assert "not a universal claim" in caveats
    assert "over-approximate" in caveats
    # The self-measurement limitation of using our own source.
    assert "own source" in caveats
    # The honest admission that a stronger baseline is imaginable.
    assert "strongest imaginable" in caveats


def test_the_method_states_that_uncapped_cost_is_what_is_compared(evidence):
    method = evidence["method"].lower()
    assert "uncapped" in method
    assert "cheapest" in method


def test_the_published_reductions_are_real_and_not_rounded_up(evidence):
    """Recompute every quoted reduction from the tokens beside it."""
    for corpus in evidence["corpora"]:
        for m in corpus["measurements"]:
            if "skipped" in m or m["cheapest_complete_answer"] is None:
                continue
            route = m["cheapest_complete_answer"]["tokens"]
            for baseline_key, reduction_key in (
                ("grep_and_read", "reduction_vs_grep_and_read"),
                ("naive_whole_corpus", "reduction_vs_naive"),
            ):
                baseline = m[baseline_key]["tokens"]
                expected = (baseline - route) / baseline
                assert m[reduction_key] == pytest.approx(expected, abs=1e-3), (
                    f"{m['name']} {reduction_key} does not match its own token counts"
                )

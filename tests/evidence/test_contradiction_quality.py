"""Held-out acceptance for the contradiction scanner.

These assertions are the published numbers, and they are deliberately not 1.0.
The corpus contains three real disagreements the scanner does not see and two
false positives it raises, all of them kept in the score.

Recall COULD be 1.0. One token of slack in topic matching catches all three
misses, and it was measured and reverted. The reason is N7, N8 and N9, added by
the review that found it: two different subjects distinguished by a word rather
than a numeral, which the corpus had never contained. With slack of 1 those are
reported as contradictions and precision falls from 0.75 to 0.5294. P9 and N7
are lexically the same shape, so no threshold separates them.

That is why the assertions below pin BOTH numbers and the signal count. If
someone widens the matcher until it starts inventing disagreements, precision
and the signal count move and a test here fails. If someone narrows it until it
stops finding real ones, recall falls and a different test fails. Asserting only
one of them is how a matcher gets tuned into uselessness while looking better.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


@pytest.fixture(scope="module")
def result() -> dict:
    from contradiction_quality import run

    return run()


def test_recall_on_real_disagreements_is_six_of_nine(result: dict) -> None:
    assert result["real_disagreements"] == 9
    assert result["true_positives"] == 6
    assert result["recall"] == pytest.approx(0.6667, abs=5e-4)


def test_precision_is_three_quarters_with_two_known_false_positives(result: dict) -> None:
    """The signal count is pinned, not just the ratio.

    A matcher can raise recall by reporting more, and a precision figure alone
    can survive that if the true positives rise with the false ones. Pinning how
    many signals come back at all is what makes this checkable.
    """
    assert result["signals_returned"] == 8
    assert result["false_positive_signals"] == 2
    assert result["precision"] == pytest.approx(0.75, abs=5e-4)


def test_the_three_misses_are_the_documented_ones(result: dict) -> None:
    assert result["missed_real_disagreements"] == [
        "P6-retention-verb-outside-pattern-set",
        "P8-adjective-drift",
        "P9-attribute-noun-dropped",
    ]


def test_two_different_subjects_told_apart_by_a_word_stay_silent(result: dict) -> None:
    """The regression guard for the relaxation that was tried and reverted.

    Before N7, N8 and N9 existed, widening topic matching measured as free: the
    corpus had no case where two different subjects are distinguished by a word
    rather than a numeral, so nothing could see the cost. These three are that
    case. If a future change makes any of them fire, it has bought recall with
    precision and this says so.
    """
    fired = sorted(
        c["id"]
        for c in result["per_case"]
        if c["detected"] and c["id"].startswith(("N7", "N8", "N9"))
    )
    assert fired == [], (
        f"a relaxed matcher now reports {fired} as contradictions; these are "
        "different subjects, not disagreements"
    )


def test_every_case_agrees_with_its_recorded_expectation(result: dict) -> None:
    disagreeing = [c["id"] for c in result["per_case"] if not c["agrees_with_expectation"]]
    assert disagreeing == []


def test_the_two_false_positives_are_the_documented_ones(result: dict) -> None:
    raised = sorted(
        c["id"] for c in result["per_case"] if c["detected"] and not c["real_disagreement"]
    )
    assert raised == ["N5-qualified-exception", "N6-incidental-negation"]


def test_the_cases_that_must_stay_silent_did(result: dict) -> None:
    held = sorted(
        c["id"] for c in result["per_case"] if not c["real_disagreement"] and not c["detected"]
    )
    assert held == [
        "N1-different-subjects",
        "N2-same-subject-different-attribute",
        "N3-agreement-not-contradiction",
        "N4-same-quantity-different-units",
        "N7-different-subjects-distinguished-by-a-word",
        "N8-opposite-directions-are-not-a-disagreement",
        "N9-same-attribute-different-component",
    ]


def test_both_routes_contributed(result: dict) -> None:
    routes = {c["route"] for c in result["per_case"] if c["detected"]}
    assert routes == {"paraphrase", "same-subject-entity"}


def test_scan_was_not_truncated(result: dict) -> None:
    assert result["scan_truncated"] is False


def test_the_corpus_states_its_own_independence_limits(result: dict) -> None:
    """The first nine cases are closer to a regression suite than to an
    independent sample, and the artifact has to say so."""
    import json

    corpus = json.loads(
        (ROOT / "tests" / "evidence" / "corpus" / "contradiction_heldout.json").read_text(
            encoding="utf-8"
        )
    )
    caveat = corpus.get("independence_caveat", "")
    assert "same author as the fix" in caveat
    assert "adversarial" in caveat

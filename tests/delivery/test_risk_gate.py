"""The named risk gate (R-17).

A merge gate on a NAMED level rather than a raw impacted-entity count, with the
thresholds for every level published in the output itself, off by default, and
the score reported whether the gate is on or off.

The older integer gate is kept working rather than silently redefined, so it is
tested here too: it still fails on the same inputs it always failed on, and it
now says in the output that it is deprecated and why.

Most of this runs on plain dictionaries and needs no code extra. The end-to-end
case, which proves the published cuts come from a real graph, is skipped without
one rather than faked.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from dkg.code.report import evaluate_gates

ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / "action.yml"

LEVELS = ("low", "moderate", "elevated", "high")


def _report(level="moderate", score=0.42, impacted=None):
    report = {
        "review": {
            "risk": {
                "level": level,
                "score": score,
                "levels": {
                    "names": list(LEVELS),
                    "cuts": {"low": 0.0, "moderate": 0.31, "elevated": 0.55, "high": 0.79},
                    "derivation": "nearest-rank percentiles of this graph's own score distribution",
                },
                "weights": {"caller_count": 0.25},
            }
        }
    }
    if impacted is not None:
        report["impact"] = {"impacted_count": impacted, "base_ref": "abc", "changed_files": []}
    return report


# -- the gate is off by default and the score is reported anyway --------------


def test_the_gate_is_off_by_default():
    gate = evaluate_gates(_report())
    assert gate["risk"]["enabled"] is False
    assert gate["risk"]["failed"] is False
    assert gate["failed"] is False


def test_the_score_is_reported_even_when_the_gate_is_disabled():
    gate = evaluate_gates(_report(level="high", score=0.91))
    assert gate["risk"]["enabled"] is False
    assert gate["risk"]["observed_level"] == "high"
    assert gate["risk"]["observed_score"] == 0.91
    # Turning the gate off suppresses the failure, never the measurement.
    assert gate["risk"]["failed"] is False
    assert gate["failed"] is False


def test_the_thresholds_for_each_level_are_published_in_the_output():
    gate = evaluate_gates(_report(), risk_gate="elevated")
    cuts = gate["risk"]["cuts"]
    assert set(cuts) == set(LEVELS)
    assert cuts["low"] <= cuts["moderate"] <= cuts["elevated"] <= cuts["high"]
    assert "percentile" in gate["risk"]["derivation"]


# -- the named comparison ----------------------------------------------------


def test_a_level_at_or_above_the_gate_fails():
    for observed, requested, expected in [
        ("high", "high", True),
        ("high", "elevated", True),
        ("elevated", "elevated", True),
        ("moderate", "elevated", False),
        ("low", "moderate", False),
        ("low", "low", True),
        ("moderate", "high", False),
    ]:
        gate = evaluate_gates(_report(level=observed), risk_gate=requested)
        assert gate["risk"]["failed"] is expected, (observed, requested)
        assert gate["failed"] is expected, (observed, requested)


def test_the_verdict_explains_itself():
    gate = evaluate_gates(_report(level="high"), risk_gate="moderate")
    assert "high" in gate["risk"]["why"]
    assert "moderate" in gate["risk"]["why"]
    assert "at or above" in gate["risk"]["why"]


def test_an_unknown_level_does_not_silently_pass_or_fail():
    gate = evaluate_gates(_report(), risk_gate="catastrophic")
    assert gate["risk"]["failed"] is False
    assert "unknown risk level" in gate["risk"]["why"]
    for name in LEVELS:
        assert name in gate["risk"]["why"]


def test_a_gate_with_no_risk_analysis_behind_it_does_not_fail_silently():
    gate = evaluate_gates({}, risk_gate="high")
    assert gate["risk"]["enabled"] is True
    assert gate["risk"]["failed"] is False
    assert "nothing to gate on" in gate["risk"]["why"]


# -- the deprecated integer gate still behaves exactly as it did -------------


def test_the_impact_gate_still_works_and_is_labelled_deprecated():
    over = evaluate_gates(_report(impacted=12), fail_on_impact=5)
    assert over["impact"]["enabled"] is True
    assert over["impact"]["failed"] is True
    assert over["failed"] is True
    assert over["impact"]["deprecated"] is True
    assert "not comparable across repositories" in over["impact"]["why"]

    under = evaluate_gates(_report(impacted=3), fail_on_impact=5)
    assert under["impact"]["failed"] is False
    assert under["failed"] is False

    # Strictly greater than, as it always was: equal does not fail.
    equal = evaluate_gates(_report(impacted=5), fail_on_impact=5)
    assert equal["impact"]["failed"] is False


def test_the_impact_gate_is_off_when_not_requested():
    gate = evaluate_gates(_report(impacted=999))
    assert gate["impact"]["enabled"] is False
    assert gate["impact"]["failed"] is False
    assert gate["impact"]["observed_count"] == 999  # still reported


def test_both_gates_can_be_on_and_either_can_fail_the_run():
    gate = evaluate_gates(_report(level="low", impacted=99), risk_gate="high", fail_on_impact=1)
    assert gate["risk"]["failed"] is False
    assert gate["impact"]["failed"] is True
    assert gate["failed"] is True


# -- the surfaces expose it --------------------------------------------------


def test_the_cli_exposes_the_named_gate_and_deprecates_the_count():
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    proc = subprocess.run(
        [sys.executable, "-m", "dkg", "code-report", "--help"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=env,
        timeout=60,
    )
    assert proc.returncode == 0
    assert "--risk-gate" in proc.stdout
    for name in LEVELS:
        assert name in proc.stdout
    assert "--fail-on-impact" in proc.stdout
    assert "DEPRECATED" in proc.stdout


def test_the_action_exposes_the_named_gate_and_reports_the_score():
    text = ACTION.read_text(encoding="utf-8")
    assert re.search(r"^  risk-gate:\s*$", text, re.M)
    assert re.search(r'^    default:\s*"off"\s*$', text, re.M), "the gate must be off by default"
    # The score is an output whether or not the gate is on.
    assert re.search(r"^  risk-level:\s*$", text, re.M)
    assert re.search(r"^  risk-score:\s*$", text, re.M)
    assert "--risk-gate" in text
    assert "DEPRECATED" in text


# -- end to end, against a real graph ----------------------------------------


def test_published_cuts_come_from_a_real_graph():
    import pytest

    pytest.importorskip("tree_sitter")
    import tempfile

    from dkg.code.ingest import ingest_repo
    from dkg.code.report import build_report
    from dkg.core.db import open_database

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo = root / "repo"
        repo.mkdir()
        (repo / "lib.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
        (repo / "app.py").write_text(
            "from lib import helper\n\n\ndef run():\n    return helper()\n", encoding="utf-8"
        )
        with open_database(root / "g.db") as db:
            ingest_repo(db, repo, audit_path=root / "a.log")
            report = build_report(db, repo, review=True)

    risk = report["review"]["risk"]
    assert risk["level"] in LEVELS
    assert 0.0 <= risk["score"] <= 1.0
    assert set(risk["levels"]["cuts"]) == set(LEVELS)

    off = evaluate_gates(report, risk_gate="off")
    assert off["failed"] is False
    # The measurement survives the gate being off.
    assert off["risk"]["observed_level"] == risk["level"]
    assert off["risk"]["observed_score"] == risk["score"]

    on = evaluate_gates(report, risk_gate=risk["level"])
    assert on["failed"] is True, "gating at the observed level must fail"

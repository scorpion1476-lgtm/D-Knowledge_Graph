"""Measured code-plane accuracy and timings. Skips without the code extra."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter")

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

import code_accuracy  # noqa: E402


def test_per_language_parsing_accuracy():
    from dkg.code.parser import claimed_languages

    per = code_accuracy.measure_parsing()
    # Every language this build claims must carry a labelled corpus, so a
    # language can never be claimed without a measurement standing behind it.
    assert set(per) == set(claimed_languages())
    # The starter set is always present wherever the code extra is installed.
    assert {"python", "javascript", "go"}.issubset(set(per))
    measured = 0
    for lang, m in per.items():
        if m["status"] != "measured":
            # An absent optional grammar is reported as not measured, never as
            # a zero that would read like a real accuracy failure.
            assert m["status"] in ("not_measured_in_this_environment", "unsupported"), (lang, m)
            assert "precision" not in m, (lang, m)
            continue
        measured += 1
        assert m["precision"] >= 0.9, (lang, m)
        assert m["recall"] >= 0.9, (lang, m)
    assert measured >= 3


def test_incremental_reparses_only_changed():
    m = code_accuracy.measure_incremental()
    assert m["incremental_files_reparsed"] == 1
    assert m["unchanged_files"] == 2
    assert m["incremental_seconds"] >= 0


def test_blast_radius_metrics_honest_over_approximation():
    m = code_accuracy.measure_impact()
    assert m["recall"] == 1.0
    assert 0.0 < m["precision"] <= 1.0
    assert {"chain.py::mid", "chain.py::top"}.issubset(set(m["found_impact"]))
    # the name-collision case over-flags, so precision is honestly below 1.0
    assert m["precision"] < 1.0

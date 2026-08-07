"""Token-efficiency measurement: the estimator, the corpus, and the honesty rules.

The estimator and helpers are unit-tested directly. The full measurement is run
once, gated on the code extra, because it ingests the corpus.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import token_efficiency as te  # noqa: E402

try:
    import tree_sitter  # noqa: F401

    _TS = True
except Exception:
    _TS = False

requires_ts = pytest.mark.skipif(not _TS, reason="tree-sitter not installed (the 'code' extra)")


# -- estimator --------------------------------------------------------------


def test_estimator_is_deterministic_and_monotonic():
    text = "def core_util_0(value):\n    return value + 1\n"
    assert te.estimate_tokens(text) == te.estimate_tokens(text)
    assert te.estimate_tokens(text + text) > te.estimate_tokens(text)
    assert te.estimate_tokens("") == 0


def test_estimator_follows_its_documented_rule():
    # Four letters or fewer is one token; a longer run splits.
    assert te.estimate_tokens("abcd") == 1
    assert te.estimate_tokens("abcde") == 2
    # Each digit and each punctuation mark counts once.
    assert te.estimate_tokens("12") == 2
    assert te.estimate_tokens("()") == 2
    # A newline counts; other whitespace does not.
    assert te.estimate_tokens("\n") == 1
    assert te.estimate_tokens("    ") == 0


# -- corpus -----------------------------------------------------------------


def test_corpus_is_present_and_its_structure_is_recorded():
    files = te._corpus_files()
    assert len(files) >= 30, "the corpus should be large enough for the ratio to mean something"
    structure = json.loads((te.CORPUS / "structure.json").read_text(encoding="utf-8"))
    assert structure["deterministic"] is True
    assert structure["files"] == len(files)
    assert "generate_corpus.py" not in {p.name for p in files}


def test_corpus_regeneration_is_byte_identical():
    # A retained corpus that drifted on regeneration would silently invalidate
    # every published number measured against it.
    before = {p.name: p.read_bytes() for p in te._corpus_files()}
    sys.path.insert(0, str(te.CORPUS))
    import generate_corpus  # noqa: PLC0415

    generate_corpus.generate()
    after = {p.name: p.read_bytes() for p in te._corpus_files()}
    assert before == after


def test_subset_keeps_the_corpus_internally_consistent():
    files = te._corpus_files()
    subset = te._subset(files, 5)
    names = {p.name for p in subset}
    assert "core.py" in names
    assert any(n.startswith("layer_") for n in names)
    # Every retained leaf module is below the cut, so nothing imports a module
    # that was dropped.
    leaves = [n for n in names if n.startswith("mod_")]
    assert leaves
    for name in leaves:
        assert int("".join(c for c in name[:-3] if c.isdigit())) < 5
    assert len(subset) < len(files)


def test_follow_up_text_is_capped_and_reports_what_it_dropped():
    files = te._corpus_files()
    named = {p.name for p in files}
    text, chosen, omitted = te._follow_up_text(named, files)
    assert len(chosen) == te.FOLLOW_UP_FILE_CAP
    assert omitted == len(named) - te.FOLLOW_UP_FILE_CAP
    assert text
    # A name that is not in the corpus is ignored rather than guessed at.
    _t, chosen2, _o = te._follow_up_text({"does_not_exist.py"}, files)
    assert chosen2 == []


def test_named_paths_extracts_files_from_answer_shapes():
    payload = {
        "root": {"canonical": "core.py::core_util_0"},
        "impacted": [{"canonical": "mod_01.py::mod_01_run"}],
        "questions": [{"subject": "app.py::a->lib.py::b"}],
        "unrelated": {"display": "not_a_path"},
    }
    assert te._named_paths(payload) == {"core.py", "mod_01.py", "app.py", "lib.py"}


# -- the measurement --------------------------------------------------------


@requires_ts
def test_measurement_reports_all_three_figures_and_the_scaling_evidence():
    result = te.run()
    assert result["corpus"]["symbols_ingested"] > 0, "an empty graph must never produce a ratio"
    assert result["corpus"]["files"] >= 30

    for m in result["measurements"]:
        assert m["question"].endswith("?")
        # All three figures present, and the honest one charges more than the
        # bare answer because it also reads the files the answer names.
        assert m["full_corpus"]["tokens"] > 0
        assert m["graph_answer"]["tokens"] > 0
        assert m["graph_plus_sources"]["tokens"] >= m["graph_answer"]["tokens"]
        assert m["token_ratio_vs_graph_answer"] >= m["token_ratio_vs_graph_plus_sources"]

    summary = result["summary"]
    assert summary["headline_metric"] == "token_ratio_vs_graph_plus_sources"
    # The headline is the conservative figure, never the flattering one.
    assert summary["mean_token_ratio_graph_plus_sources"] <= summary["mean_token_ratio_answer_only"]


@requires_ts
def test_the_ratio_grows_with_corpus_size_as_claimed():
    result = te.run()
    scaling = result["scaling"]
    assert len(scaling) >= 2, "the size dependence must be measured, not just asserted"
    sizes = [s["corpus_tokens"] for s in scaling]
    ratios = [s["mean_token_ratio_graph_plus_sources"] for s in scaling]
    assert sizes == sorted(sizes)
    # The central claim of this benchmark: a bigger corpus makes the graph route
    # relatively cheaper. If this ever stops holding, the framing is wrong.
    assert ratios == sorted(ratios), ratios
    assert ratios[-1] > ratios[0]


@requires_ts
def test_scope_and_limitations_are_stated_in_the_artifact():
    result = te.run()
    why = result["why"]
    assert "not a universal claim" in why["scope"]
    assert "corpus size" in why["scope"]
    assert "Answer quality is not measured" in why["limitation"]
    assert "not a vendor tokenizer" in result["estimator"]["kind"]

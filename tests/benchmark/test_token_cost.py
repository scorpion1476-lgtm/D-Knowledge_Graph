"""The token-cost benchmark: corpus, tasks, aggregation, and honesty rules.

The tasks themselves are slow (they ingest a 414-file corpus), so the full runs
are marked slow. What is always checked is the structure that keeps the
published numbers honest: one tokenizer on both sides, a required-set
correctness score rather than a judgement, and a saving that is never reported
as a win when correctness fell.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT / "scripts"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import token_cost  # noqa: E402
from tokentasks import common  # noqa: E402

CORPUS = ROOT / "tests" / "code" / "corpus" / "large"

# Importing tree_sitter is not the capability these tasks need. An environment
# with the base library but no grammar installed passed this guard and then
# failed inside the benchmark with "code ingestion requires the 'code' extra",
# turning an honest skip into a red test. Ask the code plane what it can
# actually parse.
try:
    from dkg.code.capability import available_languages

    _LANGS = available_languages()
except Exception:
    _LANGS = []
_TS = bool(_LANGS)

requires_ts = pytest.mark.skipif(
    not _TS, reason="no Tree-sitter grammar available (install the 'code' extra)"
)


# -- corpus -----------------------------------------------------------------


def test_corpus_is_large_enough_for_a_ratio_to_mean_anything():
    corpus = common.load_corpus()
    assert len(corpus.code_files) >= 400
    assert len(corpus.doc_files) >= 20
    truth = corpus.truth
    assert truth["deterministic"] is True
    assert truth["code_bytes"] > 200_000


def test_corpus_regeneration_is_byte_identical():
    before = {p.name: p.read_bytes() for p in sorted((CORPUS / "code").glob("*.py"))}
    sys.path.insert(0, str(CORPUS))
    import generate_corpus  # noqa: PLC0415

    generate_corpus.generate()
    after = {p.name: p.read_bytes() for p in sorted((CORPUS / "code").glob("*.py"))}
    assert before == after


def test_ground_truth_covers_every_scored_dimension():
    truth = common.load_corpus().truth
    assert truth["impact"] and all(v for v in truth["impact"].values())
    assert len(truth["contradictions"]) >= 6
    for c in truth["contradictions"]:
        assert c["value_a"] != c["value_b"]
        assert c["doc_a"] != c["doc_b"]
    assert len(truth["qa"]) >= 8


# -- baselines --------------------------------------------------------------


def test_the_strong_baseline_is_a_real_baseline_not_a_straw_man():
    corpus = common.load_corpus()
    files = common.strong_baseline_files(corpus, "What is the blast radius of layer_0_gateway?")
    assert files, "the baseline must actually retrieve something"
    assert len(files) <= common.STRONG_BASELINE_FILE_BUDGET
    # It reads whole files, which is exactly what an agent without a graph does.
    assert common.strong_baseline_text(corpus, "layer_0_gateway")


def test_query_terms_drop_stopwords_but_keep_identifiers():
    terms = common.query_terms("What is the blast radius of layer_0_gateway?")
    assert "layer_0_gateway" in terms
    assert "what" not in [t.lower() for t in terms]


# -- scoring honesty --------------------------------------------------------


def test_correctness_is_recall_against_a_required_set():
    assert common.required_recall(["a b c"], ["a", "b", "c"]) == 1.0
    assert common.required_recall(["a b"], ["a", "b", "c"]) == pytest.approx(0.6667, abs=1e-3)
    assert common.required_recall([""], ["a"]) == 0.0
    # An empty requirement is vacuously satisfied, not a failure.
    assert common.required_recall([""], []) == 1.0


def test_a_cheaper_but_less_correct_route_is_not_recorded_as_a_win():
    baseline = {"tokens": 1000, "cost_usd": 0.003, "correctness": 1.0}
    cheap_and_wrong = {"tokens": 100, "cost_usd": 0.0003, "correctness": 0.2}
    s = common.savings(baseline, cheap_and_wrong)
    assert s["tokens_saved"] == 900
    assert s["correctness_held"] is False, "dropping correctness must never read as a win"


def test_a_dearer_but_more_correct_route_is_recorded_faithfully():
    baseline = {"tokens": 100, "cost_usd": 0.0003, "correctness": 0.24}
    dearer = {"tokens": 500, "cost_usd": 0.0015, "correctness": 1.0}
    s = common.savings(baseline, dearer)
    assert s["tokens_saved"] < 0, "a loss must be reported as a negative saving"
    assert s["correctness_held"] is True


def test_route_record_uses_the_shared_tokenizer():
    from dkg.context.tokens import count_tokens

    text = "def util():\n    return 1\n"
    record = common.route_record("x", text, correctness=1.0)
    assert record["tokens"] == count_tokens(text), "both sides must use one tokenizer"


# -- harness ----------------------------------------------------------------


def test_every_task_is_registered_in_the_harness():
    names = {t[0] for t in token_cost.TASKS}
    assert names == {
        "impact_analysis",
        "code_review",
        "knowledge_base_qa",
        "evidence_contradiction",
    }
    for _name, module_name, title in token_cost.TASKS:
        assert title
        rel = module_name.replace(".", "/") + ".py"
        assert (ROOT / "scripts" / rel).is_file(), rel


def test_published_artifact_is_present_and_self_consistent():
    path = ROOT / "test-evidence" / "token_cost.json"
    if not path.is_file():
        pytest.skip("token_cost.json not generated in this environment")
    data = json.loads(path.read_text(encoding="utf-8"))
    agg = data["aggregate"]
    # The naive upper bound must cost more than either real route.
    assert agg["naive"]["tokens"] > agg["strong"]["tokens"]
    assert agg["naive"]["tokens"] > agg["graph"]["tokens"]
    gv = data["graph_vs_strong"]
    assert gv["won_count"] + len(gv["did_not_win"]) == gv["task_count"]
    # The framing must name the strong baseline as the one that matters.
    assert "baseline that matters" in data["why"]["strong_baseline"]
    assert "upper bound" in data["why"]["naive_baseline"]
    assert data["tokenizer"]["tokenizer"]


def test_the_artifact_does_not_hide_a_lost_task():
    path = ROOT / "test-evidence" / "token_cost.json"
    if not path.is_file():
        pytest.skip("token_cost.json not generated in this environment")
    data = json.loads(path.read_text(encoding="utf-8"))
    # Whatever the outcome, every task must appear in exactly one bucket.
    buckets = data["graph_vs_strong"]
    listed = {w["task"] for w in buckets["won_on_tokens_with_correctness_held"]}
    listed |= {w["task"] for w in buckets["did_not_win"]}
    ran = {n for n, e in data["tasks"].items() if e["status"] == "ran"}
    assert listed == ran, "a task that ran must be reported as won or not won"


@pytest.mark.slow
@requires_ts
def test_the_full_benchmark_runs_and_reports_every_task():
    summary = token_cost.build()
    for name, entry in summary["tasks"].items():
        assert entry["status"] == "ran", f"{name}: {entry.get('reason')}"
    assert summary["aggregate"]["graph"]["tokens"] > 0

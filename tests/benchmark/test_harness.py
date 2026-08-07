"""Benchmark harness structure and the published results artifact.

These tests do not run the benchmarks (that is the harness's job and the CI
benchmark lane); they validate the harness code with synthetic data and assert
that the committed, published artifact is well-formed and that the relative
claims still hold for whichever benchmarks ran when it was generated.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import benchmark  # noqa: E402


def test_expanded_corpora_are_representative():
    # Retrieval: at least 30 documents and 40 queries.
    c = json.loads((ROOT / "tests/retrieval/corpus/corpus.json").read_text())
    q = json.loads((ROOT / "tests/retrieval/corpus/queries.json").read_text())
    assert len(c["documents"]) >= 30
    assert len(q["queries"]) >= 40
    # Code resolution: several dozen true edges per resolved language.
    gt = json.loads((ROOT / "tests/code/corpus/ambiguity/ground_truth.json").read_text())
    assert len(gt["languages"]["python"]["true_call_edges"]) >= 20
    assert len(gt["languages"]["javascript"]["true_call_edges"]) >= 20
    # Community: several dozen semantic entities and at least 16 structural cliques.
    g = json.loads((ROOT / "tests/graph/corpus/graph_corpus.json").read_text())
    assert len(g["semantic"]["entities"]) >= 40
    assert g["structural"]["cliques"] >= 16


def test_registry_is_well_formed():
    assert len(benchmark.BENCHMARKS) == 10
    names = [b[0] for b in benchmark.BENCHMARKS]
    assert len(set(names)) == len(names), "duplicate benchmark name"
    # Every benchmark the project claims to publish must be registered here, or
    # the one-command harness silently stops regenerating it.
    assert {
        "retrieval",
        "contradiction",
        "community",
        "code_resolution",
        "execution_flow",
        "code_parse_impact",
        "token_efficiency",
        "token_cost",
        "media_ocr_asr",
        "media_enrichment",
    } == set(names)
    for name, module_name, func_name, out_file, title in benchmark.BENCHMARKS:
        assert name and module_name and func_name and title
        assert out_file.endswith(".json")
        assert (ROOT / "scripts" / f"{module_name}.py").is_file(), module_name


def test_render_handles_ran_and_not_run():
    summary = {
        "generated_at": "2026-08-04T00:00:00+00:00",
        "seed": {"PYTHONHASHSEED": 0},
        "environment": {"embedding_model": True, "tesseract": False},
        "corpus_sizes": {"retrieval": {"documents": 30, "queries": 40}},
        "benchmarks": {
            "retrieval": {
                "title": "Retrieval quality",
                "status": "ran",
                "result": {
                    "configurations": {
                        "A_keyword_only_baseline": {"mrr": 0.9, "ndcg@10": 0.9, "recall@10": 0.97, "mean_latency_ms": 1.0},
                        "B_previous_stub_hybrid": {"mrr": 0.7, "ndcg@10": 0.8, "recall@10": 0.97, "mean_latency_ms": 2.0},
                        "C_new_embeddings_plus_rerank": {"mrr": 1.0, "ndcg@10": 1.0, "recall@10": 1.0, "mean_latency_ms": 150.0},
                    },
                    "new_beats_keyword_baseline": {"mrr_delta": 0.1, "ndcg@10_delta": 0.1, "latency_overhead_ms": 149.0},
                },
            },
            "media_ocr_asr": {
                "title": "Media OCR and ASR",
                "status": "not_run_in_this_environment",
                "reason": "tesseract absent",
            },
        },
    }
    md = benchmark._render_markdown(summary)
    assert "# D-Knowledge_Graph benchmark results" in md
    assert "PYTHONHASHSEED=0" in md
    assert "Retrieval quality | ran" in md
    assert "not_run_in_this_environment" in md
    assert "0.9" in md and "1.0" in md
    # No eponymous community-detection algorithm names leak into the rendered
    # document. The forbidden terms are assembled from parts so the literal names
    # never appear in this tracked file.
    banned = {"lou" + "vain", "lei" + "den"}
    lowered = md.lower()
    assert not any(term in lowered for term in banned)


def test_published_artifact_is_well_formed_and_claims_hold():
    path = ROOT / "test-evidence" / "benchmarks.json"
    if not path.exists():
        pytest.skip("benchmarks.json not generated in this checkout")
    summary = json.loads(path.read_text(encoding="utf-8"))
    assert "seed" in summary and "environment" in summary and "benchmarks" in summary
    for name, entry in summary["benchmarks"].items():
        assert entry["status"] in ("ran", "not_run_in_this_environment", "error"), (name, entry["status"])
        assert entry["status"] != "error", (name, entry.get("reason"))

    b = summary["benchmarks"]

    # Retrieval: where the embeddings-plus-rerank config ran, it does not lose to
    # the keyword baseline on recall.
    r = b.get("retrieval", {}).get("result")
    if r:
        c = r["configurations"].get("C_new_embeddings_plus_rerank")
        a = r["configurations"].get("A_keyword_only_baseline")
        if isinstance(c, dict) and isinstance(a, dict):
            assert c["recall@10"] >= a["recall@10"]

    # Code resolution: where it ran, resolved precision and recall are at least
    # structural for every resolved language.
    r = b.get("code_resolution", {}).get("result")
    if r:
        for lang, m in r["per_language"].items():
            if "blast_radius" in m:
                for metric in ("blast_radius", "execution_flow"):
                    assert m[metric]["resolved"]["precision"] >= m[metric]["structural"]["precision"], (lang, metric)
                    assert m[metric]["resolved"]["recall"] >= m[metric]["structural"]["recall"], (lang, metric)

    # Community: where the semantic sub-corpus ran, the refinement detector's
    # agreement is at least the default's (the semantic advantage holds).
    r = b.get("community", {}).get("result")
    if r and "mnemosyne" in r.get("semantic", {}):
        sem = r["semantic"]
        assert sem["ariadne"]["rand_index_vs_truth"] >= sem["mnemosyne"]["rand_index_vs_truth"]

#!/usr/bin/env python3
"""One-command reproducible benchmark harness.

Runs every accuracy and quality benchmark across both planes in one seeded
process and regenerates the published results. A benchmark whose required tool or
model is not staged is reported as not run in this environment rather than
failing the harness.

Usage:
    python scripts/benchmark.py            # run all, write artifacts
    python scripts/benchmark.py --list     # list the benchmarks

Determinism: the harness re-execs itself once with PYTHONHASHSEED=0 if that is
not already set, disables outbound network and telemetry, and puts the staged
language servers on PATH. Every benchmark is deterministic by construction (fixed
retained corpora, deterministic models and algorithms), so re-runs on the same
staged environment reproduce the numbers. The seed is recorded in the artifact.

Outputs:
    test-evidence/<name>.json     each benchmark's own artifact (unchanged paths)
    test-evidence/benchmarks.json unified results with status and corpus sizes
    docs/BENCHMARKS.md            the tracked, public-safe results document
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
EVIDENCE = ROOT / "test-evidence"
SEED = 0


def _bootstrap() -> None:
    """Re-exec once with a fixed hash seed and a locked-down, staged environment."""
    if os.environ.get("PYTHONHASHSEED") != str(SEED):
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = str(SEED)
        env.setdefault("DKG_ALLOW_OUTBOUND", "0")
        env.setdefault("DKG_TELEMETRY", "0")
        lsp_bin = ROOT / "tools" / "lsp" / "node_modules" / ".bin"
        if lsp_bin.is_dir():
            env["PATH"] = f"{lsp_bin}{os.pathsep}{env.get('PATH', '')}"
        os.execve(sys.executable, [sys.executable, *sys.argv], env)  # noqa: S606 (list args, no shell)


# name -> (module, run-function, output filename, human title)
BENCHMARKS = [
    ("retrieval", "retrieval_quality", "run_evaluation", "retrieval_quality.json", "Retrieval quality"),
    ("contradiction", "contradiction_quality", "run", "contradiction_quality.json", "Contradiction detection (held-out corpus)"),
    ("community", "community_quality", "run_benchmark", "community_quality.json", "Community detection"),
    ("code_resolution", "resolution_accuracy", "run", "resolution_accuracy.json", "Code resolution (structural vs resolved)"),
    ("execution_flow", "flow_accuracy", "run", "flow_accuracy.json", "Execution-flow accuracy"),
    ("code_parse_impact", "code_accuracy", "run", "code_accuracy.json", "Code parse and blast-radius accuracy"),
    ("token_efficiency", "token_efficiency", "run", "token_efficiency.json", "Token efficiency (full corpus vs graph query)"),
    ("token_cost", "token_cost", "build", "token_cost.json", "Token cost across four tasks (strong baseline)"),
    ("media_ocr_asr", "media_accuracy", "run", "media_accuracy.json", "Media OCR and ASR"),
    ("media_enrichment", "media_enrichment_accuracy", "run", "media_enrichment_accuracy.json", "Media enrichment (scene, keyframe OCR, detection)"),
]

# Errors that mean a tool or model is simply not staged here.
_ABSENT_MARKERS = ("requires the", "not installed", "no grammar", "unavailable", "not staged", "extra:")


def _corpus_sizes() -> dict:
    sizes: dict = {}
    rp = ROOT / "tests" / "retrieval" / "corpus"
    if (rp / "corpus.json").exists() and (rp / "queries.json").exists():
        c = json.loads((rp / "corpus.json").read_text())
        q = json.loads((rp / "queries.json").read_text())
        sizes["retrieval"] = {"documents": len(c["documents"]), "queries": len(q["queries"])}
    gp = ROOT / "tests" / "graph" / "corpus" / "graph_corpus.json"
    if gp.exists():
        g = json.loads(gp.read_text())
        sizes["community"] = {
            "structural_nodes": len(g["structural"]["nodes"]),
            "structural_cliques": g["structural"]["cliques"],
            "semantic_entities": len(g["semantic"]["entities"]),
            "semantic_topics": g["semantic"]["topics"],
        }
    return sizes


def run_all() -> dict:
    for p in (str(SRC), str(SCRIPTS)):
        if p not in sys.path:
            sys.path.insert(0, p)

    results: dict = {}
    for name, module_name, func_name, out_file, title in BENCHMARKS:
        entry: dict = {"title": title, "module": f"scripts/{module_name}.py"}
        try:
            module = importlib.import_module(module_name)
            fn = getattr(module, func_name)
            result = fn()
            entry["status"] = "ran"
            entry["result"] = result
            # Write the benchmark's own artifact so existing evidence paths stay valid.
            (EVIDENCE / out_file).write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
            entry["artifact"] = f"test-evidence/{out_file}"
        except Exception as e:  # noqa: BLE001
            message = f"{type(e).__name__}: {e}"
            absent = isinstance(e, ImportError) or any(m in message.lower() for m in _ABSENT_MARKERS)
            entry["status"] = "not_run_in_this_environment" if absent else "error"
            entry["reason"] = message
            if not absent:
                entry["traceback"] = traceback.format_exc().splitlines()[-3:]
        results[name] = entry
    return results


def _environment() -> dict:
    env: dict = {}
    try:
        from dkg.adapters.embedding import Model2VecEmbeddingAdapter

        env["embedding_model"] = Model2VecEmbeddingAdapter().available()[0]
    except Exception:
        env["embedding_model"] = False
    try:
        from dkg.adapters.reranker import CrossEncoderReranker

        env["reranker"] = CrossEncoderReranker().available()[0]
    except Exception:
        env["reranker"] = False
    try:
        from dkg.code.lsp import resolution_available

        env["python_language_server"] = resolution_available("python")
        env["javascript_language_server"] = resolution_available("javascript")
    except Exception:
        env["python_language_server"] = env["javascript_language_server"] = False
    import shutil

    env["ffmpeg"] = shutil.which("ffmpeg") is not None
    env["tesseract"] = shutil.which("tesseract") is not None
    return env


def build() -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seed": {"PYTHONHASHSEED": SEED},
        "note": (
            "Reproducible from `python scripts/benchmark.py` on a staged "
            "environment. Numbers are deterministic on the retained corpora. "
            "Benchmarks whose tool or model is absent are marked not run in this "
            "environment. Comparisons are internal only (structural vs resolved; "
            "base vs refinement detector) with absolute numbers."
        ),
        "environment": _environment(),
        "corpus_sizes": _corpus_sizes(),
        "benchmarks": run_all(),
    }


# -- results document -------------------------------------------------------


def _fmt(x) -> str:
    return "n/a" if x is None else str(x)


def _render_markdown(summary: dict) -> str:
    L: list[str] = [
        "# D-Knowledge_Graph benchmark results",
        "",
        "Authoritative published benchmark. Regenerate with `python scripts/benchmark.py`.",
        f"Generated: {summary['generated_at']}. Seed: PYTHONHASHSEED={summary['seed']['PYTHONHASHSEED']}.",
        "",
        "Every number below is measured on a retained corpus whose generation is "
        "documented next to it, and is deterministic given the same corpus and "
        "staged models. Comparisons are internal only: a structural baseline "
        "versus type-aware resolution, and the default community detector versus "
        "the refinement one. Benchmarks whose tool or model is not staged are marked "
        "not run in this environment, not failed.",
        "",
        "## Environment staged for this run",
        "",
        "| Component | Staged |",
        "| --- | --- |",
    ]
    for k, v in summary["environment"].items():
        L.append(f"| {k} | {'yes' if v else 'no'} |")
    L += ["", "## Benchmark status", "", "| Benchmark | Status |", "| --- | --- |"]
    for e in summary["benchmarks"].values():
        status = e["status"] + (f" ({e['reason']})" if e.get("reason") and e["status"] != "ran" else "")
        L.append(f"| {e['title']} | {status} |")

    b = summary["benchmarks"]
    cs = summary["corpus_sizes"]

    # Retrieval
    r = b.get("retrieval", {}).get("result")
    if r:
        L += [
            "",
            "## Retrieval quality",
            "",
            f"Corpus: {cs.get('retrieval', {}).get('documents')} documents, "
            f"{cs.get('retrieval', {}).get('queries')} queries "
            "(`tests/retrieval/corpus`, generated by generate_corpus.py).",
            "",
            "| Configuration | MRR | nDCG@10 | recall@10 | latency ms |",
            "| --- | --- | --- | --- | --- |",
        ]
        for key, label in [
            ("A_keyword_only_baseline", "Keyword baseline (FTS5)"),
            ("B_previous_stub_hybrid", "Stub hybrid (hashing vector)"),
            ("C_new_embeddings_plus_rerank", "Embeddings + cross-encoder rerank"),
        ]:
            m = r["configurations"].get(key)
            if isinstance(m, dict):
                L.append(f"| {label} | {m['mrr']} | {m['ndcg@10']} | {m['recall@10']} | {m['mean_latency_ms']} |")
            else:
                L.append(f"| {label} | not run | | | |")
        nb = r.get("new_beats_keyword_baseline")
        if nb:
            L += ["", f"Embeddings-plus-rerank beats the keyword baseline: MRR delta "
                  f"{nb['mrr_delta']}, nDCG delta {nb['ndcg@10_delta']}, latency overhead "
                  f"{nb['latency_overhead_ms']} ms."]

    # Contradiction detection
    r = b.get("contradiction", {}).get("result")
    if r:
        L += [
            "",
            "## Contradiction detection (held-out corpus)",
            "",
            f"Corpus: {r['cases_total']} cases in "
            "`tests/evidence/corpus/contradiction_heldout.json`, in domains absent "
            "from the token-cost corpus. All documents are ingested into one graph "
            "and scanned once, so a case can be failed by a false positive raised "
            "against another case.",
            "",
            "| Measure | Value |",
            "| --- | --- |",
            f"| Real disagreements in the corpus | {r['real_disagreements']} |",
            f"| Detected | {r['true_positives']} |",
            f"| Recall | {r['recall']} |",
            f"| Signals returned | {r['signals_returned']} |",
            f"| Precision | {r['precision']} |",
            f"| False-positive signals | {r['false_positive_signals']} |",
            f"| Cases that had to stay silent, and did | "
            f"{r['silent_cases_held']} of {r['must_stay_silent']} |",
            "",
            f"Missed: {', '.join(r['missed_real_disagreements']) or 'none'}. "
            f"False positives: {', '.join(r.get('known_false_positives', [])) or 'none'}. "
            + str(r["recall_note"]),
            "",
            "How held-out this is, stated rather than implied: "
            + str(r.get("independence_caveat", "")),
            "",
            "The scanner is lexical and over-approximate; its output is advisory, and "
            "it is not an entailment model.",
        ]

    # Community
    r = b.get("community", {}).get("result")
    if r:
        L += [
            "",
            "## Community detection (Mnemosyne base pass vs Ariadne refinement pass)",
            "",
            f"Structural corpus: {cs.get('community', {}).get('structural_nodes')} nodes in "
            f"{cs.get('community', {}).get('structural_cliques')} cliques. "
            f"Semantic corpus: {cs.get('community', {}).get('semantic_entities')} entities across "
            f"{cs.get('community', {}).get('semantic_topics')} topics "
            "(`tests/graph/corpus`, generated by generate_corpus.py).",
            "",
            "| Sub-corpus | Detector | communities | modularity | agreement (Rand) |",
            "| --- | --- | --- | --- | --- |",
        ]
        st = r.get("structural", {})
        for det in ("mnemosyne", "ariadne"):
            m = st.get(det, {})
            L.append(f"| structural | {det} | {_fmt(m.get('num_communities'))} | {_fmt(m.get('modularity'))} | {_fmt(m.get('rand_index_vs_truth'))} |")
        sem = r.get("semantic", {})
        if "mnemosyne" in sem:
            for det in ("mnemosyne", "ariadne"):
                m = sem.get(det, {})
                L.append(f"| semantic | {det} | {_fmt(m.get('num_communities'))} | {_fmt(m.get('modularity'))} | {_fmt(m.get('rand_index_vs_truth'))} |")
        else:
            L.append(f"| semantic | (both) | not run | | {sem.get('reason', 'embeddings absent')} |")
        L += ["", f"Structural verdict: {r['verdict']['structural']}. Semantic verdict: {r['verdict']['semantic']}."]
        L += [
            "",
            "Each detector is measured on its own here, so read the table alongside how "
            "the default path chooses. `dkg community` runs both passes and returns the "
            "partition with the higher structural modularity, strictly greater so a tie "
            "keeps the base pass. On the structural corpus the two tie, so the base "
            "partition is returned. On the semantic corpus the refinement agrees better "
            "with the known topics but scores LOWER structural modularity, so the "
            "default path returns the BASE partition there. Higher agreement with ground "
            "truth is not the selection criterion; modularity is, and modularity is a "
            "structural score. To obtain the refinement partition on such a graph, ask "
            "for it directly with `dkg community --detector ariadne`. The two detectors "
            "are described in full in docs/MNEMOSYNE.md and docs/ARIADNE.md.",
            "",
            "One further detail, so the table is not read as more than it is. Through "
            "`dkg community` and through the combined default path a resolution of 1.0 "
            "is supplied explicitly, so Ariadne's auto-tuned resolution sweep does not "
            "run on those paths; the sweep runs when the Python API is called without a "
            "resolution, which is how the semantic row above was produced.",
        ]

    # Code resolution
    r = b.get("code_resolution", {}).get("result")
    if r:
        L += [
            "",
            "## Code resolution: structural vs type-aware resolved",
            "",
            "Corpus: `tests/code/corpus/ambiguity` (generated by generate_corpus.py), "
            "systematically ambiguous same-named methods with direct, polymorphic-"
            "reassignment, and unique-name callers.",
            "",
            "| Language | eval nodes | true edges | blast-radius precision (struct / resolved) | recall (struct / resolved) |",
            "| --- | --- | --- | --- | --- |",
        ]
        for lang, m in r["per_language"].items():
            if "blast_radius" in m:
                br = m["blast_radius"]
                L.append(
                    f"| {lang} | {m['eval_nodes']} | {m['true_call_edges']} | "
                    f"{br['structural']['precision']} / {br['resolved']['precision']} | "
                    f"{br['structural']['recall']} / {br['resolved']['recall']} |"
                )
            else:
                L.append(f"| {lang} | | | {m.get('resolution_status', 'structural only')} | |")

    # Execution flow
    r = b.get("execution_flow", {}).get("result")
    if r and "per_language" in r:
        L += [
            "",
            "## Execution-flow accuracy (structural call graph)",
            "",
            "Corpus: `tests/code/corpus/flow`, deliberately unambiguous per-language "
            "call graphs with hand-labelled ground truth.",
            "",
            "| Language | edge precision | edge recall | ground-truth edges |",
            "| --- | --- | --- | --- |",
        ]
        for lang, m in r["per_language"].items():
            if isinstance(m, dict) and "edge_precision" in m:
                L.append(f"| {lang} | {m['edge_precision']} | {m['edge_recall']} | {m.get('ground_truth_edges', '')} |")

    # Code parse accuracy, per language
    r = b.get("code_parse_impact", {}).get("result")
    if r and isinstance(r.get("parsing"), dict):
        L += [
            "",
            "## Code parse accuracy per language",
            "",
            "Corpus: `tests/code/corpus/langs`, at least two hand-labelled files per "
            "language, with the ground truth in "
            "`tests/code/corpus/language_ground_truth.json`. The metric is symbol "
            "(kind, name) multiset precision and recall; module symbols are excluded "
            "because every file has exactly one. A language whose optional grammar is "
            "not installed here is reported not measured, never as a zero.",
            "",
            "Fidelity says how the language is read. `grammar` is a real Tree-sitter "
            "parse of the whole file. `composite` means the file is unwrapped first "
            "(a notebook's code cells, a component's script block, an infrastructure "
            "file's block structure) and its code is then parsed with another "
            "language's grammar. `fallback` is the documented pattern extractor, "
            "used where no dedicated permissive grammar package is installable. Two "
            "different situations report `fallback` and they should not be confused. "
            "Five languages (R, GDScript, ReScript, VB.NET, Perl) have a grammar "
            "inside the multi-grammar bundle the `code-bundle` extra installs, whose "
            "every grammar is licence-audited into docs/grammar_bundle_licences.json; "
            "they report `grammar` where that extra is present and `fallback` where "
            "it is not, and the row above says which actually ran. Perl XS is the "
            "other situation: no permissive Tree-sitter grammar for .xs exists in any "
            "source available to this project, including that bundle, so it is always "
            "the pattern extractor and no extra changes that. A fallback result is "
            "never presented as though the language had been fully parsed, and every "
            "edge leaving such a file is confidence-scaled.",
            "",
            "This corpus was written by the same author as the parser and iterated "
            "against it, so a perfect score on it is weaker evidence than it looks. "
            "The held-out figures in the next section are the stronger number.",
            "",
            "| Language | fidelity | precision | recall | labelled symbols | files | status |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for lang, m in sorted(r["parsing"].items()):
            if m.get("status") == "measured":
                L.append(
                    f"| {lang} | {m.get('fidelity', 'grammar')} | {m['precision']} | "
                    f"{m['recall']} | {m['expected']} | {m.get('files', '')} | measured |"
                )
            else:
                L.append(
                    f"| {lang} | {m.get('fidelity', '')} | | | | | "
                    f"{m.get('reason', m.get('status', 'not measured'))} |"
                )
        held_out = r.get("parsing_held_out")
        if held_out:
            summary = held_out.get("summary", {})
            L += [
                "",
                "## Code parse accuracy on the held-out corpus",
                "",
                "Corpus: `tests/code/corpus/hard`, constructs a definition-shaped parser "
                "finds hard (nested and anonymous definitions, generics, extensions, "
                "metaclasses, records, enums with bodies, function-valued bindings, "
                "singleton classes). These files were written and labelled before they "
                "were ever parsed. Ground truth: "
                "`tests/code/corpus/hard_ground_truth.json`.",
                "",
                f"Micro precision {summary.get('micro_precision')}, micro recall "
                f"{summary.get('micro_recall')} over "
                f"{summary.get('labelled_symbols')} labelled symbols in "
                f"{summary.get('languages_measured')} languages. Name-only macro "
                f"precision {summary.get('macro_name_precision')}, recall "
                f"{summary.get('macro_name_recall')}: the name-only figures exist "
                "because which symbol kind a construct deserves is debatable across "
                "languages, and a kind disagreement should not read as a missed symbol.",
                "",
                "The parser WAS changed in response to this corpus. That is the point of "
                "it, and it means the figure above is not independent evidence about the "
                "constructs it exposed. The first measurement, taken before any of those "
                "changes, is the independent one, and it is published here rather than "
                "left in a file: micro precision 0.956, micro recall 0.8529. Every "
                "measurement point since, and what changed between them, is recorded in "
                "`test-evidence/held_out_first_measurement.json`. This project squashes "
                "onto its published branch, so the commit history cannot corroborate the "
                "ordering; the retained figures are offered as a record, not as proof.",
                "",
                "| Language | precision | recall | name precision | name recall | missed |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
            for lang, m in sorted(held_out.get("languages", {}).items()):
                if m.get("status") != "measured":
                    L.append(f"| {lang} | | | | | {m.get('status')} |")
                    continue
                missed = ", ".join(m.get("missed", [])) or "none"
                L.append(
                    f"| {lang} | {m['precision']} | {m['recall']} | "
                    f"{m['name_precision']} | {m['name_recall']} | {missed} |"
                )

    # Token efficiency
    r = b.get("token_efficiency", {}).get("result")
    if r:
        c = r["corpus"]
        s = r["summary"]
        L += [
            "",
            "## Token efficiency (measured on one corpus, not a universal claim)",
            "",
            f"Corpus: {c['files']} generated Python files, {c['bytes']} bytes, "
            f"{c['estimated_tokens']} estimated tokens, {c['symbols_ingested']} symbols and "
            f"{c['edges_ingested']} edges ingested (`{c['path']}`, generated by "
            "generate_corpus.py). Token counts use a documented in-repo estimator, not a "
            "vendor tokenizer; both sides of every comparison use the same estimator, and "
            "character counts are published in the artifact so the ratio can be re-derived.",
            "",
            "The headline figure charges the graph route for reading the source files its "
            "answer names (capped at "
            f"{r['follow_up_file_cap']}), because a structural answer does not remove the "
            "need to read code. A ratio below 1.0 means the graph route cost MORE than "
            "simply supplying the whole corpus.",
            "",
            "| Question | full-corpus tokens | graph answer plus sources | ratio | files named | ratio if every named file is read |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
        for m in r["measurements"]:
            L.append(
                f"| {m['name']} | {m['full_corpus']['tokens']} | "
                f"{m['graph_plus_sources']['tokens']} | {m['token_ratio_vs_graph_plus_sources']} | "
                f"{m.get('files_named', '')} | {m.get('token_ratio_uncapped', '')} |"
            )
        L += [
            "",
            f"Mean ratio across {s['questions']} questions: "
            f"{s['mean_token_ratio_graph_plus_sources']} "
            f"(range {s['min_token_ratio_graph_plus_sources']} to "
            f"{s['max_token_ratio_graph_plus_sources']}). Answer-only mean, which ignores "
            f"the cost of reading any code, is {s['mean_token_ratio_answer_only']}. "
            f"The last column removes the file cap entirely and charges for every "
            f"file an answer names; on that basis the mean is "
            f"{s.get('mean_token_ratio_uncapped')}. The cap flatters the graph "
            f"route whenever an answer names more files than the cap allows, and "
            f"blast-radius on a hub names the entire corpus, so its true cost is "
            f"worse than the capped figure already shows.",
        ]
        if r.get("scaling"):
            L += [
                "",
                "The ratio depends on corpus size, and that dependence is measured rather "
                "than asserted. The same questions run against smaller slices of the same "
                "corpus:",
                "",
                "| Leaf modules | Files | Corpus tokens | Mean ratio |",
                "| ---: | ---: | ---: | ---: |",
            ]
            for sc in r["scaling"]:
                L.append(
                    f"| {sc['leaf_modules']} | {sc['files']} | {sc['corpus_tokens']} "
                    f"| {sc['mean_token_ratio_graph_plus_sources']} |"
                )
            L += [
                "",
                "On this corpus the graph route only becomes cheaper than supplying every "
                "file once the corpus is large enough. A repository small enough to fit "
                "comfortably in a context window does not need a graph to save tokens.",
            ]

    # Token cost across the four tasks
    r = b.get("token_cost", {}).get("result")
    if r:
        agg = r["aggregate"]
        gv = r["graph_vs_strong"]
        L += [
            "",
            "## Token cost across four tasks, against a competent baseline",
            "",
            f"Tokenizer: {r['tokenizer']['tokenizer']}. Costs are input tokens at the rates in "
            f"the price table recorded on {r['pricing']['rates_recorded_on']} "
            f"({r['pricing']['input_usd_per_mtok']} USD per million input tokens, "
            f"{r['pricing']['tier']} tier). The price table is configuration, not a "
            "measurement: substitute your own rate and redo the multiplication.",
            "",
            "Three routes over the same corpus, counted the same way:",
            "",
            "- **naive**: hand over every file. An upper bound only. Nobody competent works "
            "this way, so beating it proves little.",
            "- **strong**: a competent agent without a graph. Grep for the query terms, rank "
            "files by RARITY-WEIGHTED match count (inverse document frequency, the same kind of "
            "ranking the graph route's own hybrid search gets), read the top 12 whole. This is "
            "the baseline that matters. An earlier version of this benchmark summed raw match "
            "counts, which let one common term bury the answer document and made the graph look "
            "better than it is.",
            "- **graph**: the graph route with the context levers applied.",
            "",
            "Correctness is recall against a required set known by construction, never an "
            "LLM judgement. A route that saves tokens by answering less scores lower, and "
            "that is not reported as a saving.",
            "",
            "| Route | Tokens | Cost USD | Mean correctness |",
            "| --- | ---: | ---: | ---: |",
        ]
        for route in ("naive", "strong", "graph"):
            m = agg[route]
            L.append(f"| {route} | {m['tokens']} | {m['cost_usd']} | {m['mean_correctness']} |")
        L += [
            "",
            f"**The graph route beat the strong baseline on tokens, with correctness held, in "
            f"{gv['won_count']} of {gv['task_count']} tasks.** Overall it costs MORE tokens "
            f"than the strong baseline ({agg['graph']['tokens']} against "
            f"{agg['strong']['tokens']}) while being substantially more correct "
            f"({agg['graph']['mean_correctness']} against {agg['strong']['mean_correctness']}). "
            "The strong baseline is cheap because it is incomplete, not because it is "
            "efficient. Read the two columns together or not at all.",
            "",
            "| Task | Result against the strong baseline |",
            "| --- | --- |",
        ]
        for w in gv["won_on_tokens_with_correctness_held"]:
            L.append(
                f"| {w['task']} | won: {w['tokens_saved']} tokens saved "
                f"({w['tokens_saved_pct']}%), correctness {w['correctness_baseline']} to "
                f"{w['correctness_candidate']} |"
            )
        for w in gv["did_not_win"]:
            # A task can fail to win two different ways, and calling both "did
            # not win on tokens" contradicts the number printed beside it when
            # the route was cheaper but less correct.
            if not w.get("correctness_held"):
                verdict = (
                    f"lost on CORRECTNESS: {w['correctness_baseline']} to "
                    f"{w['correctness_candidate']}, so its {w['tokens_saved']}-token "
                    "saving is the cost of answering less, not a win"
                )
            else:
                verdict = (
                    f"lost on tokens: {w['tokens_saved']} ({w['tokens_saved_pct']}%), "
                    f"correctness {w['correctness_baseline']} to {w['correctness_candidate']}"
                )
            L.append(f"| {w['task']} | {verdict} |")
        L += ["", f"Limitation: {r['why']['limitation']}"]

    # Media
    for key, heading in [("media_ocr_asr", "## Media OCR and ASR"), ("media_enrichment", "## Media enrichment")]:
        e = b.get(key, {})
        if e.get("status") == "ran":
            L += ["", heading, "", "```json", json.dumps(e["result"], indent=2, sort_keys=True), "```"]
        elif e.get("status"):
            L += ["", heading, "", f"Status: {e['status']}. {e.get('reason', '')}"]

    L.append("")
    return "\n".join(L)


def main() -> int:
    parser = argparse.ArgumentParser(description="Reproducible benchmark harness")
    parser.add_argument("--list", action="store_true", help="list benchmarks and exit")
    args = parser.parse_args()
    if args.list:
        for name, module_name, _fn, out_file, title in BENCHMARKS:
            print(f"  {name:18} {title}  ({module_name}.py -> {out_file})")
        return 0

    EVIDENCE.mkdir(parents=True, exist_ok=True)
    summary = build()
    (EVIDENCE / "benchmarks.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    (ROOT / "docs" / "BENCHMARKS.md").write_text(_render_markdown(summary), encoding="utf-8")
    print("wrote test-evidence/benchmarks.json and docs/BENCHMARKS.md")
    print(f"seed PYTHONHASHSEED={SEED}")
    for name, e in summary["benchmarks"].items():
        print(f"  {name:18} {e['status']}")
    return 0


if __name__ == "__main__":
    _bootstrap()
    raise SystemExit(main())

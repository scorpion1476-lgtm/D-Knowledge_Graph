#!/usr/bin/env python3
"""Measure full-corpus tokens against graph-query tokens for review questions.

The claim under test is narrow and stated as such: on THIS corpus, answering a
review-style question by handing over every source file costs a certain number
of tokens, and answering it by querying the code graph costs fewer. It is not a
universal claim. The ratio depends directly on corpus size, because the
full-corpus cost grows with the repository while a graph answer stays bounded by
what the question actually touches. A repository small enough to fit in a
context window comfortably does not need a graph at all.

Three figures are published per question, not one:

1. ``full_corpus``: every source file in the corpus, which is what you must
   supply when you do not know where to look.
2. ``graph_answer``: the serialised result the graph tool returns.
3. ``graph_plus_sources``: the graph answer PLUS the full text of the files it
   names, capped. This is the honest number for a reviewer who reads the code
   the analysis points at, and it is the one to quote. Reporting only figure 2
   would flatter the result by pretending a structural answer removes the need
   to read any code.

Token counts come from a documented in-repo estimator, not a vendor tokenizer,
because depending on one would add a network fetch or a dependency and would tie
a published number to a third party's versioning. Character counts are published
alongside every token count so a reader can apply their own tokenizer ratio, and
because both sides of each comparison use the same estimator the RATIO is
substantially estimator-independent. The character ratio is published too, so
that can be checked rather than taken on trust.

Writes test-evidence/token_efficiency.json. No forced green.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "code" / "corpus" / "tokens"
OUT = ROOT / "test-evidence" / "token_efficiency.json"

# Estimator rule, stated so the number can be reproduced by hand:
#   - a run of up to 4 letters is one token (approximating how subword
#     tokenizers break long identifiers such as core_util_0 into pieces),
#   - each digit is one token,
#   - each newline is one token,
#   - every other non-whitespace character is one token,
#   - other whitespace is not counted, since tokenizers fold it into neighbours.
_TOKEN_RE = re.compile(r"[A-Za-z]{1,4}|[0-9]|\n|[^\sA-Za-z0-9]")

ESTIMATOR_RULE = (
    "runs of up to four letters, single digits, newlines, and single "
    "non-alphanumeric characters each count as one token; other whitespace is "
    "not counted"
)

# How many of the files an answer names a reviewer is assumed to open. Bounded
# so the follow-up cost cannot silently grow to the whole corpus.
FOLLOW_UP_FILE_CAP = 10

# Leaf-module count in the committed corpus, mirrored from its generator.
MODULES_IN_CORPUS = 30


def estimate_tokens(text: str) -> int:
    return len(_TOKEN_RE.findall(text))


def _measure(text: str) -> dict:
    return {"tokens": estimate_tokens(text), "characters": len(text)}


def _corpus_files() -> list[Path]:
    return sorted(p for p in CORPUS.glob("*.py") if p.name != "generate_corpus.py")


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True, timeout=60)


def _stage_repo(files: list[Path], dst: Path) -> Path:
    """Copy the corpus into a throwaway git repository and commit it.

    Code ingestion walks the repository through git, so a corpus sitting
    untracked inside the project tree would ingest zero files and the benchmark
    would silently measure nothing. Staging a real repository makes the
    measurement independent of whether the corpus happens to be committed.
    """
    dst.mkdir(parents=True, exist_ok=True)
    for p in files:
        shutil.copy(p, dst / p.name)
    _git(dst, "init", "-q")
    _git(dst, "add", "-A")
    _git(dst, "-c", "user.email=benchmark@localhost", "-c", "user.name=benchmark", "commit", "-qm", "corpus")
    return dst


def _corpus_text(files: list[Path]) -> str:
    # A real prompt would carry a path header per file; include it so the
    # baseline is not artificially low.
    parts = []
    for p in files:
        parts.append(f"# file: {p.name}\n{p.read_text(encoding='utf-8')}")
    return "\n".join(parts)


def _named_paths(payload: object, found: set[str] | None = None) -> set[str]:
    """Collect the corpus file paths an answer refers to.

    Canonical names are ``path::symbol``, so the file an answer points at is
    recoverable from the answer itself without another query.
    """
    found = set() if found is None else found
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in ("canonical", "from", "to", "subject") and isinstance(value, str):
                for part in value.split("->"):
                    head = part.strip().split("::")[0].strip()
                    if head.endswith(".py"):
                        found.add(head)
            else:
                _named_paths(value, found)
    elif isinstance(payload, list):
        for item in payload:
            _named_paths(item, found)
    return found


def _follow_up_text(paths: set[str], files: list[Path], *, cap: int | None = FOLLOW_UP_FILE_CAP) -> tuple[str, list[str], int]:
    """The text of the corpus files an answer names, bounded by the cap.

    ``cap=None`` charges for every named file, which is the honest upper bound
    when an answer points at most of the repository.
    """
    available = {p.name: p for p in files}
    present = sorted(n for n in paths if n in available)
    chosen = present if cap is None else present[:cap]
    omitted = max(0, len(present) - len(chosen))
    parts = [f"# file: {n}\n{available[n].read_text(encoding='utf-8')}" for n in chosen]
    return "\n".join(parts), chosen, omitted


def _questions(db):
    """The review-style questions, each paired with the graph call that answers it."""
    from dkg.code.centrality import hubs_and_bridges
    from dkg.code.flow import execution_flow
    from dkg.code.gaps import knowledge_gaps
    from dkg.code.impact import blast_radius
    from dkg.code.review import review_questions

    return [
        (
            "blast_radius",
            "If I change core_util_0, what else is affected?",
            lambda: blast_radius(db, "core.py::core_util_0", depth=5, max_nodes=500),
        ),
        (
            "execution_flow",
            "What does mod_00_run end up calling?",
            lambda: execution_flow(db, "mod_00.py::mod_00_run", depth=5, max_nodes=500),
        ),
        (
            "chokepoints",
            "Which symbols are the architectural chokepoints in this repository?",
            lambda: hubs_and_bridges(db, limit=10),
        ),
        (
            "untested_hotspots",
            "Which heavily used symbols have no test covering them?",
            lambda: knowledge_gaps(db, limit=10),
        ),
        (
            "review_questions",
            "What should I be asking about in review of this repository?",
            lambda: review_questions(db, limit=10),
        ),
    ]


def _subset(files: list[Path], leaf_modules: int) -> list[Path]:
    """Core, every layer, and the first N leaf modules with their tests.

    Truncating the leaf modules keeps the subset internally consistent: every
    remaining module still imports a layer that is present, and every layer
    still imports core.
    """
    keep: list[Path] = []
    for p in files:
        name = p.name
        if name == "core.py" or name.startswith("layer_"):
            keep.append(p)
            continue
        stem = name[:-3]
        digits = "".join(ch for ch in stem if ch.isdigit())
        if digits and int(digits) < leaf_modules:
            keep.append(p)
    return sorted(keep)


def _measure_scale(files: list[Path]) -> tuple[list[dict], dict, dict]:
    """Run every question against a graph built from exactly these files."""
    from dkg.code.ingest import ingest_repo
    from dkg.core.db import open_database

    full = _measure(_corpus_text(files))
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        repo = _stage_repo(files, tdp / "repo")
        with open_database(tdp / "graph.sqlite") as db:
            ingested = ingest_repo(db, repo, audit_path=tdp / "audit.log", incremental=False)
            if not ingested.get("nodes"):
                # Fail loud. A zero-node graph would make every answer empty and
                # produce a spectacular, meaningless ratio.
                raise RuntimeError(
                    f"token-efficiency corpus ingested no symbols from {repo}; refusing to report a ratio"
                )
            measurements = []
            for name, question, call in _questions(db):
                answer = call()
                answer_text = json.dumps(answer, indent=2, sort_keys=True)
                graph = _measure(answer_text)
                named = _named_paths(answer)
                follow_text, opened, omitted = _follow_up_text(named, files)
                combined = _measure(answer_text + "\n" + follow_text)
                all_text, _all_chosen, _z = _follow_up_text(named, files, cap=None)
                uncapped_measure = _measure(answer_text + "\n" + all_text)
                measurements.append(
                    {
                        "name": name,
                        "question": question,
                        "full_corpus": full,
                        "graph_answer": graph,
                        "graph_plus_sources": combined,
                        "files_opened": len(opened),
                        "files_omitted_by_cap": omitted,
                        "files_named": len(named),
                        # The cap flatters the graph route whenever an answer
                        # names more files than the cap allows, because the
                        # baseline is always the WHOLE corpus while the graph
                        # route is charged for at most the cap. The uncapped
                        # figure is published next to it so the size of that
                        # effect is visible rather than buried in a footnote.
                        "graph_plus_all_named_sources": uncapped_measure,
                        "token_ratio_uncapped": _ratio(full["tokens"], uncapped_measure["tokens"]),
                        "token_ratio_vs_graph_answer": _ratio(full["tokens"], graph["tokens"]),
                        "token_ratio_vs_graph_plus_sources": _ratio(full["tokens"], combined["tokens"]),
                        "character_ratio_vs_graph_plus_sources": _ratio(
                            full["characters"], combined["characters"]
                        ),
                    }
                )
    return measurements, full, dict(ingested)


def run() -> dict:
    files = _corpus_files()
    if not files:
        raise FileNotFoundError(
            f"token-efficiency corpus is missing: {CORPUS}. Run its generate_corpus.py."
        )
    structure = json.loads((CORPUS / "structure.json").read_text(encoding="utf-8"))
    measurements, full, ingested = _measure_scale(files)

    # The size dependence is the most important caveat on this benchmark, so it
    # is measured rather than merely asserted: the same questions are run
    # against smaller slices of the same corpus and the ratios are reported side
    # by side.
    scaling = []
    for leaf_modules in (5, 15, MODULES_IN_CORPUS):
        subset = _subset(files, leaf_modules)
        if len(subset) >= len(files) and leaf_modules != MODULES_IN_CORPUS:
            continue
        sub_measurements, sub_full, _ = _measure_scale(subset)
        ratios = [m["token_ratio_vs_graph_plus_sources"] for m in sub_measurements]
        scaling.append(
            {
                "leaf_modules": leaf_modules,
                "files": len(subset),
                "corpus_tokens": sub_full["tokens"],
                "mean_token_ratio_graph_plus_sources": _mean(ratios),
                "per_question": {
                    m["name"]: m["token_ratio_vs_graph_plus_sources"] for m in sub_measurements
                },
            }
        )

    headline = [m["token_ratio_vs_graph_plus_sources"] for m in measurements]
    answer_only = [m["token_ratio_vs_graph_answer"] for m in measurements]
    return {
        "corpus": {
            "path": "tests/code/corpus/tokens",
            "generator": structure["generator"],
            "files": len(files),
            "bytes": sum(p.stat().st_size for p in files),
            "characters": full["characters"],
            "estimated_tokens": full["tokens"],
            "symbols_ingested": ingested.get("nodes"),
            "edges_ingested": ingested.get("edges"),
        },
        "scaling": scaling,
        "estimator": {
            "kind": "in-repo deterministic estimator, not a vendor tokenizer",
            "rule": ESTIMATOR_RULE,
            "note": (
                "Both sides of every comparison use this estimator, so the ratio "
                "is substantially independent of it. Character counts and the "
                "character ratio are published so that can be verified."
            ),
        },
        "follow_up_file_cap": FOLLOW_UP_FILE_CAP,
        "measurements": measurements,
        "summary": {
            "questions": len(measurements),
            "mean_token_ratio_graph_plus_sources": _mean(headline),
            "min_token_ratio_graph_plus_sources": min(headline) if headline else None,
            "max_token_ratio_graph_plus_sources": max(headline) if headline else None,
            "mean_token_ratio_answer_only": _mean(answer_only),
            "mean_token_ratio_uncapped": _mean([m["token_ratio_uncapped"] for m in measurements]),
            "headline_metric": "token_ratio_vs_graph_plus_sources",
        },
        "why": {
            "scope": (
                "Measured on this corpus only. The ratio scales with corpus "
                "size: the full-corpus cost grows with the repository while a "
                "graph answer stays bounded by what the question touches. This "
                "is not a universal claim and must not be quoted as one."
            ),
            "headline": (
                "The figure to quote is token_ratio_vs_graph_plus_sources, which "
                "charges the graph route for reading the files its answer names. "
                "The answer-only ratio is reported for completeness and flatters "
                "the graph route by assuming no code is read at all."
            ),
            "limitation": (
                "Answer quality is not measured here, only size. The accuracy of "
                "the underlying analyses is measured separately, and those "
                "analyses are structural and over-approximate."
            ),
        },
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    if not denominator:
        return None
    return round(numerator / denominator, 4)


def _mean(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return round(sum(present) / len(present), 4)


def main() -> int:
    import sys

    src = str(ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    result = run()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    c = result["corpus"]
    s = result["summary"]
    print(f"corpus: {c['files']} files, {c['bytes']} bytes, {c['estimated_tokens']} estimated tokens")
    for m in result["measurements"]:
        print(
            f"  {m['name']:20} full={m['full_corpus']['tokens']:6} "
            f"graph+sources={m['graph_plus_sources']['tokens']:6} "
            f"ratio={m['token_ratio_vs_graph_plus_sources']}"
        )
    print(f"mean ratio (graph plus sources): {s['mean_token_ratio_graph_plus_sources']}")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

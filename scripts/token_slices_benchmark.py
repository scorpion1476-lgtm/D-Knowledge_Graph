#!/usr/bin/env python3
"""Per-question token cost of answer-shaped graph slices against two baselines.

What was wrong with the previous measurement, stated plainly. It compared the
whole corpus against a graph answer PLUS the full text of every file that answer
named. On a 24 KB corpus the graph route lost. Both halves of that were fair, and
together they measured the wrong thing: a 24 KB repository fits in a context
window and needs no graph at all, and charging the graph route for whole FILES
when the question is about SYMBOLS charges it for the very thing a graph exists
to avoid.

This measures the corrected claim on a corpus where the question is real:

* **Baseline A, naive whole corpus.** Every source file. What you must supply
  when you do not know where to look. Honest but not what a competent agent
  actually does.
* **Baseline B, grep and read.** The strong baseline, and the one that matters:
  grep the corpus for the symbol, then read every file that matched, in full.
  This is what a capable agent with no graph does, and it is much better than
  baseline A. Beating it is the claim worth making.
* **Graph route, answer-shaped slices.** One slice per relevant SYMBOL, each
  reduced to its declaration plus the lines bearing on the question, ranked and
  packed into a token budget. No whole files anywhere.

Every side is tokenised by the SAME real tokenizer (``dkg.context.tokens``,
which loads the pre-staged vocabulary local-files-only), and the tokenizer
actually used is recorded in the output. A number measured with the fallback
estimator is labelled as such and is not a tokenizer count.

Correctness is not assumed. Every question has ground truth known by
construction from the corpus generator, and each route is scored for whether its
answer actually contains the required symbols. A cheap answer that loses the
required symbols is a worse answer, not a better one, and is reported as such:
a route that does not reach full recall has its reduction reported next to that
recall rather than on its own.

Writes test-evidence/token_slices.json. Publishes what reproduces; no forced
green.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

CORPUS = ROOT / "tests" / "code" / "corpus" / "large" / "code"
GROUND_TRUTH = ROOT / "tests" / "code" / "corpus" / "large" / "ground_truth.json"
OUT = ROOT / "test-evidence" / "token_slices.json"

# The token budget the graph route is held to. Chosen once, applied to every
# question, and published: tuning it per question would make the result a
# property of the tuning rather than of the method.
TOKEN_BUDGET = 6000

# Traversal depth for the slice route, matching what the equivalent structural
# query would use, so the comparison is not won by looking at less of the graph.
DEPTH = 3


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True, timeout=120)  # noqa: S603, S607


def _stage_repo(files: list[Path], dst: Path, base: Path | None = None) -> Path:
    """Copy the corpus into a throwaway git repository and commit it.

    Ingestion walks the repository through git, so an untracked corpus would
    ingest zero files and the benchmark would silently measure nothing.

    ``base`` preserves the directory structure relative to it. Without that a
    nested tree flattens and every ``__init__.py`` overwrites the last one, so
    the graph would be built from a fraction of the corpus while the baselines
    were measured over all of it. That would silently flatter the graph route.
    """
    dst.mkdir(parents=True, exist_ok=True)
    for p in files:
        target = dst / (p.relative_to(base) if base else Path(p.name))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(p, target)
    _git(dst, "init", "-q")
    _git(dst, "add", "-A")
    _git(
        dst,
        "-c",
        "user.email=benchmark@localhost",
        "-c",
        "user.name=benchmark",
        "commit",
        "-qm",
        "corpus",
    )
    return dst


def _corpus_files() -> list[Path]:
    return sorted(p for p in CORPUS.glob("*.py"))


def _file_block(path: Path, base: Path | None = None) -> str:
    """One file as it would appear in a prompt, with the header a real prompt carries."""
    name = path.relative_to(base) if base else path.name
    return f"# file: {name}\n{path.read_text(encoding='utf-8')}"


def naive_whole_corpus(files: list[Path], base: Path | None = None) -> str:
    return "\n".join(_file_block(p, base) for p in files)


def grep_and_read(files: list[Path], symbol: str, base: Path | None = None) -> tuple[str, list[str]]:
    """The strong baseline: grep for the symbol, then read every matching file.

    Implemented directly rather than by shelling out, so the result does not
    depend on which grep is installed. Matching is on the bare symbol name as a
    word, which is what an agent would search for.
    """
    name = symbol.split("::")[-1].split(".")[-1]
    pattern = re.compile(rf"\b{re.escape(name)}\b")
    matched = [p for p in files if pattern.search(p.read_text(encoding="utf-8"))]
    return "\n".join(_file_block(p, base) for p in matched), [p.name for p in matched]


def _symbols_in(text: str) -> set[str]:
    """Symbol names a piece of text actually contains, for recall scoring."""
    return set(re.findall(r"\b\w+\b", text))


def _recall(text: str, required: list[str]) -> float:
    """Fraction of the required symbols the answer text actually names."""
    if not required:
        return 1.0
    present = _symbols_in(text)
    hit = sum(1 for r in required if r.split("::")[-1].split(".")[-1] in present)
    return hit / len(required)


def _questions(truth: dict) -> list[dict]:
    """Questions whose true answer is known by construction from the generator.

    Each names a seed symbol, the relation that answers it, the traversal depth
    the question actually implies, and the FULL set of symbols the answer must
    contain. Nothing here is hand-labelled or model-judged: every required set is
    read from the generator's own record or derived from the shape it documents.

    Depth is per question and matters. "What calls X" asks for direct callers, so
    it is a depth-1 question; answering it at depth 3 would return the transitive
    closure, which is a different and much larger answer, and comparing that
    against a grep for X would be comparing two different questions.
    """
    layers = int(truth.get("layers", 8))
    impact = truth.get("impact", {})
    out: list[dict] = []

    # Transitive impact. The true set is the generator's own record.
    for seed in sorted(impact, key=lambda k: (-len(impact[k]), k))[:2]:
        out.append(
            {
                "name": f"impact:{seed.split('::')[-1]}",
                "question": f"If I change {seed.split('::')[-1]}, what else is affected?",
                "seed": seed,
                "relation": "impact",
                "depth": 3,
                "required": sorted(impact[seed]),
            }
        )

    # Direct callers, derived from the shape the generator documents: every
    # layer's three step functions call core_util_0, and so does core_entry;
    # every layer's gateway calls core_entry.
    out.append(
        {
            "name": "callers:core_util_0",
            "question": "What calls core_util_0?",
            "seed": "core.py::core_util_0",
            "relation": "callers",
            "depth": 1,
            "required": sorted(
                [f"layer_{i}.py::layer_{i}_step_{j}" for i in range(layers) for j in range(3)]
                + ["core.py::core_entry"]
            ),
        }
    )
    out.append(
        {
            "name": "callers:core_entry",
            "question": "What calls core_entry?",
            "seed": "core.py::core_entry",
            "relation": "callers",
            "depth": 1,
            "required": sorted(f"layer_{i}.py::layer_{i}_gateway" for i in range(layers)),
        }
    )

    # Forward flow. core_entry calls every core utility, by construction.
    out.append(
        {
            "name": "callees:core_entry",
            "question": "What does core_entry end up calling?",
            "seed": "core.py::core_entry",
            "relation": "callees",
            "depth": 2,
            "required": sorted(
                f"core.py::core_util_{i}" for i in range(int(truth.get("core_utils", 10)))
            ),
        }
    )
    return out


def real_corpus_questions(files: list[Path]) -> list[dict]:
    """Direct-caller questions over real code, with ground truth from Python's own AST.

    The synthetic corpus proves the mechanism on a shape chosen to have a clean
    answer. That is weak evidence on its own, because the shape was chosen. This
    runs the same comparison over a real, non-generated codebase.

    The codebase is this project's own ``src/dkg``. It is used rather than a
    vendored third-party repository deliberately: vendoring one would put
    somebody else's source in a tree that carries a single non-commercial
    licence, and would need its own attribution, for no gain in realism. The
    limitation of self-measurement is real and is published: this is our code, so
    it is not an independent sample of "code in general".

    The ground truth is NOT taken from the code graph, which would make the
    measurement circular. It is derived from Python's own ``ast`` module: a
    function contains a direct call to the target when an ``ast.Call`` whose
    callee name matches appears inside its body. That is a genuinely independent
    oracle over the same files.
    """
    import ast

    callers: dict[str, set[str]] = {}
    # Only names DEFINED in this corpus can be seeds. Without this the candidate
    # list fills with builtins and standard-library methods (encode, loads,
    # enumerate), which the code graph rightly does not contain, and every
    # question would be skipped.
    defined: dict[str, int] = {}
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defined[node.name] = defined.get(node.name, 0) + 1
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                if not isinstance(inner, ast.Call):
                    continue
                func = inner.func
                name = (
                    func.id
                    if isinstance(func, ast.Name)
                    else func.attr
                    if isinstance(func, ast.Attribute)
                    else None
                )
                # A function calling itself recursively is not an interesting
                # caller of itself, and would make the required set include the
                # seed, which every route returns trivially.
                if name and name != node.name:
                    callers.setdefault(name, set()).add(node.name)

    # Targets with a real but bounded caller set: enough callers that the
    # question is not trivial, few enough that the answer is not the repository.
    # Defined exactly once, so the seed resolves to one symbol and the AST truth
    # is about that symbol rather than about several sharing a name.
    candidates = sorted(
        (
            name
            for name, who in callers.items()
            if 4 <= len(who) <= 25 and not name.startswith("_") and defined.get(name) == 1
        ),
        key=lambda n: (-len(callers[n]), n),
    )
    out: list[dict] = []
    for name in candidates[:4]:
        out.append(
            {
                "name": f"real-callers:{name}",
                "question": f"What calls {name}?",
                "seed": name,
                "relation": "callers",
                "depth": 1,
                "required": sorted(callers[name]),
            }
        )
    return out


def _measure_corpus(
    files: list[Path], questions: list[dict], label: str, base: Path | None = None
) -> dict:
    """Run every question over one corpus and return its measurements.

    Shared by the synthetic and the real-code corpus so both are measured by
    exactly the same code, which is what makes the two results comparable.
    """
    from dkg.code.ingest import ingest_repo
    from dkg.context.slices import DETAIL_LEVELS, answer_slices
    from dkg.context.tokens import count_tokens
    from dkg.core.db import open_database

    naive_text = naive_whole_corpus(files, base)
    naive_tokens = count_tokens(naive_text)

    measurements: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        repo = _stage_repo(files, Path(tmp) / "repo", base=base)
        with open_database(Path(tmp) / "graph.db") as db:
            stats = ingest_repo(db, repo, audit_path=Path(tmp) / "audit.log", incremental=False)
            if not stats.get("nodes"):
                raise RuntimeError(f"{label}: ingestion produced no nodes")
            for q in questions:
                seed = _resolve_seed(db, q["seed"])
                if seed is None:
                    # Reported and skipped, never silently dropped: a question
                    # whose seed is absent would otherwise vanish from the count.
                    measurements.append(
                        {"name": q["name"], "skipped": "seed not present in the graph"}
                    )
                    continue
                q = dict(q, seed=seed)
                grep_text, grep_files = grep_and_read(files, q["seed"], base)
                grep_tokens = count_tokens(grep_text)

                # Measure every detail level rather than one, because the trade
                # between how much of each symbol is returned and how many
                # symbols fit the budget IS the mechanism. Reporting only the
                # default would hide the level at which the answer is complete.
                #
                # Uncapped FIRST. The baselines are uncapped (a grep-and-read
                # returns whatever it returns), so the like-for-like number is
                # the cost of the whole answer, not the cost of as much of it as
                # a budget allowed. The budgeted run is measured too, and
                # reported separately, because what a fixed budget buys is a
                # different and also useful question.
                by_detail: dict[str, dict] = {}
                for detail in DETAIL_LEVELS:
                    result = answer_slices(
                        db,
                        q["seed"],
                        relation=q["relation"],
                        depth=q["depth"],
                        detail=detail,
                        token_budget=None,
                        max_nodes=5000,
                    )
                    if not result.get("found"):
                        print(f"token-slices: seed not in the graph: {q['seed']}", file=sys.stderr)
                        return 2
                    text = result["text"]
                    # Stricter recall for the graph route: over the canonical
                    # names it actually RETURNED, not over its text. Text recall
                    # is the fair cross-route measure (a reader finds the name by
                    # reading), but it could in principle be satisfied by a name
                    # appearing incidentally inside another symbol's excerpt.
                    # Publishing both means the weaker measure cannot be mistaken
                    # for the stronger one.
                    returned = {s["canonical"] for s in result["slices"]}
                    returned_short = {c.split("::")[-1].split(".")[-1] for c in returned}
                    required_short = [
                        r.split("::")[-1].split(".")[-1] for r in q["required"]
                    ]
                    strict = (
                        sum(1 for r in required_short if r in returned_short) / len(required_short)
                        if required_short
                        else 1.0
                    )
                    budgeted = answer_slices(
                        db,
                        q["seed"],
                        relation=q["relation"],
                        depth=q["depth"],
                        detail=detail,
                        token_budget=TOKEN_BUDGET,
                        max_nodes=5000,
                    )
                    budgeted_text = budgeted["text"]
                    by_detail[detail] = {
                        "tokens": count_tokens(text),
                        "slices": result["totals"]["returned"],
                        "matched": result["totals"]["matched"],
                        "traversal_truncated": result["totals"]["traversal_truncated"],
                        "recall_of_required": _recall(text, q["required"]),
                        "recall_of_required_by_returned_symbol": round(strict, 4),
                        "at_fixed_budget": {
                            "token_budget": TOKEN_BUDGET,
                            "tokens": count_tokens(budgeted_text),
                            "slices": budgeted["totals"]["returned"],
                            "omitted": budgeted["totals"]["omitted"],
                            "recall_of_required": _recall(budgeted_text, q["required"]),
                        },
                    }

                # The number worth quoting: the cheapest detail level that keeps
                # every required symbol. A cheaper answer that lost part of the
                # answer is not a better answer, so it is never the headline.
                # Completeness is judged on the STRICT measure, so the quoted
                # figure rests on symbols the route actually returned.
                correct = [
                    (name, m)
                    for name, m in by_detail.items()
                    if m["recall_of_required_by_returned_symbol"] >= 1.0
                ]
                best_name, best = (
                    min(correct, key=lambda kv: kv[1]["tokens"])
                    if correct
                    else (None, None)
                )

                measurements.append(
                    {
                        "name": q["name"],
                        "question": q["question"],
                        "seed": q["seed"],
                        "relation": q["relation"],
                        "depth": q["depth"],
                        "required_symbols": len(q["required"]),
                        "naive_whole_corpus": {
                            "tokens": naive_tokens,
                            "files": len(files),
                            "recall_of_required": _recall(naive_text, q["required"]),
                        },
                        "grep_and_read": {
                            "tokens": grep_tokens,
                            "files": len(grep_files),
                            "recall_of_required": _recall(grep_text, q["required"]),
                        },
                        "graph_slices_by_detail": by_detail,
                        "cheapest_complete_answer": (
                            {
                                "detail": best_name,
                                "tokens": best["tokens"],
                                "slices": best["slices"],
                            }
                            if best
                            else None
                        ),
                        "reduction_vs_naive": (
                            _reduction(naive_tokens, best["tokens"]) if best else None
                        ),
                        "reduction_vs_grep_and_read": (
                            _reduction(grep_tokens, best["tokens"]) if best else None
                        ),
                    }
                )

    return {
        "label": label,
        "corpus": {
            "files": len(files),
            "characters": len(naive_text),
            "tokens": naive_tokens,
            "nodes_ingested": stats.get("nodes"),
            "edges_ingested": stats.get("edges"),
        },
        "measurements": measurements,
        "summary": _summary([m for m in measurements if "skipped" not in m]),
    }


def _resolve_seed(db, seed: str) -> str | None:
    """Canonical name for a seed given either a canonical or a bare symbol name.

    The synthetic corpus names seeds canonically; the real-code questions come
    from an AST walk and know only the bare name. An ambiguous bare name is
    refused rather than guessed, because measuring against whichever definition
    happened to sort first would be measuring the wrong symbol.
    """
    row = db.fetchone(
        "SELECT canonical FROM entities WHERE tenant_id='local' AND kind LIKE 'code:%' "
        "AND canonical=? LIMIT 1;",
        (seed,),
    )
    if row:
        return str(row["canonical"])
    rows = db.fetchall(
        "SELECT canonical FROM entities WHERE tenant_id='local' AND kind LIKE 'code:%' "
        "AND display=? ORDER BY canonical LIMIT 2;",
        (seed,),
    )
    if len(rows) == 1:
        return str(rows[0]["canonical"])
    return None


def _print_corpus(result: dict) -> None:
    print(f"\n  == {result['label']} ==")
    c = result["corpus"]
    print(f"     {c['files']} files, {c['tokens']} tokens, {c['nodes_ingested']} nodes")
    for m in result["measurements"]:
        if "skipped" in m:
            print(f"     {m['name']:30} SKIPPED: {m['skipped']}")
            continue
        best = m["cheapest_complete_answer"]
        if best is None:
            worst = max(d["recall_of_required"] for d in m["graph_slices_by_detail"].values())
            print(f"     {m['name']:30} NO COMPLETE ANSWER (best recall {worst:.2f})")
            continue
        print(
            f"     {m['name']:30} naive={m['naive_whole_corpus']['tokens']:6} "
            f"grep={m['grep_and_read']['tokens']:6} complete={best['tokens']:6} "
            f"({best['detail']}, {best['slices']} slices) "
            f"vs-grep={m['reduction_vs_grep_and_read']:7.1%} "
            f"vs-naive={m['reduction_vs_naive']:6.1%}"
        )
    s = result["summary"]
    print(
        f"     complete {s['questions_answered_completely']}/{s['questions']}; "
        f"cheaper than grep on {s['questions_cheaper_than_grep_and_read']}; "
        f"median vs grep {s['median_reduction_vs_grep_and_read']:.1%}, "
        f"vs naive {s['median_reduction_vs_naive']:.1%}"
    )


def main() -> int:
    from dkg.context.tokens import tokenizer_available, tokenizer_name

    synthetic_files = _corpus_files()
    if not synthetic_files:
        print(f"token-slices: corpus is empty at {CORPUS}", file=sys.stderr)
        return 2
    truth = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
    synthetic_questions = _questions(truth)
    if not synthetic_questions:
        print("token-slices: no questions derived from the ground truth", file=sys.stderr)
        return 2

    real_files = sorted((ROOT / "src" / "dkg").rglob("*.py"))
    real_questions = real_corpus_questions(real_files)
    if not real_questions:
        print("token-slices: no real-code questions derived from the AST", file=sys.stderr)
        return 2

    corpora = [
        _measure_corpus(
            synthetic_files,
            synthetic_questions,
            "synthetic (tests/code/corpus/large/code, ground truth by construction)",
        ),
        _measure_corpus(
            real_files,
            real_questions,
            "real code (src/dkg, ground truth from Python's ast, independent of the graph)",
            base=ROOT / "src",
        ),
    ]

    payload = {
        "tokenizer": {
            "name": tokenizer_name(),
            "is_real_tokenizer": tokenizer_available(),
            "note": (
                "Both sides of every comparison are counted by this one tokenizer. "
                "When is_real_tokenizer is false these are estimator counts and must "
                "not be quoted as token counts."
            ),
        },
        "settings": {"token_budget": TOKEN_BUDGET, "detail_levels": ["signature", "focused", "full"]},
        "corpora": corpora,
        "method": (
            "Three routes per question, all counted by the same real tokenizer. "
            "Naive whole corpus: every file. Grep and read: every file whose text "
            "contains the symbol, in full, which is what a capable agent without a "
            "graph does. Graph slices: one slice per relevant SYMBOL, reduced to its "
            "declaration plus the lines bearing on the question. The quoted figure is "
            "the CHEAPEST detail level that keeps every required symbol, so a saving "
            "bought by losing part of the answer never reaches the headline. The "
            "uncapped cost is what is compared, because the baselines are uncapped "
            "too; what a fixed budget buys is reported separately per detail level."
        ),
        "caveats": [
            "Measured on the two corpora named above. This is not a universal claim.",
            "The reduction depends on corpus size and on the question. A repository "
            "that fits in a context window does not need a graph at all.",
            "The real-code corpus is this project's own source. Self-measurement is a "
            "real limitation: it is not an independent sample of code in general. Its "
            "ground truth is independent of the graph (Python's ast), which is the "
            "part that would otherwise be circular.",
            "The underlying code edges are structural and name-based, so the matched "
            "set is over-approximate: the graph route can return symbols that are not "
            "truly affected. Recall against the required set is reported per question; "
            "precision is not claimed.",
            "Grep-and-read is a strong baseline but not the strongest imaginable: an "
            "agent that greps and then reads only the matching FUNCTIONS rather than "
            "whole files would land between it and the graph route.",
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"token-slices: wrote {OUT}")
    print(f"  tokenizer: {tokenizer_name()} (real={tokenizer_available()})")
    for c in corpora:
        _print_corpus(c)
    return 0


def _reduction(baseline: int, route: int) -> float:
    """Fraction of the baseline's tokens saved. Negative means the route cost more."""
    if baseline <= 0:
        return 0.0
    return round((baseline - route) / baseline, 4)


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _summary(measurements: list[dict]) -> dict:
    """Headline figures, computed only over answers that were actually complete.

    A question the slice route could not answer in full at any detail level
    within the budget contributes to the counts but never to a reduction, so a
    saving bought by dropping part of the answer cannot reach the headline.
    """
    complete = [m for m in measurements if m["cheapest_complete_answer"] is not None]
    wins = [m for m in complete if m["reduction_vs_grep_and_read"] > 0]
    return {
        "questions": len(measurements),
        "questions_answered_completely": len(complete),
        "questions_cheaper_than_grep_and_read": len(wins),
        "median_reduction_vs_naive": _median([m["reduction_vs_naive"] for m in complete]),
        "median_reduction_vs_grep_and_read": _median(
            [m["reduction_vs_grep_and_read"] for m in complete]
        ),
        "worst_reduction_vs_grep_and_read": (
            min(m["reduction_vs_grep_and_read"] for m in complete) if complete else None
        ),
    }


if __name__ == "__main__":
    raise SystemExit(main())

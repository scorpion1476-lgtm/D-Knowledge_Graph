#!/usr/bin/env python3
"""Shared scaffolding for the token-cost benchmark tasks.

Every task measures the same three routes over the same corpus with the same
tokenizer, and scores correctness deterministically against ground truth known
by construction. Nothing here judges an answer with a model.

The three routes:

- ``naive``: hand over every file. The honest upper bound, and clearly labelled
  as such. Nobody competent works this way, so beating it proves little.
- ``strong``: what a competent agent without a graph actually does. Grep the
  corpus for the query terms, rank files by match count, and read the top files
  in full until a file budget is reached. This is the baseline that matters,
  and it is deliberately given a fair shot: the same query terms the graph gets,
  and enough files to usually find the answer.
- ``graph``: the graph route with the context levers applied.

Correctness is a required-set criterion: the ground truth names what MUST appear
in an answer, and a route scores 1.0 only if it produced all of it. Extra output
is not rewarded, and a route that saves tokens by dropping a required item
scores below 1.0 and is reported as a failure, not a saving.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "tests" / "code" / "corpus" / "large"
CODE_DIR = CORPUS / "code"
DOCS_DIR = CORPUS / "docs"

# How many whole files the strong baseline is allowed to read. Chosen so the
# baseline usually succeeds: a baseline tuned to fail would make the comparison
# meaningless. Recorded in the output so it can be challenged.
STRONG_BASELINE_FILE_BUDGET = 12


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True, timeout=120)


@dataclass
class Corpus:
    """The corpus on disk plus its ground truth."""

    code_files: list[Path]
    doc_files: list[Path]
    truth: dict

    @property
    def all_files(self) -> list[Path]:
        return [*self.code_files, *self.doc_files]

    def text_of(self, paths: Sequence[Path]) -> str:
        return "\n".join(f"# file: {p.name}\n{p.read_text(encoding='utf-8')}" for p in paths)

    def naive_text(self) -> str:
        return self.text_of(self.all_files)


def load_corpus() -> Corpus:
    truth_path = CORPUS / "ground_truth.json"
    if not truth_path.is_file():
        raise FileNotFoundError(
            f"large corpus is missing: {CORPUS}. Run its generate_corpus.py first."
        )
    return Corpus(
        code_files=sorted(CODE_DIR.glob("*.py")),
        doc_files=sorted(DOCS_DIR.glob("*.md")),
        truth=json.loads(truth_path.read_text(encoding="utf-8")),
    )


@dataclass
class GraphFixture:
    """A temporary ingested copy of the corpus, cleaned up on exit."""

    tempdir: tempfile.TemporaryDirectory = field(repr=False)
    db: object = None
    stats: dict = field(default_factory=dict)

    def close(self) -> None:
        self.tempdir.cleanup()


def ingest_corpus(corpus: Corpus):
    """Ingest the code corpus into a throwaway graph.

    Code ingestion walks a repository through git, so the corpus is staged as a
    real repository rather than read in place. A corpus that ingested nothing
    would make every graph answer empty and every ratio meaningless, so this
    raises instead.
    """
    from dkg.code.ingest import ingest_repo
    from dkg.core.db import open_database

    tmp = tempfile.TemporaryDirectory()
    tdp = Path(tmp.name)
    repo = tdp / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    for p in corpus.code_files:
        shutil.copy(p, repo / p.name)
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=benchmark@localhost", "-c", "user.name=benchmark", "commit", "-qm", "corpus")

    db = open_database(tdp / "graph.sqlite").__enter__()
    stats = ingest_repo(db, repo, audit_path=tdp / "audit.log", incremental=False)
    if not stats.get("nodes"):
        tmp.cleanup()
        raise RuntimeError("benchmark corpus ingested no symbols; refusing to report a ratio")
    return GraphFixture(tempdir=tmp, db=db, stats=dict(stats))


# -- baselines --------------------------------------------------------------


_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


def query_terms(question: str) -> list[str]:
    """The terms a competent agent would grep for."""
    seen: list[str] = []
    for m in _WORD.finditer(question):
        term = m.group(0)
        if term.lower() in _STOPWORDS or term in seen:
            continue
        seen.append(term)
    return seen


_STOPWORDS = {
    "the", "what", "which", "does", "call", "calls", "and", "for", "are", "is",
    "this", "that", "with", "from", "into", "how", "why", "who", "when", "any",
    "all", "you", "your", "its", "has", "have", "was", "were", "will", "would",
    "should", "could", "about", "there", "their", "them", "they", "then", "than",
}


def _document_frequency(corpus: Corpus, terms: Sequence[str]) -> dict[str, int]:
    """How many files contain each term. Cached per corpus instance."""
    cache = getattr(corpus, "_df_cache", None)
    if cache is None:
        cache = {}
        corpus._df_cache = cache  # type: ignore[attr-defined]
    missing = [t for t in terms if t not in cache]
    if missing:
        for path in corpus.all_files:
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for term in missing:
                if term in text:
                    cache[term] = cache.get(term, 0) + 1
        for term in missing:
            cache.setdefault(term, 0)
    return {t: cache[t] for t in terms}


def strong_baseline_files(corpus: Corpus, question: str, *, budget: int = STRONG_BASELINE_FILE_BUDGET) -> list[Path]:
    """Rank files by rarity-weighted match count and take the top ones, whole.

    This is what an agent without a graph does: search, then open the best
    matches in full because it has no way to slice a file at symbol level.

    Terms are weighted by inverse document frequency, which is what every real
    search tool does and what the graph route's own hybrid search already gets
    through BM25. An earlier version summed raw match counts, and that was not a
    fair baseline: for a question like "what is the retry budget for layer 3",
    the common term "layer" appears thousands of times across the source while
    the rare terms "retry" and "budget" appear only in the one note that holds
    the answer, so the common term buried the answer document and the baseline
    never retrieved it. Ranking the two sides with comparable machinery is the
    difference between a baseline and a straw man.
    """
    terms = query_terms(question)
    if not terms:
        return corpus.all_files[:budget]
    total = max(1, len(corpus.all_files))
    df = _document_frequency(corpus, terms)
    weights = {t: math.log(1.0 + total / (1.0 + df.get(t, 0))) for t in terms}
    scored: list[tuple[float, str, Path]] = []
    for path in corpus.all_files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        # Saturating term frequency, so one term repeated a thousand times
        # cannot outweigh the presence of a rare, discriminating term.
        score = 0.0
        for term in terms:
            count = text.count(term)
            if count:
                score += weights[term] * (1.0 + math.log(count))
        if score:
            scored.append((-score, path.name, path))
    scored.sort()
    return [p for _s, _n, p in scored[:budget]]


def strong_baseline_text(corpus: Corpus, question: str, *, budget: int = STRONG_BASELINE_FILE_BUDGET) -> str:
    return corpus.text_of(strong_baseline_files(corpus, question, budget=budget))


# -- scoring ----------------------------------------------------------------


def required_recall(produced: Sequence[str], required: Sequence[str]) -> float:
    """Fraction of the required items the route actually produced.

    Correctness is recall against a required set, never an LLM judgement. A
    route that omits a required item to save tokens scores below 1.0.
    """
    required_set = set(required)
    if not required_set:
        return 1.0
    produced_blob = "\n".join(produced)
    found = sum(1 for item in required_set if item in produced_blob)
    return round(found / len(required_set), 4)


def contains_all(text: str, required: Sequence[str]) -> float:
    if not required:
        return 1.0
    found = sum(1 for item in required if item in text)
    return round(found / len(required), 4)


# -- result record ----------------------------------------------------------


def route_record(name: str, text: str, *, correctness: float, tier: str = "mid", extra: dict | None = None) -> dict:
    """One route's measured cost and correctness, in the shape the harness expects."""
    from dkg.context.tokens import measure

    record = {"route": name, "correctness": correctness}
    record.update(measure(text, tier=tier))
    if extra:
        record.update(extra)
    return record


def savings(baseline: dict, candidate: dict) -> dict:
    """How much the candidate saved against a baseline, and whether it held up."""
    b_tokens, c_tokens = baseline["tokens"], candidate["tokens"]
    return {
        "tokens_saved": b_tokens - c_tokens,
        "tokens_saved_pct": round(100.0 * (b_tokens - c_tokens) / b_tokens, 2) if b_tokens else None,
        "cost_saved_usd": round(baseline["cost_usd"] - candidate["cost_usd"], 6),
        "ratio": round(b_tokens / c_tokens, 4) if c_tokens else None,
        "correctness_baseline": baseline["correctness"],
        "correctness_candidate": candidate["correctness"],
        "correctness_held": candidate["correctness"] >= baseline["correctness"],
    }

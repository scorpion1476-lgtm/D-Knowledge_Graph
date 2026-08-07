#!/usr/bin/env python3
"""Measure source-code plane accuracy and timings on the retained corpus.

- Parsing: per-language entity-extraction precision and recall of the (kind, name)
  symbol set against the labelled ground truth.
- Incremental: build a temp git repo from the impact corpus, full-ingest, then
  change one file and time the git-incremental re-ingest.
- Blast-radius: ingest the impact corpus and measure structural precision and
  recall of the impacted function set against the known true impact. The metric
  is honestly structural and over-approximate.

Writes test-evidence/code_accuracy.json. No forced green.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "code" / "corpus"
OUT = ROOT / "test-evidence" / "code_accuracy.json"

_IMPACT_KINDS = ("code:function", "code:method", "code:class", "code:type", "code:test")


def _git(cwd: Path, *args: str) -> None:
    cmd = ["git", *args]
    subprocess.run(cmd, cwd=cwd, capture_output=True, check=True, timeout=60)


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=x@x", "-c", "user.name=x", "commit", "-qm", message)


def measure_parsing() -> dict:
    """Per-language symbol precision and recall against the labelled truth.

    Delegates to scripts/language_accuracy.py so there is exactly one measured
    result per language and one corpus behind it. Capability detection is per
    language, not per benchmark: a grammar that is not installed here is
    recorded as not measured, and every language whose grammar IS present is
    still measured. Failing the whole benchmark because one optional grammar is
    missing would hide results that are perfectly valid, and claiming a language
    works without measuring it would be worse.
    """
    import sys as _sys

    _sys.path.insert(0, str(ROOT / "scripts"))
    import language_accuracy

    return language_accuracy.measure()


def _setup_impact_repo(dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for p in sorted((CORPUS / "impact").glob("*.py")):
        shutil.copy(p, dst / p.name)
    _git(dst, "init", "-q")
    _commit(dst, "init")


def measure_incremental() -> dict:
    from dkg.code.ingest import ingest_repo
    from dkg.core.db import open_database

    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        repo = tdp / "repo"
        _setup_impact_repo(repo)
        with open_database(tdp / "g.db") as db:
            t0 = time.perf_counter()
            full = ingest_repo(db, repo, audit_path=tdp / "a.log")
            full_t = time.perf_counter() - t0
            chain = repo / "chain.py"
            chain.write_text(chain.read_text() + "\n\ndef added():\n    return top()\n", encoding="utf-8")
            _commit(repo, "modify chain")
            t1 = time.perf_counter()
            inc = ingest_repo(db, repo, audit_path=tdp / "a.log")
            inc_t = time.perf_counter() - t1
    return {
        "full_ingest_seconds": round(full_t, 4),
        "incremental_seconds": round(inc_t, 4),
        "incremental_files_reparsed": inc["parsed_files"],
        "unchanged_files": inc["unchanged_files"],
        "files_total": full["files"],
    }


def measure_impact() -> dict:
    from dkg.code.impact import blast_radius
    from dkg.code.ingest import ingest_repo
    from dkg.core.db import open_database

    truth = json.loads((CORPUS / "impact_truth.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        repo = tdp / "repo"
        _setup_impact_repo(repo)
        with open_database(tdp / "g.db") as db:
            ingest_repo(db, repo, audit_path=tdp / "a.log")
            r = blast_radius(db, truth["changed_entity"])
            found = {i["canonical"] for i in r["impacted"] if i["kind"] in _IMPACT_KINDS}
    true = set(truth["true_impact_functions"])
    tp = len(found & true)
    return {
        "precision": round(tp / len(found), 4) if found else 0.0,
        "recall": round(tp / len(true), 4) if true else 0.0,
        "true_impact": sorted(true),
        "found_impact": sorted(found),
        "label": "structural and over-approximate; refinements deferred to Wave 4",
    }


def measure_parsing_held_out() -> dict:
    """The held-out hard-construct corpus, scored separately from the main one.

    Kept apart on purpose: the per-language corpus was authored alongside the
    parser, and this one was labelled before it was ever run. Merging them would
    average away the difference between the two strengths of evidence.
    """
    import sys as _sys

    _sys.path.insert(0, str(ROOT / "scripts"))
    import language_accuracy

    languages = language_accuracy.measure_hard()
    return {"summary": language_accuracy.summarise_hard(languages), "languages": languages}


def run() -> dict:
    return {
        "generated_at": "2026-08-06",
        "parsing": measure_parsing(),
        "parsing_held_out": measure_parsing_held_out(),
        "incremental": measure_incremental(),
        "blast_radius": measure_impact(),
    }


def main() -> int:
    result = run()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

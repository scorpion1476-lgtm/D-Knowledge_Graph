#!/usr/bin/env python3
"""Update the Part 1b rows and generate evidence for those rows only.

Deliberately narrow. ``scripts/build_row_evidence.py`` re-runs every row,
including two whose acceptance shells out to ``docker build``, which the Docker
isolation rule forbids here. This touches only the rows this wave changed and
runs each one's real acceptance command, so the evidence is executed rather than
asserted.
"""

from __future__ import annotations

import csv
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "docs" / "REQUIREMENTS_TRACEABILITY_MATRIX.csv"
EVIDENCE_DIR = ROOT / "test-evidence" / "rows"

# id -> (implementation_files, tests, status, remaining_limitation)
UPDATES: dict[str, tuple[str, str, str, str]] = {
    "Q-07": (
        "src/dkg/code/risk.py, src/dkg/mcp/tools.py, src/dkg/cli/entry.py",
        "tests/code/test_risk_score.py",
        "PRODUCTION READY",
        "The five factors are structural and the weights are a stated editorial judgement, not a measurement; they are reported with every result so a reader can disagree with the arithmetic rather than with a black box.",
    ),
    "Q-08": (
        "src/dkg/code/risk.py, src/dkg/cli/entry.py, src/dkg/mcp/tools.py",
        "tests/code/test_churn_signal.py",
        "PRODUCTION READY",
        "Churn is counted over a bounded window of local git history, so a count is comparable only against other counts from the same window. A file that changes often is not thereby worse code, which is why the signal only ever raises a score and is never enabled by default.",
    ),
    "Q-09": (
        "src/dkg/code/cochange.py, src/dkg/cli/entry.py",
        "tests/code/test_cochange_ground_truth.py",
        "PRODUCTION READY",
        "Co-change is evidence from outside the graph but it is not correctness: two files change together for reasons other than dependency, and a real dependency not edited in the window counts as a false positive. Both facts are stated in the result.",
    ),
    "Q-10": (
        "src/dkg/code/deadcode.py, src/dkg/mcp/tools.py, src/dkg/cli/entry.py",
        "tests/code/test_dead_code.py",
        "PRODUCTION READY",
        "Advisory and over-approximate by construction: a candidate is the absence of an edge in a name-based graph. The four false-positive sources are named in every result.",
    ),
    "Q-11": (
        "src/dkg/code/rename.py, src/dkg/mcp/tools.py, src/dkg/cli/entry.py",
        "tests/code/test_rename_preview.py",
        "PRODUCTION READY",
        "Matching is whole-identifier and textual: it does not resolve scope, so a local variable sharing the name inside an attributed file is reported applicable. Only files present in the code graph are scanned.",
    ),
    "Q-12": (
        "src/dkg/code/size.py, src/dkg/code/analysis.py, src/dkg/mcp/tools.py, src/dkg/cli/entry.py",
        "tests/code/test_large_symbols.py",
        "PRODUCTION READY",
        "Length is a smell, not a defect: a long generated table and a long tangled function have the same line count. A symbol whose parser recorded no span is reported unknown rather than ranked as zero.",
    ),
    "Q-13": (
        "src/dkg/code/rename.py, src/dkg/cli/entry.py",
        "tests/code/test_rename_apply.py",
        "PRODUCTION READY",
        "Deliberately command-line only and registered nowhere on the MCP surface, with a test asserting its absence: a write tool behind that boundary would let an agent acting on injected content edit source. Applying leaves the graph stale until re-ingest.",
    ),
    "Q-14": (
        "src/dkg/code/aliases.py, src/dkg/code/graph.py, src/dkg/code/ingest.py",
        "tests/code/test_path_alias_resolution.py",
        "PRODUCTION READY",
        "Reads tsconfig.json and jsconfig.json only. Other build systems declare aliases elsewhere (bundler configs, module-resolution plugins) and are not read, so an alias declared there still fails to resolve.",
    ),
    "Q-15": (
        "src/dkg/code/refactor.py, src/dkg/code/coupling.py, src/dkg/mcp/tools.py, src/dkg/cli/entry.py",
        "tests/code/test_refactor_suggestions.py",
        "PRODUCTION READY",
        "Suggestions, not findings. Each rests on one run of a modularity optimizer over a name-based graph; each carries its own reason for possibly being wrong, and a move fires only when the partition and the neighbourhood actually disagree. Four cuts are derived from this graph's distribution; three (minimum neighbours, majority, plural traffic) are fixed because they are properties of the question rather than of the repository, and all seven are published in the thresholds block.",
    ),
    "N-18": (
        "src/dkg/code/ignores.py, src/dkg/code/changes.py, src/dkg/code/ingest.py",
        "tests/code/test_ignore_file.py",
        "PRODUCTION READY",
        "The supported pattern subset is documented in the module: comments, directory suffixes, basename matching, ** across separators, root anchoring, and negation. Character classes and escaping are not interpreted and simply will not match.",
    ),
    "N-19": (
        "src/dkg/code/changes.py, src/dkg/code/ingest.py",
        "tests/code/test_svn_changes.py",
        "IMPLEMENTED BUT NOT FULLY VERIFIED",
        "HONESTLY NOT FULLY VERIFIED. The Subversion path is implemented and shares the git incremental comparison, and the executed suite drives it end to end through ingest_repo: capability detection, the subprocess invocation, the XML parse, the hash comparison, and re-parsing only what changed. But no Subversion binary exists in this environment, so the suite supplies a stub svn emitting real-format 'svn status -v --xml' output. Every line of product code runs; a real Subversion working copy does not. One test against a real binary is present and skips here.",
    ),
    "N-20": (
        "src/dkg/code/changes.py, src/dkg/code/ingest.py, src/dkg/cli/entry.py",
        "tests/code/test_submodules.py",
        "PRODUCTION READY",
        "Only initialised submodules that are themselves git working trees are collected; an uninitialised one has no content and is skipped rather than promised.",
    ),
    "N-21": (
        "src/dkg/code/forget.py, src/dkg/code/graph.py, src/dkg/cli/entry.py",
        "tests/code/test_forget.py",
        "PRODUCTION READY",
        "Forgetting does not re-resolve the edges of the files that remain: a reference into a forgotten file is gone rather than downgraded, so re-ingest if the path returns.",
    ),
    "N-22": (
        "src/dkg/code/postprocess.py, src/dkg/code/ingest.py, src/dkg/cli/entry.py",
        "tests/code/test_postprocess_stage.py",
        "PRODUCTION READY",
        "The index stage needs a pre-staged embedding model and is reported not run without one, which lowers the applied level below the requested one. That is capability detection, not failure, and the result distinguishes them.",
    ),
    "T-11": (
        "src/dkg/code/wiki.py, src/dkg/cli/entry.py",
        "tests/code/test_wiki.py",
        "PRODUCTION READY",
        "Pages are capped at 300 members and 200 crossing edges and say so when they truncate. The knowledge base describes a partition from one run, and every page carries that caveat.",
    ),
    "T-13": (
        "src/dkg/code/postprocess.py, src/dkg/code/catalogue.py, src/dkg/core/migrations/003_postprocess.sql, src/dkg/mcp/tools.py, src/dkg/cli/entry.py",
        "tests/code/test_flow_catalogue.py",
        "PRODUCTION READY",
        "Flows are traced over call and dispatch edges, so they over-approximate exactly as those edges do, and a flow reached only through a dynamic call is not catalogued at all. A catalogue computed before the graph moved is reported stale rather than served as current.",
    ),
    "U-13": (
        "src/dkg/context/savings.py, src/dkg/mcp/tools.py, src/dkg/cli/entry.py",
        "tests/benchmark/test_context_savings.py",
        "PRODUCTION READY",
        "The headline figures are estimator counts and are labelled ESTIMATED; the real-tokenizer cross-check is opt-in and publishes the calibration error rather than replacing the estimate. The baseline is the files the answer names, which is a harder baseline than the whole repository and a truer one.",
    ),
    "U-16": (
        "src/dkg/code/postprocess.py, src/dkg/code/catalogue.py, src/dkg/core/migrations/003_postprocess.sql, src/dkg/mcp/tools.py, src/dkg/cli/entry.py",
        "tests/benchmark/test_precomputed_summaries.py",
        "PRODUCTION READY",
        "Everything stored is derived and disposable, and a row computed against an earlier graph is reported stale. The opt-in churn signal is deliberately never precomputed, because it is not derived from the graph.",
    ),
    "V-04": (
        "src/dkg/code/config_keys.py, src/dkg/code/ingest.py, src/dkg/code/changes.py, src/dkg/code/graph.py",
        "tests/code/test_config_properties.py",
        "PRODUCTION READY",
        "Binding detection covers the documented forms (environment lookups, config lookups, Spring @Value, Laravel env and config helpers); a key read through a computed string is not linked. The value is discarded at the point of reading and a test asserts it appears nowhere in the database.",
    ),
    "V-05": (
        "src/dkg/code/entrypoints.py, src/dkg/code/parser.py, src/dkg/code/model.py",
        "tests/code/test_entry_point_nodes.py",
        "PRODUCTION READY",
        "Detection is pattern-based, so the entry-point set is a LOWER BOUND rather than a census: a route registered through a variable, built by concatenation, or produced by an unrecognised framework convention is not detected. The recognised set is named in the result.",
    ),
    "F-16": (
        "src/dkg/search/federated.py, src/dkg/mcp/tools.py, src/dkg/watch/registry.py, src/dkg/cli/entry.py",
        "tests/unit/test_cross_repo_search.py",
        "PRODUCTION READY",
        "Cross-repository search runs the keyword ranker per repository and merges by score; it does not build a shared index, so scores are comparable only in the sense that the same ranker produced them. Each database is opened through SQLite's read-only URI with no journalling pragma and no migration runner, so searching neither upgrades a schema nor converts a journal mode; a repository that cannot be read that way is reported rather than opened anyway.",
    ),
}

# Rows whose acceptance test path changed with the row.
ACCEPTANCE: dict[str, str] = {
    "F-16": "pytest tests/unit/test_cross_repo_search.py -q",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run(target: str) -> tuple[int, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header", target],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(ROOT),
        timeout=600,
    )
    return proc.returncode, (proc.stdout + proc.stderr)


def main() -> int:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    with CSV_PATH.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    failures: list[str] = []
    for row in rows:
        rid = (row.get("id") or "").strip()
        if rid not in UPDATES:
            continue
        impl, tests, status, limitation = UPDATES[rid]
        row["implementation_files"] = impl
        row["tests"] = tests
        if rid in ACCEPTANCE:
            row["acceptance_test"] = ACCEPTANCE[rid]
        row["evidence_path"] = f"test-evidence/rows/{rid}.log"
        row["remaining_limitation"] = limitation

        target = tests.split(",")[0].strip()
        code, output = _run(target)
        # The status in the file is the one that was true when the acceptance
        # ran, so a failing run cannot be written up as production ready.
        effective = status if code == 0 else "IMPLEMENTED BUT NOT FULLY VERIFIED"
        if code != 0:
            failures.append(f"{rid}: {target} exited {code}")
        row["status"] = effective

        header = [
            "# D-Knowledge_Graph row evidence",
            f"# row_id: {rid}",
            f"# area: {row.get('area','')}",
            f"# requirement: {row.get('requirement','')}",
            f"# status: {effective}",
            f"# acceptance_test: {row.get('acceptance_test','')}",
            f"# generated_at: {_now()}",
            "# action_kind: pytest",
            f"# command: python -m pytest -q --no-header {target}",
            f"# exit_code: {code}",
            "",
        ]
        (EVIDENCE_DIR / f"{rid}.log").write_text(
            "\n".join(header) + output, encoding="utf-8"
        )
        print(f"{rid}: exit {code} -> {effective}")

    with CSV_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    if failures:
        print("\nFAILURES:")
        for line in failures:
            print(f"  {line}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

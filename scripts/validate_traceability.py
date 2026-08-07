#!/usr/bin/env python3
"""Validate the requirements traceability matrix.

Reads ``docs/REQUIREMENTS_TRACEABILITY_MATRIX.csv`` and enforces:

1. Every row has a stable non-empty ID; no duplicates.
2. Every row has a non-empty ``area``, ``requirement``, ``acceptance_test``,
   and one of the six approved status labels.
3. Every required column is present on every row.
4. Every row's ``implementation_files`` paths resolve on disk (directory and
   glob citations are allowed). A citation that points at a non-existent file
   fails validation.
5. A row may be ``PRODUCTION READY`` only if:
   - all implementation paths resolve,
   - its ``acceptance_test`` is an executable pytest command, and
   - its evidence file exists, records a pass, and is not a manual or
     documentary disclaimer.
   Merely having an evidence file present is not sufficient.
6. The per-status distribution sums to the total row count (EXPECTED_TOTAL).

Modes:
- default: validate and write ``docs/traceability_summary.json``; exit
  non-zero on any failure.
- ``--rederive`` / ``--rederive-write``: recompute each row's status against
  the strict bar, only ever DOWN-labelling a ``PRODUCTION READY`` row that
  fails the bar to ``IMPLEMENTED BUT NOT FULLY VERIFIED``; print the change
  list; with ``-write`` also save the corrected CSV.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "docs" / "REQUIREMENTS_TRACEABILITY_MATRIX.csv"
JSON_OUT = ROOT / "docs" / "traceability_summary.json"

# The authoritative row count. 124 base requirements (areas A-L), 10 media plane
# rows (area M) in Wave 1, 8 source-code plane rows (area N) in Wave 2, 6
# retrieval-and-graph-enrichment rows (area O) in Wave 3a, 4 perception-and-flow
# rows (area P) in Wave 3b, 3 advanced-code-analysis rows (area Q) added in
# Wave 4a, 5 delivery-and-distribution rows (area R) added in Wave 4b, and 4
# evidence-and-reproducibility rows (area S) added in Wave 5. The end-to-end
# finalization adds 12 token-cost-and-context-lever rows (area U), plus one
# retrieval row for the combined detector default, one supply-chain row for the
# repository-wide licence, and one evidence row for the documentation-count
# guard. The gap-closing
# wave adds 8 graph-analysis-and-review rows (area T), 1 more source-code-plane
# row for the wider language set, 1 more delivery row for the configuration
# helper, and 2 more evidence rows for token efficiency and the scrub scan.
# The contradiction-and-licence fix round adds 2 more evidence rows: the
# held-out contradiction benchmark and the pre-publish every-ref scrub gate.
# The parity capture adds 69 rows recording every capability gap found by
# comparing the platform against a public reference implementation of the same
# problem domain: 12 source-code-plane input-surface rows, 6 rows in a new
# framework-and-configuration-awareness area (V), 10 advanced-code-analysis
# rows, 3 graph-analysis rows, 6 MCP rows, 1 retrieval row, 3 token-cost rows,
# 15 delivery-and-distribution rows, 2 evidence-and-reproducibility rows, and
# 11 UX-and-docs rows. Every one of them is honestly NOT IMPLEMENTED or
# PARTIAL; none is built by the capture step. An independent adversarial review
# of that comparison then found eight capabilities the first pass had missed and
# they were added too: directed relationship queries and a persisted flow
# catalogue (T), compiler-config path-alias resolution (Q), identifier-aware
# ranking (O), a multi-hop retrieval benchmark (S), viewer accessibility and
# loopback serving (R), and automated dependency-update proposals (K). A second
# review found eight more and inflated PARTIAL labels on seven: inbound Origin
# and Host validation and a response verbosity lever (F), a post-processing
# stage (N), refactor suggestions (Q), daemon lifecycle and package-index
# publication (R), a scheduled report-only benchmark run (S), and precomputed
# summary tables (U).
EXPECTED_TOTAL = 283

APPROVED_STATUSES = {
    "PRODUCTION READY",
    "IMPLEMENTED BUT NOT FULLY VERIFIED",
    "PARTIAL",
    "NOT IMPLEMENTED",
    "BLOCKED BY EXTERNAL PLATFORM",
    "NOT APPLICABLE",
}

REQUIRED_COLUMNS = (
    "id",
    "area",
    "source_ref",
    "requirement",
    "implementation_files",
    "tests",
    "acceptance_test",
    "status",
    "evidence_path",
    "licence_impact",
    "remaining_limitation",
)

STATUSES_ALLOWING_EMPTY_IMPL = {
    "NOT IMPLEMENTED",
    "NOT APPLICABLE",
    "BLOCKED BY EXTERNAL PLATFORM",
}
STATUSES_ALLOWING_EMPTY_TESTS = STATUSES_ALLOWING_EMPTY_IMPL

# An evidence log carrying this phrase is a documentary or manual note, not an
# executed acceptance run, so it cannot support a PRODUCTION READY label.
DISCLAIMER_MARKER = "not an executed acceptance run"
DOWNLABEL_TARGET = "IMPLEMENTED BUT NOT FULLY VERIFIED"

# A pytest summary line that records a failure or an error. The gate used to ask
# only whether the log contained the word "passed", which "1 failed, 19 passed"
# satisfies: a row stayed green on evidence of a failing run, and one did.
# Checking that something passed is not the same as checking that nothing failed.
_FAILURE_MARKERS = (
    re.compile(r"\b\d+\s+failed\b", re.I),
    re.compile(r"\b\d+\s+error(?:s)?\b", re.I),
    re.compile(r"^FAILED\s+\S", re.M),
    re.compile(r"^ERROR\s+\S", re.M),
)


def _cell(row: dict, name: str) -> str:
    return (row.get(name, "") or "").strip()


def _row_missing_columns(row: dict) -> list[str]:
    return [col for col in REQUIRED_COLUMNS if col not in row]


def _impl_tokens(impl: str) -> list[str]:
    return [t.strip() for t in impl.split(",") if t.strip()]


def _path_resolves(token: str) -> bool:
    tok = token.strip()
    if not tok:
        return True
    if tok.endswith("/*"):
        tok = tok[:-2]
    if tok.endswith("/"):
        tok = tok[:-1]
    if "*" in tok:
        return bool(glob.glob(str(ROOT / tok)))
    return (ROOT / tok).exists()


def _unresolved_paths(impl: str) -> list[str]:
    return [t for t in _impl_tokens(impl) if not _path_resolves(t)]


def _acceptance_is_pytest(acc: str) -> bool:
    return acc.strip().startswith("pytest ")


def _evidence_real(evidence_path: str, acceptance: str = "") -> tuple[bool, str]:
    if not evidence_path:
        return False, "evidence_path empty"
    p = ROOT / evidence_path
    if not p.exists():
        return False, f"evidence_path {evidence_path!r} does not exist on disk"
    raw = p.read_text(encoding="utf-8", errors="replace")
    text = raw.lower()
    # A renamed test leaves the acceptance string pointing at a node id that no
    # longer exists. pytest then selects nothing, exits 0, and writes a log that
    # contains neither "failed" nor any test. Treat selecting nothing as failure,
    # or a row stays green on an acceptance that runs no test at all.
    if "no tests ran" in text or "error: not found" in text:
        return False, "acceptance selected no tests (renamed or missing test id)"
    if DISCLAIMER_MARKER in text:
        return False, "evidence is a manual or documentary disclaimer, not an executed run"
    if "passed" not in text:
        return False, "evidence does not record a passing test"
    # Something passed is not the same as nothing failed. A log reading
    # "1 failed, 19 passed" contains "passed" and satisfied every check above,
    # which is how a green row came to be backed by evidence of a failing run.
    for marker in _FAILURE_MARKERS:
        found = marker.search(raw)
        if found:
            return False, f"evidence records a failing run: {found.group(0).strip()!r}"
    # A recorded exit code that is not zero contradicts the log it sits on.
    exit_code = re.search(r"^#\s*exit_code:\s*(-?\d+)\s*$", raw, re.M)
    if exit_code and exit_code.group(1) != "0":
        return False, f"evidence records exit_code {exit_code.group(1)}"
    # The log must be evidence for THIS row's acceptance. Without this, a log
    # from some other command satisfies every check above.
    header = re.search(r"^#\s*acceptance_test:\s*(.*)$", raw, re.M)
    if acceptance and header and header.group(1).strip() != acceptance.strip():
        return False, (
            f"evidence records acceptance {header.group(1).strip()!r}, "
            f"but the row's acceptance is {acceptance.strip()!r}"
        )
    return True, "ok"


def _strict_pr_reasons(row: dict) -> list[str]:
    """Reasons this row does NOT meet the strict PRODUCTION READY bar."""
    reasons: list[str] = []
    impl = _cell(row, "implementation_files")
    acc = _cell(row, "acceptance_test")
    ev = _cell(row, "evidence_path")
    if not impl:
        reasons.append("implementation_files empty")
    else:
        missing = _unresolved_paths(impl)
        if missing:
            reasons.append(f"implementation paths do not resolve: {missing}")
    if not _acceptance_is_pytest(acc):
        reasons.append(f"acceptance is manual or non-pytest: {acc!r}")
    ok, why = _evidence_real(ev, acc)
    if not ok:
        reasons.append(why)
    return reasons


def _read_rows() -> tuple[list[str], list[dict]]:
    with CSV_PATH.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    return fieldnames, rows


def rederive(write: bool) -> list[tuple[str, list[str]]]:
    fieldnames, rows = _read_rows()
    changes: list[tuple[str, list[str]]] = []
    for row in rows:
        if _cell(row, "status") != "PRODUCTION READY":
            continue
        reasons = _strict_pr_reasons(row)
        if reasons:
            row["status"] = DOWNLABEL_TARGET
            note = "Down-labelled 2026-08-02: " + "; ".join(reasons) + "."
            rl = (row.get("remaining_limitation") or "").strip()
            row["remaining_limitation"] = f"{rl} {note}".strip()
            changes.append((_cell(row, "id"), reasons))
    if write and changes:
        with CSV_PATH.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
    return changes


def validate() -> int:
    if not CSV_PATH.exists():
        print(f"missing: {CSV_PATH}", file=sys.stderr)
        return 2

    total = 0
    per_status: dict[str, int] = {label: 0 for label in APPROVED_STATUSES}
    per_area: dict[str, int] = {}
    ids_seen: dict[str, int] = {}
    invalid_status_rows: list[dict] = []
    duplicate_ids: list[str] = []
    structural_errors: list[dict] = []
    incomplete_rows: list[dict] = []
    pr_gate_failures: list[dict] = []
    impl_path_failures: list[dict] = []

    with CSV_PATH.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        missing_cols = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing_cols:
            print(f"CSV missing required columns: {missing_cols}", file=sys.stderr)
            return 3

        for lineno, row in enumerate(reader, start=2):
            missing = _row_missing_columns(row)
            if missing:
                structural_errors.append({"line": lineno, "id": _cell(row, "id"), "missing": missing})
                continue

            rid = _cell(row, "id")
            if not rid:
                structural_errors.append({"line": lineno, "id": "", "missing": ["id (empty)"]})
                continue

            total += 1
            ids_seen[rid] = ids_seen.get(rid, 0) + 1
            if ids_seen[rid] > 1:
                duplicate_ids.append(rid)

            status = _cell(row, "status")
            area = _cell(row, "area")
            per_area[area] = per_area.get(area, 0) + 1

            if status not in APPROVED_STATUSES:
                invalid_status_rows.append({"line": lineno, "id": rid, "status": status})
                continue

            per_status[status] += 1

            row_defects: list[str] = []
            if not area:
                row_defects.append("area is empty")
            if not _cell(row, "requirement"):
                row_defects.append("requirement is empty")
            if not _cell(row, "acceptance_test"):
                row_defects.append("acceptance_test is empty")
            if not _cell(row, "implementation_files") and status not in STATUSES_ALLOWING_EMPTY_IMPL:
                row_defects.append("implementation_files is empty")
            if not _cell(row, "tests") and status not in STATUSES_ALLOWING_EMPTY_TESTS:
                row_defects.append("tests is empty")
            if row_defects:
                incomplete_rows.append({"line": lineno, "id": rid, "status": status, "defects": row_defects})

            # Every row that cites implementation files must have them resolve.
            impl = _cell(row, "implementation_files")
            if impl:
                unresolved = _unresolved_paths(impl)
                if unresolved:
                    impl_path_failures.append({"line": lineno, "id": rid, "unresolved": unresolved})

            # Strict PRODUCTION READY gate.
            if status == "PRODUCTION READY":
                reasons = _strict_pr_reasons(row)
                if reasons:
                    pr_gate_failures.append({"line": lineno, "id": rid, "issues": reasons})

    status_sum = sum(per_status.values())
    ok = (
        not structural_errors
        and not incomplete_rows
        and not pr_gate_failures
        and not impl_path_failures
        and not invalid_status_rows
        and not duplicate_ids
        and status_sum == total
        and total == EXPECTED_TOTAL
    )

    summary = {
        "csv_path": str(CSV_PATH.relative_to(ROOT)),
        "total_rows": total,
        "status_distribution": dict(sorted(per_status.items())),
        "status_sum": status_sum,
        "sum_equals_total": status_sum == total,
        "expected_total": EXPECTED_TOTAL,
        "expected_total_matches": total == EXPECTED_TOTAL,
        "per_area": dict(sorted(per_area.items())),
        "duplicate_ids": sorted(set(duplicate_ids)),
        "invalid_status_rows": invalid_status_rows,
        "structural_errors": structural_errors,
        "incomplete_rows": incomplete_rows,
        "implementation_path_failures": impl_path_failures,
        "production_ready_gate_failures": pr_gate_failures,
        "approved_statuses": sorted(APPROVED_STATUSES),
        "required_columns": list(REQUIRED_COLUMNS),
        "production_ready_criteria": (
            "implementation paths resolve; acceptance is an executable pytest "
            "command; evidence exists, records a pass, and is not a manual or "
            "documentary disclaimer"
        ),
        "ok": ok,
    }

    JSON_OUT.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(f"total_rows        : {total}")
    print(f"expected_total    : {EXPECTED_TOTAL}")
    print(f"total_matches     : {total == EXPECTED_TOTAL}")
    print(f"status_sum        : {status_sum}")
    print(f"sum_equals_total  : {status_sum == total}")
    print("status_distribution:")
    for k, v in sorted(per_status.items()):
        print(f"  {v:>4}  {k}")
    if duplicate_ids:
        print(f"duplicate_ids     : {sorted(set(duplicate_ids))}", file=sys.stderr)
    if invalid_status_rows:
        print(f"invalid_status    : {invalid_status_rows}", file=sys.stderr)
    if structural_errors:
        print(f"structural_errors : {structural_errors}", file=sys.stderr)
    if incomplete_rows:
        print(f"incomplete_rows   : {len(incomplete_rows)}", file=sys.stderr)
        for r in incomplete_rows[:5]:
            print(f"  {r}", file=sys.stderr)
    if impl_path_failures:
        print(f"implementation_path_failures : {impl_path_failures}", file=sys.stderr)
    if pr_gate_failures:
        print(f"production_ready_gate_failures : {pr_gate_failures}", file=sys.stderr)
    print(f"wrote             : {JSON_OUT.relative_to(ROOT)}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Validate or re-derive the traceability matrix.")
    ap.add_argument("--rederive", action="store_true", help="print down-label recommendations")
    ap.add_argument("--rederive-write", action="store_true", help="apply down-labels to the CSV")
    args = ap.parse_args(argv)

    if args.rederive or args.rederive_write:
        changes = rederive(write=args.rederive_write)
        if not changes:
            print("rederive: no PRODUCTION READY row needs down-labelling")
        else:
            print(f"rederive: {len(changes)} rows down-labelled to {DOWNLABEL_TARGET}")
            for rid, reasons in changes:
                print(f"  {rid}: {'; '.join(reasons)}")
        if args.rederive_write:
            print(f"rederive: wrote {CSV_PATH.relative_to(ROOT)}")
        return 0

    return validate()


if __name__ == "__main__":
    raise SystemExit(main())

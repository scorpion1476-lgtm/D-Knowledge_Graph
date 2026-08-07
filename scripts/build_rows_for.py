#!/usr/bin/env python3
"""Regenerate row evidence for NAMED rows only, by actually running them.

Why this exists rather than ``build_row_evidence.py``. That script re-runs every
row in the matrix, and two of those rows shell out to ``docker build``. The
project's Docker isolation rule forbids touching Docker at all, so running it
wholesale is not allowed. This runs exactly the rows named on the command line,
which is what changing a handful of rows actually needs.

Fail loud: a row whose acceptance command fails is written with the failing
output and reported, never quietly skipped and never written as a pass. A row
that cannot promote must be visible as such.

Usage:
    python scripts/build_rows_for.py F-19 Q-05 Q-06
"""

from __future__ import annotations

import csv
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "docs" / "REQUIREMENTS_TRACEABILITY_MATRIX.csv"
ROWS_DIR = ROOT / "test-evidence" / "rows"

# Same interpreter that has the project's extras installed. A row run under a
# different interpreter would record a failure that says nothing about the code.
PYTHON = sys.executable


def _rows() -> dict[str, dict]:
    with CSV_PATH.open(encoding="utf-8", newline="") as handle:
        return {r["id"]: r for r in csv.DictReader(handle)}


def run_row(row: dict) -> tuple[bool, str]:
    acceptance = (row.get("acceptance_test") or "").strip()
    if not acceptance.startswith("pytest "):
        return False, f"acceptance is not a pytest command: {acceptance!r}"
    # shlex, not split(): a -k expression is quoted and naive splitting would
    # hand pytest the words separately, which selects nothing and fails a row
    # that is actually fine.
    args = shlex.split(acceptance)
    proc = subprocess.run(  # noqa: S603
        [PYTHON, "-m", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, out


def main() -> int:
    wanted = [a.strip() for a in sys.argv[1:] if a.strip()]
    if not wanted:
        print(__doc__, file=sys.stderr)
        return 2
    rows = _rows()
    unknown = [w for w in wanted if w not in rows]
    if unknown:
        print(f"build-rows-for: unknown row ids: {unknown}", file=sys.stderr)
        return 2

    ROWS_DIR.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    for row_id in wanted:
        row = rows[row_id]
        ok, output = run_row(row)
        stamp = datetime.now(timezone.utc).isoformat()
        header = (
            "# D-Knowledge_Graph row evidence\n"
            f"# row_id: {row_id}\n"
            f"# area: {row['area']}\n"
            f"# requirement: {row['requirement']}\n"
            f"# status: {row['status']}\n"
            f"# acceptance_test: {row['acceptance_test']}\n"
            f"# generated_at: {stamp}\n"
            "# action_kind: executed\n"
            f"# exit_code: {0 if ok else 1}\n\n"
        )
        (ROWS_DIR / f"{row_id}.log").write_text(header + output, encoding="utf-8")
        state = "PASS" if ok else "FAIL"
        print(f"{state} {row_id}: {row['acceptance_test']}")
        if not ok:
            failures.append(row_id)

    if failures:
        print(
            f"build-rows-for: {len(failures)} row(s) FAILED: {failures}. "
            "Their evidence records the failure; they must not be promoted.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

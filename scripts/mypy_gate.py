#!/usr/bin/env python3
"""Blocking mypy gate with a decreasing-baseline budget.

Runs ``python -m mypy src``, counts the reported errors, and compares the
count against the committed baseline in ``.mypy_baseline``.

- If the current count EXCEEDS the baseline, the gate fails: a new type error
  was introduced. This makes mypy blocking in CI without forcing a risky mass
  refactor to reach zero in one step.
- If the current count is at or below the baseline, the gate passes. When it is
  below, it prints a reminder to lower ``.mypy_baseline`` so the budget only
  ever decreases.

The baseline is calibrated to the CI environment (the declared extras
installed), so run this in that environment (or a matching one) for a
deterministic result.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE_FILE = ROOT / ".mypy_baseline"


def _current_error_count() -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "mypy", "src"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    if "Success" in out and "error" not in out:
        return 0, out
    m = re.search(r"Found (\d+) error", out)
    if m:
        return int(m.group(1)), out
    return -1, out


def main() -> int:
    baseline = 0
    if BASELINE_FILE.exists():
        baseline = int(BASELINE_FILE.read_text(encoding="utf-8").strip() or "0")

    count, out = _current_error_count()
    tail = "\n".join(out.strip().splitlines()[-30:])
    print(tail)
    if count < 0:
        print("mypy-gate: could not parse mypy output", file=sys.stderr)
        return 2

    print(f"mypy-gate: current={count} baseline={baseline}")
    if count > baseline:
        print(
            f"mypy-gate: FAIL - {count - baseline} new type error(s) "
            f"({count} > baseline {baseline})",
            file=sys.stderr,
        )
        return 1
    if count < baseline:
        print(
            f"mypy-gate: OK and improved - lower .mypy_baseline from "
            f"{baseline} to {count} to lock the gain."
        )
    else:
        print("mypy-gate: OK - no new type errors.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

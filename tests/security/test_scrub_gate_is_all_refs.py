"""The forbidden-identifier gate must cover every local ref, not just HEAD.

A push exposes every object reachable from the ref being pushed, and a stale
branch is exactly the thing that gets pushed by mistake. Scanning only HEAD once
let a set of stale local branches sit carrying forbidden identifiers in their
history while the gate printed the word clean. These assertions are what stops
that regressing into the scripts again.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STOP_GATE = ROOT / "scripts" / "stop_gate.sh"
PUBLISH = ROOT / "scripts" / "publish_github.sh"
SCRUB = ROOT / "scripts" / "scrub_scan.py"

_SCRUB_CALL = re.compile(r"scrub_scan\.py\s*(?P<args>[^\n|&;]*)")


def _scrub_invocations(script: Path) -> list[str]:
    return [m.group("args").strip() for m in _SCRUB_CALL.finditer(script.read_text(encoding="utf-8"))]


def test_stop_gate_scans_every_ref_not_just_head() -> None:
    calls = _scrub_invocations(STOP_GATE)
    assert calls, "stop gate no longer runs the scrub scan at all"
    for args in calls:
        assert "--history" in args, f"scrub call is not history-aware: {args!r}"
        assert "HEAD" not in args, f"scrub call is narrowed to HEAD: {args!r}"


def _first_code_line(script: Path, needle: str) -> int:
    """Line number of the first line that runs `needle`, ignoring comments."""
    for number, line in enumerate(script.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if needle in stripped:
            return number
    raise AssertionError(f"{needle!r} is not run by {script.name}")


def test_publish_script_gates_on_the_scrub_scan_before_it_touches_git() -> None:
    calls = _scrub_invocations(PUBLISH)
    assert calls, "publish script has no forbidden-identifier gate"
    for args in calls:
        assert "--history" in args
        assert "HEAD" not in args
    # The gate has to precede the first thing that could push or create a repo.
    gate = _first_code_line(PUBLISH, "scrub_scan.py")
    assert gate < _first_code_line(PUBLISH, "gh auth status")
    assert gate < _first_code_line(PUBLISH, "gh repo create")


def test_history_flag_defaults_to_every_local_ref() -> None:
    text = SCRUB.read_text(encoding="utf-8")
    assert 'const="--all"' in text, "--history no longer defaults to every local ref"


def test_scanner_refuses_to_report_clean_on_a_ref_it_cannot_resolve() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRUB), "--history", "refs/heads/definitely-not-a-real-ref"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode != 0
    assert "clean" not in (proc.stdout + proc.stderr).lower()


def test_every_local_ref_is_currently_clean() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRUB), "--history"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # The verdict now names coverage as well as absence: a scan that could not
    # read an in-scope path is not entitled to the word clean.
    assert "clean (no forbidden identifier found, every in-scope path read)" in proc.stdout

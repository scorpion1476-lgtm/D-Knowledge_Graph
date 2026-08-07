#!/usr/bin/env bash
# Deterministic completion gate for the project-local Stop hook.
#
# Runs only the checks that are CLOSABLE in this environment and blocks stop
# (exit 2) while any of them fails. What it deliberately does NOT gate on:
#
#   - Optional-extra suites that need a staged model or external binary. Those
#     tests skip honestly when their tool is absent, so the base lane below can
#     never trap the run on an unstaged dependency.
#   - Requirement-row status. A row that is honestly blocked by something
#     external must never trap the run, so status is reported, not gated.
#
# The hook contract: read the hook payload on stdin, and if stop_hook_active is
# already true, exit 0 immediately. Without that check a blocking gate would
# re-trigger itself forever.
#
# Exit codes: 0 = all closable checks pass, 2 = block stop with the reason.

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 0

# Bash 3.2 is the system shell on macOS, so this stays free of associative
# arrays, mapfile, and empty-array expansion under `set -u`.
payload="$(cat 2>/dev/null || true)"
case "$payload" in
  *'"stop_hook_active"'*[Tt]rue*) exit 0 ;;
esac

if [ -f "$ROOT/.venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  . "$ROOT/.venv/bin/activate"
fi
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONPATH
# Air-gap default holds inside the gate too.
export DKG_ALLOW_OUTBOUND=0
export DKG_TELEMETRY=0

failed=""

run() {
  name="$1"
  shift
  if out="$("$@" 2>&1)"; then
    printf 'stop-gate: %s OK\n' "$name"
  else
    failed="$failed $name"
    printf 'stop-gate: %s FAILED\n' "$name" >&2
    printf '%s\n' "$out" | tail -40 >&2
  fi
}

run "ruff"          python -m ruff check src tests
run "mypy-budget"   python scripts/mypy_gate.py
run "dash-scan"     bash scripts/check_dashes.sh
run "secret-scan"   python scripts/secret_scan.py
# History-aware and every-ref: a file deleted in a later commit is still in the
# history that gets pushed, so an index-only scan would pass while a forbidden
# term sat one `git log -p` away. `--history` with no argument means --all,
# which is every local ref, not just HEAD. Scanning HEAD alone once let a set of
# stale local branches sit for days carrying forbidden identifiers in their
# history while the gate printed the word clean, and any of them was one
# mistaken `git push` from being public. The extra cost is seconds.
run "scrub-scan"    python scripts/scrub_scan.py --history
run "licence-audit" python scripts/license_inventory.py
# Reports row counts and enforces the structural invariants. It does NOT gate on
# any individual row's status, so a row that is honestly blocked by something
# external can never trap the run.
run "requirements"  python scripts/validate_traceability.py
run "doc-counts"    python -m pytest -q --disable-warnings tests/unit/test_doc_count_consistency.py
# The full test suite is deliberately NOT run here. It is a verification step,
# not a per-stop check: at several minutes a run it would dominate the gate's
# cost while the fast static checks above already catch what a stop can break.
# Verification runs it in both lanes (with and without optional extras).

if [ -n "$failed" ]; then
  printf 'stop-gate: BLOCKING. Failing closable checks:%s\n' "$failed" >&2
  printf 'stop-gate: fix the above, then stop again.\n' >&2
  exit 2
fi

printf 'stop-gate: all closable checks pass\n'
exit 0

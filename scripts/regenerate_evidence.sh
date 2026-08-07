#!/usr/bin/env bash
# Regenerate every committed artifact in the one order that leaves the tree
# self-consistent.
#
# The order is load-bearing and it is not obvious, which is exactly why it lives
# in a script instead of in somebody's head. Two files hash other files:
#
#   EVIDENCE_BUNDLE.json  records a SHA-256 for every evidence file except
#                         SHA256SUMS, and reports the test counts read out of
#                         the newest recorded pytest log.
#   SHA256SUMS            records a SHA-256 for every evidence file including
#                         the bundle.
#
# So the bundle must be written before the checksums, and anything that changes
# an evidence file must be written before both. Running the checksums first
# leaves SHA256SUMS failing against its own repository, which is what happened
# once and is why tests/release/test_evidence_checksums.py now exists.
#
# The recorded test run sits between the two checksum-consistent points: it runs
# when the tree is already consistent, so the run passes, and its log is
# untracked at that moment so it cannot invalidate the checksums it is verifying.
# The bundle and checksums are then rewritten to pick that log up.
#
# Docker is never touched. scripts/build_row_evidence.py is deliberately NOT run
# here: it re-runs every row including two whose acceptance shells out to
# `docker build`. Regenerate row evidence for changed rows only.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="python3"
if [ -x "$ROOT/.venv/bin/python" ]; then
  PY="$ROOT/.venv/bin/python"
fi
export DKG_ALLOW_OUTBOUND=0
export DKG_TELEMETRY=0

step() { printf '\n== %s\n' "$1"; }

step "benchmarks (seeded; regenerates docs/BENCHMARKS.md and every benchmark artifact)"
PYTHONHASHSEED=0 "$PY" scripts/benchmark.py

step "licence inventory"
"$PY" scripts/license_inventory.py

step "SBOM"
"$PY" scripts/sbom.py

step "lockfile"
"$PY" scripts/lockfile.py

step "traceability validation (writes docs/traceability_summary.json)"
"$PY" scripts/validate_traceability.py

step "spreadsheet export"
"$PY" scripts/export_matrix_xlsx.py

# The bundle reads test-evidence/test_run_summary.json, which run_tests.sh
# writes. On a tree that has never recorded a run there is nothing to read, and
# the bundle would publish measured:false while the run below is about to assert
# otherwise. One bootstrap run breaks that circle; on any later regeneration the
# file already exists and this is a no-op.
if [ ! -f test-evidence/test_run_summary.json ]; then
  step "bootstrap test run (no recorded run exists yet)"
  bash scripts/run_tests.sh >/dev/null 2>&1 || true
fi

step "evidence bundle (must precede the checksums)"
"$PY" scripts/build_evidence_bundle.py

step "checksums (must follow the bundle)"
"$PY" scripts/checksum.py

step "recorded test run (tree is consistent here, so this run verifies the checksums)"
bash scripts/run_tests.sh

step "evidence bundle again, to record the counts from the run just made"
"$PY" scripts/build_evidence_bundle.py

step "checksums again, over the rewritten bundle"
"$PY" scripts/checksum.py

printf '\nregenerate-evidence: done. Verify with:\n'
printf '  cd test-evidence && shasum -a 256 -c SHA256SUMS\n'

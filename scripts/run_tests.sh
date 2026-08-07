#!/usr/bin/env bash
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONPATH

# Prefer the project virtualenv. This script writes the log that the evidence
# bundle reports its test counts from, and a bare `python3` picks up whatever
# interpreter happens to be first on PATH. Recording a run from an interpreter
# that does not have the project's declared extras installed publishes a result
# about an environment nobody meant to measure.
PY="python3"
if [ -x "$ROOT/.venv/bin/python" ]; then
  PY="$ROOT/.venv/bin/python"
fi

mkdir -p test-evidence
STAMP="$(date -u +'%Y%m%dT%H%M%SZ')"
{
  echo "# D-Knowledge_Graph test run ${STAMP}"
  echo
  echo "## Environment"
  "$PY" --version
  echo "interpreter: $PY"
  echo "sqlite: $("$PY" -c 'import sqlite3; print(sqlite3.sqlite_version)')"
  echo
  echo "## pytest"
  "$PY" -m pytest -q --maxfail=1 --disable-warnings 2>&1
  echo "pytest-exit: ${PIPESTATUS[0]:-$?}"
} | tee "test-evidence/pytest.${STAMP}.log"

# `set -e` is deliberately off so the summary below is written for a FAILING run
# too: a red run is exactly the one worth recording. The exit status is captured
# here and re-raised at the end, so the script still fails loudly. Removing the
# abort without restoring the status once left this script exiting 0 on a red
# suite, which silently defeated the regeneration pipeline that runs it.
RUN_STATUS=0
if ! grep -q "^pytest-exit: 0$" "test-evidence/pytest.${STAMP}.log"; then
  RUN_STATUS=1
fi

# The log itself is gitignored (`*.log`), so a committed artifact that cited it
# as the source of a published count would point at a file nobody who clones the
# repository can see. Distil the counts into a tracked summary instead, and
# record the log's own digest so the local run can still be tied to it.
"$PY" - "test-evidence/pytest.${STAMP}.log" <<'PYEOF'
import hashlib, json, pathlib, re, sys

log = pathlib.Path(sys.argv[1])
text = log.read_text(encoding="utf-8", errors="replace")
counts = {"passed": 0, "skipped": 0, "failed": 0, "errors": 0}
for line in reversed(text.splitlines()):
    if " passed" not in line and " failed" not in line:
        continue
    found = {w: int(n) for n, w in re.findall(r"(\d+)\s+(passed|skipped|failed|error)", line)}
    if not found:
        continue
    counts = {k: found.get(k, 0) for k in counts}
    counts["errors"] = found.get("error", 0)
    break
interpreter = ""
for line in text.splitlines():
    if line.startswith("interpreter: "):
        interpreter = line.split(": ", 1)[1].strip()
        break
out = pathlib.Path("test-evidence/test_run_summary.json")
out.write_text(json.dumps({
    "measured": True,
    "log_name": log.name,
    "log_sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
    "interpreter": interpreter,
    **counts,
    "run_complete": counts["failed"] == 0 and counts["errors"] == 0,
    "note": (
        "Distilled from the run log named above by scripts/run_tests.sh. The log "
        "itself is gitignored and local; this summary is the committed record. "
        "Counts describe one environment: a run with different optional extras "
        "staged skips a different set. The run stops at the first failure "
        "(--maxfail=1), so when run_complete is false the passed count is a "
        "lower bound on a truncated run, not a total."
    ),
}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(f"wrote {out} ({counts})")
PYEOF

if [ "$RUN_STATUS" -ne 0 ]; then
  echo "run-tests: the suite FAILED; summary written and status re-raised" >&2
fi
exit "$RUN_STATUS"

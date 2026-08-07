#!/usr/bin/env bash
# Audit the D-Knowledge_Graph project's declared dependencies.
#
# The project has zero mandatory runtime dependencies. Optional extras
# (html, pdf, rss, web) and dev tools (pytest, ruff, mypy) are pinned in
# the project venv. We freeze the venv's own packages (excluding the
# editable install of the project itself) and audit that list, so the
# result reflects the project, not whatever the invoking shell happens
# to have installed globally.
#
# Prerequisites:
#   pip install pip-audit
#   .venv/ exists (created by scripts/install.sh or pip install -e .[dev])
#
# Output:
#   test-evidence/pip_audit.txt   (plain text summary)
#
# Non-zero exit if any known vulnerability is reported.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EV="$ROOT/test-evidence"
mkdir -p "$EV"

if ! command -v pip-audit >/dev/null 2>&1; then
  echo "pip-audit not installed" >&2
  echo "install with: pip install pip-audit" >&2
  exit 2
fi

VENV_PY="$ROOT/.venv/bin/python"
if [ ! -x "$VENV_PY" ]; then
  echo "project venv not found at .venv/bin/python; run scripts/install.sh first" >&2
  exit 2
fi

FREEZE=$(mktemp)
trap 'rm -f "$FREEZE"' EXIT
"$VENV_PY" -m pip freeze | grep -v "^-e \|^# Editable" > "$FREEZE"

{
  echo "# pip-audit run against D-Knowledge_Graph project venv dependencies"
  echo "# generated_at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "# pip-audit version: $(pip-audit --version)"
  echo "# python: $("$VENV_PY" --version)"
  echo "# advisory source: PyPA advisory DB (pip-audit default)"
  echo "# packages audited:"
  sed 's/^/#   /' "$FREEZE"
  echo
  echo "## RESULT"
  # 2>&1 is load-bearing. pip-audit prints "No known vulnerabilities found" on
  # stderr, not stdout, so piping stdout alone into tee wrote a report whose
  # RESULT section was empty: the run happened, the gate worked, and the
  # artifact recorded no result at all. An evidence file that omits the finding
  # it exists to record is the empty-artifact failure this project's
  # supply-chain rule forbids.
  pip-audit -r "$FREEZE" --format columns 2>&1
} | tee "$EV/pip_audit.txt"

echo "wrote $EV/pip_audit.txt"

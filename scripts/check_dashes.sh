#!/usr/bin/env bash
# Fail if an em-dash or en-dash character appears in any tracked text file.
#
# The detection lives in scripts/check_dashes.py; see that file for why. In
# short: this script used `grep -P`, which BSD grep (that is, /usr/bin/grep on
# macOS, which is what it actually ran under) rejects with "invalid option". The
# error went to /dev/null, so the gate printed "no em/en dashes" on every run no
# matter what the tree contained. This wrapper stays only so the existing gate
# name and call sites keep working.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

PY="python3"
if [ -x "$ROOT/.venv/bin/python" ]; then
  PY="$ROOT/.venv/bin/python"
fi

exec "$PY" "$ROOT/scripts/check_dashes.py"

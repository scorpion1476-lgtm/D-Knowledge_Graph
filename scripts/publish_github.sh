#!/usr/bin/env bash
# Guarded GitHub publication script for D-Knowledge_Graph.
#
# What it does:
#   1. Initialise git in the current project root (if not already).
#   2. Create a branch named feature/production-foundation.
#   3. Stage all files that are not gitignored and commit them.
#   4. Verify the target repository does NOT already exist under the
#      authenticated GitHub owner. If it exists, STOP and print the
#      conflict.
#   5. Create the repository as private and push the branch.
#
# Guarantees:
#   - Refuses to run at all if any local ref, at any point in its history,
#     carries a forbidden identifier. A push exposes everything reachable from
#     the pushed ref, and a branch nobody has looked at in a week is exactly
#     what gets pushed by accident, so this gate is over every ref and not just
#     the one being published. It runs first, before any git or gh command, and
#     it fails loud: a scanner that cannot enumerate a ref is a failure, never a
#     clean result.
#   - Never force-pushes.
#   - Never touches an existing repository.
#   - Never runs if 'gh auth status' does not confirm a signed-in user.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

REPO_NAME="D-Knowledge_Graph"
BRANCH="publish"

PY="python3"
if [ -x "$ROOT/.venv/bin/python" ]; then
  PY="$ROOT/.venv/bin/python"
fi

echo "[scrub] full-ref forbidden-identifier sweep (blocking)"
if ! "$PY" "$ROOT/scripts/scrub_scan.py" --history; then
  echo "PUBLISH BLOCKED: a local ref carries a forbidden identifier in its history." >&2
  echo "Delete or rewrite the offending ref, re-run this script, and do not push." >&2
  exit 5
fi

if ! command -v git >/dev/null 2>&1; then
  echo "git not found" >&2
  exit 2
fi
if ! command -v gh >/dev/null 2>&1; then
  echo "gh (GitHub CLI) not found" >&2
  exit 2
fi

# Sanity: confirm we are inside the intended tree.
case "$ROOT" in
  */D-Knowledge_Graph) ;;
  *)
    echo "expected script to run under a D-Knowledge_Graph tree, got $ROOT" >&2
    exit 3
    ;;
esac

echo "[gh] confirming authentication"
gh auth status

if [ ! -d ".git" ]; then
  echo "[git] initialising"
  git init
fi

CUR="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo none)"
if [ "$CUR" != "$BRANCH" ]; then
  echo "[git] switching to $BRANCH"
  git checkout -B "$BRANCH"
fi

echo "[git] staging files (respects .gitignore)"
git add .

if git diff --cached --quiet; then
  echo "[git] no changes to commit"
else
  echo "[git] committing"
  git commit -m "chore: initial production foundation"
fi

OWNER="$(gh api user --jq .login)"
echo "[gh] authenticated as $OWNER"

if gh repo view "$OWNER/$REPO_NAME" >/dev/null 2>&1; then
  echo "CONFLICT: $OWNER/$REPO_NAME already exists. Stopping." >&2
  echo "Please review the existing repository before pushing." >&2
  exit 4
fi

echo "[gh] creating private repository $OWNER/$REPO_NAME"
gh repo create "$OWNER/$REPO_NAME" --private --source=. --push --disable-issues=false --disable-wiki=false

echo "[done] published $OWNER/$REPO_NAME on branch $BRANCH"

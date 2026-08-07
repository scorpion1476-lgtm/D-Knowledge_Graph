#!/usr/bin/env bash
# Container validation for D-Knowledge_Graph.
#
# Isolated build and run under a unique project name so nothing on the
# host's existing Docker or Podman projects is touched.
#
# Guarantees:
# - Unique tag per run so no cache is polluted.
# - No shared network or volume.
# - Removes ONLY the image this script created on cleanup.
#
# Usage: bash scripts/run_container_validate.sh [runtime]
#   runtime: docker (default) or podman
set -euo pipefail

RUNTIME="${1:-docker}"
if ! command -v "$RUNTIME" >/dev/null 2>&1; then
  echo "container runtime not found: $RUNTIME" >&2
  exit 2
fi

STAMP="$(date -u +%Y%m%dt%H%M%Sz)"
TAG="dkg-validate-${STAMP}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EV_DIR="$ROOT/test-evidence/container"
mkdir -p "$EV_DIR"
LOG="$EV_DIR/${TAG}.log"

cleanup() {
  echo "[cleanup] removing image $TAG"
  "$RUNTIME" image rm -f "$TAG" >/dev/null 2>&1 || true
}
trap cleanup EXIT

{
  echo "# D-Knowledge_Graph container validation"
  echo "# runtime: $RUNTIME"
  echo "# tag:     $TAG"
  echo "# started: $STAMP"
  echo

  echo "[build] $RUNTIME build -t $TAG -f docker/Dockerfile ."
  "$RUNTIME" build -t "$TAG" -f "$ROOT/docker/Dockerfile" "$ROOT"

  echo "[run] $RUNTIME run --rm --network none $TAG status"
  "$RUNTIME" run --rm --network none "$TAG" status

  echo "[done] container validation complete"
} | tee "$LOG"

echo "wrote $LOG"

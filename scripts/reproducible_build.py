#!/usr/bin/env python3
"""Verify the platform wheel builds reproducibly (byte-identical).

Builds the platform wheel twice into separate directories with
``SOURCE_DATE_EPOCH`` and ``PYTHONHASHSEED`` fixed, then compares the two wheels
byte for byte (SHA-256). A reproducible build lets a signed release be
independently reproduced and its signature and provenance re-verified. Also
checks that the wheel INCLUDES the Ariadne module. The whole repository is under
one source-available non-commercial licence, so nothing is excluded, and the signed
artifact carries the repository's single source-available licence.

Requires the ``release`` extra (pypa/build). When ``build`` is absent this is
reported as not run rather than failing. The build is a local subprocess (list
arguments, no shell, bounded timeout); pypa/build fetches build backends from the
network at build time, which is a build-time action and does not weaken the
product's air-gap default.

Writes test-evidence/reproducible_build.json.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "test-evidence" / "reproducible_build.json"

# A fixed, documented epoch so a local two-build comparison is deterministic. The
# release workflow sets SOURCE_DATE_EPOCH from the commit timestamp instead.
SOURCE_DATE_EPOCH = "1700000000"
_TIMEOUT = 600


def _build_available() -> bool:
    # Import the real API rather than the bare name, so a stray local build/
    # directory (created by wheel builds) cannot pose as the pypa build package.
    try:
        from build import ProjectBuilder  # noqa: F401

        return True
    except ImportError:
        return False


def _build_wheel(outdir: Path) -> Path:
    env = dict(os.environ)
    env["SOURCE_DATE_EPOCH"] = SOURCE_DATE_EPOCH
    env["PYTHONHASHSEED"] = "0"
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(outdir), str(ROOT)],
        check=True,
        capture_output=True,
        timeout=_TIMEOUT,
        env=env,
    )
    wheels = list(outdir.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected exactly one wheel, found {len(wheels)}")
    return wheels[0]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run() -> dict:
    if not _build_available():
        return {
            "status": "not_run_in_this_environment",
            "reason": "pypa/build not installed; install the 'release' extra: pip install d-knowledge-graph[release]",
        }
    tmp = Path(tempfile.mkdtemp(prefix="dkg-repro-"))
    try:
        w1 = _build_wheel(tmp / "b1")
        w2 = _build_wheel(tmp / "b2")
        h1, h2 = _sha256(w1), _sha256(w2)
        ariadne_files = [n for n in zipfile.ZipFile(w1).namelist() if "ariadne" in n.lower()]
        result = {
            "status": "ran",
            "reproducible": h1 == h2,
            "wheel": w1.name,
            "sha256_build1": h1,
            "sha256_build2": h2,
            "source_date_epoch": SOURCE_DATE_EPOCH,
            "python": sys.version.split()[0],
            "ariadne_included": ariadne_files != [],
            "ariadne_files_in_wheel": ariadne_files,
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return result


def main() -> int:
    result = run()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, indent=2))
    if result.get("status") == "ran" and not (result["reproducible"] and result["ariadne_included"]):
        print("reproducible-build check FAILED", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

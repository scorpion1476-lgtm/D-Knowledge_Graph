#!/usr/bin/env python3
"""Assemble test-evidence/EVIDENCE_BUNDLE.json."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TE = ROOT / "test-evidence"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _test_summary() -> dict:
    """Read the counts out of the committed test-run summary.

    These used to be three literals in this file, which meant the published
    bundle kept asserting a pass count that had not been true for months. The
    first correction read them from the newest pytest log, which was better but
    still cited a gitignored file: the bundle named a source that nobody
    cloning the repository could open. scripts/run_tests.sh now distils each
    run into a tracked summary, and that is what this reads.
    """
    summary_path = TE / "test_run_summary.json"
    if not summary_path.exists():
        return {
            "measured": False,
            "reason": (
                "no test-evidence/test_run_summary.json; run scripts/run_tests.sh, "
                "which writes it"
            ),
        }
    data = json.loads(summary_path.read_text(encoding="utf-8"))
    return {
        "measured": bool(data.get("measured")),
        "source": "test-evidence/test_run_summary.json",
        "passed": int(data.get("passed", 0)),
        "skipped": int(data.get("skipped", 0)),
        "failed": int(data.get("failed", 0)),
        "run_log_name": data.get("log_name"),
        "run_log_sha256": data.get("log_sha256"),
        "interpreter": data.get("interpreter"),
        "notes": (
            "Read from the committed summary named in 'source', not asserted "
            "here. The underlying run log is local and gitignored; its name and "
            "digest are recorded so a local run can be tied back to it. One "
            "environment's run: a run with different optional extras staged "
            "skips a different set."
        ),
    }


def _tracked_evidence() -> set[str]:
    """Repository-relative paths of the evidence files git actually tracks.

    The bundle used to hash everything under the evidence directory, which meant
    it published a local .DS_Store and sixty other untracked local artifacts as
    "evidence". A bundle nobody cloning the repository can reproduce is not
    evidence, so the file set is now the index, exactly as the checksum manifest
    already does it.
    """
    out = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z", "test-evidence"],
        capture_output=True,
        check=True,
        timeout=120,
    ).stdout
    return {p.decode("utf-8", "surrogateescape") for p in out.split(b"\0") if p}


def main() -> int:
    tracked = _tracked_evidence()
    files = {}
    for p in sorted(TE.rglob("*")):
        # SHA256SUMS is written after this bundle, over this bundle, so hashing
        # it here would bake in a value that the next line of the pipeline
        # invalidates. The checksum file is listed under supply_chain instead.
        if not p.is_file() or p.name in ("EVIDENCE_BUNDLE.json", "SHA256SUMS"):
            continue
        rel = str(p.relative_to(ROOT))
        if rel not in tracked:
            continue
        files[rel] = {"sha256": sha256(p), "bytes": p.stat().st_size}

    summary_path = ROOT / "docs" / "traceability_summary.json"
    trace_summary = (
        json.loads(summary_path.read_text(encoding="utf-8"))
        if summary_path.exists()
        else None
    )
    promotion_path = ROOT / "test-evidence" / "promotion_report.json"
    promotion = (
        json.loads(promotion_path.read_text(encoding="utf-8"))
        if promotion_path.exists()
        else None
    )
    environment_path = ROOT / "test-evidence" / "environment_probe.json"
    environment = (
        json.loads(environment_path.read_text(encoding="utf-8"))
        if environment_path.exists()
        else None
    )
    external_path = ROOT / "test-evidence" / "phase4_external_attempts.json"
    external = (
        json.loads(external_path.read_text(encoding="utf-8"))
        if external_path.exists()
        else None
    )

    # Derived, never typed. Two counts in the category list below were string
    # literals: "224 passed" and "all 124 rows". Both were stale by roughly an
    # order of magnitude while sitting inside the file that is meant to be the
    # published record, and the same file's own measured fields contradicted
    # them. A count in a published artifact has to come from the artifact it
    # describes.
    test_summary = _test_summary()
    passed_phrase = (
        f"{test_summary['passed']} passed"
        if test_summary.get("measured")
        else "no recorded run"
    )
    row_total = (trace_summary or {}).get("total_rows")
    rows_phrase = f"all {row_total} rows" if row_total else "every row in the matrix"

    bundle = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project": "d-knowledge-graph",
        "version": "0.1.0",
        "test_summary": test_summary,
        "supply_chain": {
            "sbom": "test-evidence/sbom.cdx.json",
            "license_inventory": "test-evidence/license_inventory.json",
            "lockfile": "requirements-lock.txt",
            "sast": "test-evidence/sast.json",
            "checksums": "test-evidence/SHA256SUMS",
        },
        "traceability": trace_summary,
        "promotion": promotion,
        "environment_probe": environment,
        "external_attempts": external,
        "validation_categories_run_here": [
            f"sandbox validation (pytest suite, {passed_phrase})",
            "HTTP MCP validation (loopback socket bind and 5 integration tests)",
            "container validation (isolated docker build and run under unique tag)",
            "dependency vulnerability audit (pip-audit against project venv, no findings)",
            "GitHub publication to a new private repository (never force-push)",
            "CI matrix on GitHub Actions (ubuntu, macos, windows across 3.10, 3.11, 3.12)",
            "supply-chain scripts (secret scan, dash check, SBOM, licence, checksums, SAST, lockfile)",
            f"per-row evidence generation for {rows_phrase}",
        ],
        "validation_categories_not_run_here": [
            "signed release (no signing key present)",
            "remote third-party MCP client handshake (no independent client available in this environment)",
            "bare-metal Windows workstation run outside a container (CI covers windows-latest)",
        ],
        "evidence_files": files,
        "environment_blockers": [
            "no signing key present for signed-release production (K-12)",
            "no independent third-party MCP client available for F-12",
            "no bare-metal Windows workstation available for L-03 (CI covers windows-latest)",
        ],
        "retracted_claims": [
            "This build does not claim independent attestation of a clean-room implementation.",
            "This build does not claim end-to-end remote deployment beyond the isolated container smoke test.",
            "This build does not claim feature parity with any third-party product.",
            "This build does not claim any signed artefact.",
            "This build does not claim a bare-metal Windows workstation exercise (CI runners only).",
        ],
    }
    (TE / "EVIDENCE_BUNDLE.json").write_text(
        json.dumps(bundle, indent=2, sort_keys=True), encoding="utf-8"
    )
    print("wrote EVIDENCE_BUNDLE.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

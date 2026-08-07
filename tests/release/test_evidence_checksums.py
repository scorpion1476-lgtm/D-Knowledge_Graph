"""The published checksums must actually verify.

`test-evidence/SHA256SUMS` is the one thing a third party runs to check the
evidence has not been altered, and nothing tested it. A regeneration run that
wrote the checksums before the evidence bundle left the committed SHA256SUMS
failing on its own repository, and the whole release-integrity story rests on
that file being correct.

The bundle and the checksum file depend on each other, so the order they are
generated in is load-bearing:

    build_evidence_bundle.py   hashes every evidence file except SHA256SUMS
    checksum.py                hashes every evidence file including the bundle

Run the other way round and the recorded hash of EVIDENCE_BUNDLE.json is the
hash of the previous bundle. These assertions fail the moment that happens.

The invariant is about *committed* evidence. A test run writes a fresh log into
this same directory, so an untracked file is by definition not yet part of the
evidence anyone is checking and is out of scope; scoring it would make the check
fail during the very run that produces it, which is circular rather than strict.
Everything git tracks is in scope.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "test-evidence"
SUMS = EVIDENCE / "SHA256SUMS"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _tracked_evidence() -> set[str]:
    """Evidence paths git tracks, relative to test-evidence/."""
    proc = subprocess.run(
        ["git", "ls-files", "test-evidence"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    out = set()
    for line in proc.stdout.splitlines():
        rel = line.strip()
        if not rel:
            continue
        name = str(Path(rel).relative_to("test-evidence"))
        if name != "SHA256SUMS":
            out.add(name)
    assert len(out) > 100, f"only {len(out)} tracked evidence files found"
    return out


def _recorded() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in SUMS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, _, rel = line.partition("  ")
        out[rel.strip()] = digest.strip()
    return out


def test_every_recorded_checksum_matches_the_file_on_disk() -> None:
    recorded = _recorded()
    assert len(recorded) > 100, f"only {len(recorded)} checksums recorded"
    mismatched: list[str] = []
    missing: list[str] = []
    for rel, digest in sorted(recorded.items()):
        path = EVIDENCE / rel
        if not path.is_file():
            missing.append(rel)
            continue
        if _sha256(path) != digest:
            mismatched.append(rel)
    assert not missing, f"recorded but absent: {missing}"
    assert not mismatched, f"checksum mismatch (regeneration order?): {mismatched}"


def test_the_checksums_record_nothing_that_is_not_committed() -> None:
    """The direction the first version of this test did not check.

    Hashing whatever happened to be on disk recorded entries for gitignored
    local files, so `shasum -c SHA256SUMS` passed in the tree that wrote it and
    exited 1 on every clone. Skipping untracked entries when verifying hid that
    completely: the check has to run in both directions or it certifies nothing.
    """
    recorded = set(_recorded())
    tracked = _tracked_evidence()
    phantom = sorted(recorded - tracked)
    assert not phantom, (
        f"{len(phantom)} checksum(s) name files git does not track, so they are "
        f"absent from any clone and `shasum -c` fails there: {phantom[:10]}"
    )


def test_the_checksum_file_verifies_the_way_a_third_party_would_run_it() -> None:
    """End to end, with the actual command, not a reimplementation of it."""
    import shutil

    tool = shutil.which("shasum") or shutil.which("sha256sum")
    if tool is None:
        import pytest

        pytest.skip("neither shasum nor sha256sum is available")
    args = [tool, "-a", "256", "-c", "SHA256SUMS"] if tool.endswith("shasum") else [tool, "-c", "SHA256SUMS"]
    proc = subprocess.run(args, cwd=EVIDENCE, capture_output=True, text=True, timeout=600)
    failures = [ln for ln in (proc.stdout + proc.stderr).splitlines() if ": OK" not in ln and ln.strip()]
    assert proc.returncode == 0, "checksum verification failed:\n" + "\n".join(failures[:10])


def test_the_evidence_bundle_is_covered_by_the_checksums() -> None:
    """It is the file the ordering bug hit, so it is named explicitly."""
    assert "EVIDENCE_BUNDLE.json" in _recorded()


def test_no_committed_evidence_file_is_left_out_of_the_checksums() -> None:
    recorded = set(_recorded())
    tracked = _tracked_evidence()
    assert not (tracked - recorded), (
        f"committed evidence with no checksum: {sorted(tracked - recorded)}"
    )


def test_the_bundle_does_not_hash_the_checksum_file_that_hashes_it() -> None:
    import json

    bundle = json.loads((EVIDENCE / "EVIDENCE_BUNDLE.json").read_text(encoding="utf-8"))
    assert "test-evidence/SHA256SUMS" not in bundle["evidence_files"], (
        "the bundle hashing SHA256SUMS makes the pair unsatisfiable in either order"
    )


def test_the_bundles_test_summary_is_read_from_a_committed_source_not_asserted() -> None:
    """And the source must be a file a clone actually has.

    The first version of this asserted the source existed, and the source was a
    gitignored pytest log, so the test passed here and failed on any clean
    checkout: exactly the shape of bug it was written to prevent.
    """
    import json

    bundle = json.loads((EVIDENCE / "EVIDENCE_BUNDLE.json").read_text(encoding="utf-8"))
    summary = bundle["test_summary"]
    assert summary.get("measured") is True
    source = summary["source"]
    assert (ROOT / source).is_file()
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", source],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert tracked.returncode == 0, (
        f"the bundle cites {source!r}, which git does not track, so the published "
        "counts point at evidence no clone can open"
    )
    assert summary["passed"] > 0

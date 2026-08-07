"""The dash gate must be a gate: it has to be able to fail.

Acceptance test for matrix row J-02, "No em or en dash in public docs". The row
previously pointed at `bash scripts/check_dashes.sh`, and the history recorded
in `scripts/check_dashes.py` is the reason a shell exit code was never adequate
evidence: the original gate used `grep -P`, which BSD grep rejects, the error
went to `/dev/null`, and the gate printed the word "no" on every run no matter
what the tree contained. It passed for as long as it existed and checked
nothing.

A test that only asserts the current tree is clean would reproduce that defect
exactly. So the assertions here come in pairs:

* the real tracked tree contains no em or en dash, and
* the same detector, run over a planted file, finds one. Both dash codepoints
  are checked separately, because a detector that catches only the em dash
  would leave the en dash to sail through while the gate still reported clean.

The scanned-file count is asserted to be large. That is the direct guard
against the original failure: a gate whose file list has fallen behind reports
clean because it looked at nothing.

This module never writes a dash character into its own source. It builds them
from their codepoints at runtime, exactly as the detector itself does, so the
file stays clean under the very scan it is testing.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

EN_DASH = chr(0x2013)
EM_DASH = chr(0x2014)


def _checker():
    spec = importlib.util.spec_from_file_location(
        "dkg_check_dashes_under_test", ROOT / "scripts" / "check_dashes.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def checker():
    return _checker()


# -- the detector detects ------------------------------------------------------


def test_the_detector_knows_both_forbidden_codepoints(checker):
    assert set(checker.FORBIDDEN) == {EN_DASH, EM_DASH}


@pytest.mark.parametrize(("name", "char"), [("en dash", EN_DASH), ("em dash", EM_DASH)])
def test_a_planted_dash_is_found(checker, tmp_path, name, char):
    """Negative control, one per codepoint."""
    planted = tmp_path / "planted.md"
    planted.write_text(f"A sentence with an interrupting {char} dash in it.\n", encoding="utf-8")
    hits, scanned = checker.offenders([planted])
    assert scanned == 1
    assert hits, f"the detector missed a planted {name}"
    assert hits[0].endswith(":1")


def test_a_clean_file_produces_no_hit(checker, tmp_path):
    clean = tmp_path / "clean.md"
    clean.write_text("A sentence with a hyphen-joined word and nothing else.\n", encoding="utf-8")
    hits, scanned = checker.offenders([clean])
    assert scanned == 1
    assert hits == []


def test_a_binary_file_is_skipped_not_decoded(checker, tmp_path):
    """A NUL byte means not text, the way `grep -I` treats it."""
    blob = tmp_path / "thing.bin"
    blob.write_bytes(b"\x00\x01\x02" + EM_DASH.encode("utf-8"))
    hits, scanned = checker.offenders([blob])
    assert scanned == 0 and hits == []


def test_the_reported_line_number_is_the_offending_line(checker, tmp_path):
    planted = tmp_path / "multi.md"
    planted.write_text("clean line\nsecond clean line\nthird " + EM_DASH + " line\n", encoding="utf-8")
    hits, _ = checker.offenders([planted])
    assert len(hits) == 1 and hits[0].endswith(":3")


# -- the real tree ------------------------------------------------------------


def test_the_file_list_comes_from_git_and_is_not_empty(checker):
    """The original gate's fatal flaw was a hand-maintained target list."""
    tracked = checker.tracked_files()
    assert len(tracked) > 200, f"only {len(tracked)} tracked files found; the list has fallen behind"
    assert any(p.name == "README.md" for p in tracked)
    assert any(p.suffix == ".md" and "docs" in p.parts for p in tracked)


def test_the_tracked_tree_is_clean_and_the_scan_actually_scanned(checker):
    tracked = checker.tracked_files()
    hits, scanned = checker.offenders(tracked)
    assert scanned > 200, f"the scan only read {scanned} files; a gate that looks at nothing passes"
    assert hits == [], f"em or en dash found in tracked content: {hits[:20]}"


def test_the_gate_exits_zero_on_this_tree_and_says_what_it_scanned():
    """The command the release checklist and the stop gate actually run."""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_dashes.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "no em/en dashes in any tracked text file" in proc.stdout
    assert "scanned)" in proc.stdout, "the gate does not report how many files it read"


def test_the_shell_wrapper_delegates_to_the_python_detector():
    """The wrapper is kept for call sites; it must not reintroduce grep.

    Comment lines are excluded deliberately: the wrapper's header explains the
    grep defect at length, and that explanation is the reason the file still
    exists. Only what the shell would execute is checked.
    """
    wrapper = (ROOT / "scripts" / "check_dashes.sh").read_text(encoding="utf-8")
    assert "check_dashes.py" in wrapper
    executable = [
        line
        for line in wrapper.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    offenders = [line for line in executable if "grep" in line]
    assert not offenders, (
        "the wrapper runs grep again; that is the defect this gate was rewritten "
        f"to remove: {offenders}"
    )
    assert any("check_dashes.py" in line for line in executable), (
        "the wrapper never actually invokes the Python detector"
    )

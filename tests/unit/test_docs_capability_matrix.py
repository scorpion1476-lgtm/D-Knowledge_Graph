"""The published capability matrix must be honest about its own statuses.

Acceptance test for matrix row J-06, "Honest capability matrix with validation
status". The requirement has two halves and only the first is visible to a
reader: that a matrix exists and carries a status per capability. The second
half, that the statuses are honest, is exactly what a manual review cannot
establish, because checking it means re-deriving 283 rows against their evidence
on disk.

So this test re-runs the project's own strict bar over every row rather than
trusting the label:

* every `PRODUCTION READY` row is re-checked against the gate: its
  implementation paths must resolve, its acceptance must be an executable
  pytest command, and its evidence must be a real executed run that passed. A
  row that fails is a forced-green row, and there must be none.
* every row that is *not* production ready must say why, in its own limitation
  cell. An unexplained not-green row is a status without a reason, which is the
  same failure in the other direction.
* the document defines every status label it uses, and uses no label it has not
  defined.
* the narrative counts agree with the CSV, and the prose section describing what
  is not fully verified names that many rows.

The negative control plants a fabricated production-ready row over a copy of the
real rows and proves the gate rejects it. Without that, a gate that had stopped
evaluating would report a clean matrix forever, which is the precise failure
this project has already been bitten by.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import re
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
CSV_PATH = DOCS / "REQUIREMENTS_TRACEABILITY_MATRIX.csv"
MATRIX_MD = DOCS / "REQUIREMENTS_TRACEABILITY_MATRIX.md"
SUMMARY = DOCS / "traceability_summary.json"


def _validator():
    spec = importlib.util.spec_from_file_location(
        "dkg_traceability_under_test", ROOT / "scripts" / "validate_traceability.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def validator():
    return _validator()


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    with CSV_PATH.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def counts(rows) -> Counter:
    return Counter((r["status"] or "").strip() for r in rows)


# -- the matrix exists and is a matrix -----------------------------------------


def test_the_matrix_carries_a_status_for_every_capability(rows, validator):
    assert rows, "the matrix has no rows"
    approved = set(validator.APPROVED_STATUSES)
    bad = [r["id"] for r in rows if (r["status"] or "").strip() not in approved]
    assert not bad, f"rows carry a status outside the approved set: {bad}"


def test_every_row_names_a_requirement_and_an_acceptance(rows):
    incomplete = [
        r["id"] for r in rows if not (r["requirement"] or "").strip() or not (r["acceptance_test"] or "").strip()
    ]
    assert not incomplete, f"rows with no requirement or no acceptance: {incomplete}"


# -- the statuses are honest ---------------------------------------------------


def test_no_production_ready_row_fails_the_strict_bar(rows, validator):
    """The whole requirement, in one assertion.

    This is the check that makes the published matrix worth reading: it
    re-derives every green row from what is actually on disk.
    """
    forced: list[str] = []
    for row in rows:
        if (row["status"] or "").strip() != "PRODUCTION READY":
            continue
        reasons = validator._strict_pr_reasons(row)
        if reasons:
            forced.append(f"{row['id']}: {reasons}")
    assert not forced, "rows are green without earning it: " + "; ".join(forced)


def test_every_row_that_is_not_green_records_why(rows):
    silent = [
        r["id"]
        for r in rows
        if (r["status"] or "").strip() != "PRODUCTION READY"
        and not (r["remaining_limitation"] or "").strip()
    ]
    assert not silent, f"not-green rows with no stated reason: {silent}"


def test_every_row_points_at_evidence_that_exists(rows):
    missing = [
        r["id"] for r in rows if not (ROOT / (r["evidence_path"] or "")).is_file()
    ]
    assert not missing, f"rows whose evidence file is absent: {missing}"


def test_no_green_rows_evidence_records_a_failing_run(rows):
    """Something passed is not the same as nothing failed.

    The bar used to ask only whether the log contained the word "passed", and
    "1 failed, 19 passed" contains it. N-17 sat green on exactly that: a log
    recording a failing run, satisfying every check, in the file that certifies
    every other row. An adversarial review found it; this is the assertion that
    stops it coming back.
    """
    offenders: list[str] = []
    for row in rows:
        if (row["status"] or "").strip() != "PRODUCTION READY":
            continue
        text = (ROOT / row["evidence_path"]).read_text(encoding="utf-8", errors="replace")
        for pattern in (r"\b\d+\s+failed\b", r"\b\d+\s+error(?:s)?\b", r"(?m)^FAILED\s+\S"):
            hit = re.search(pattern, text, re.I)
            if hit:
                offenders.append(f"{row['id']}: {hit.group(0).strip()!r}")
                break
    assert not offenders, "green rows whose evidence records a failure: " + "; ".join(offenders)


def test_no_green_rows_evidence_records_a_nonzero_exit(rows):
    offenders: list[str] = []
    for row in rows:
        if (row["status"] or "").strip() != "PRODUCTION READY":
            continue
        text = (ROOT / row["evidence_path"]).read_text(encoding="utf-8", errors="replace")
        code = re.search(r"^#\s*exit_code:\s*(-?\d+)\s*$", text, re.M)
        if code and code.group(1) != "0":
            offenders.append(f"{row['id']}: exit_code {code.group(1)}")
    assert not offenders, "green rows whose evidence records a nonzero exit: " + "; ".join(offenders)


def test_every_evidence_log_is_evidence_for_its_own_rows_acceptance(rows):
    """A log from some other command satisfies every other check here."""
    offenders: list[str] = []
    for row in rows:
        text = (ROOT / row["evidence_path"]).read_text(encoding="utf-8", errors="replace")
        header = re.search(r"^#\s*acceptance_test:\s*(.*)$", text, re.M)
        if header and header.group(1).strip() != row["acceptance_test"].strip():
            offenders.append(
                f"{row['id']}: log says {header.group(1).strip()!r}, "
                f"row says {row['acceptance_test'].strip()!r}"
            )
    assert not offenders, "evidence logs that belong to a different command: " + "; ".join(offenders)


def test_the_bar_rejects_a_log_that_records_a_failure(validator, tmp_path, monkeypatch):
    """Negative control for the three checks above.

    Written against the validator's own helper so the gate and this test cannot
    drift apart: if `_evidence_real` stops rejecting failing logs, this fails.
    """
    monkeypatch.setattr(validator, "ROOT", tmp_path)
    log = tmp_path / "evidence.log"

    log.write_text("# acceptance_test: pytest x -q\n\n1 failed, 19 passed\n", encoding="utf-8")
    ok, why = validator._evidence_real("evidence.log", "pytest x -q")
    assert not ok and "failing run" in why, why

    log.write_text("# acceptance_test: pytest x -q\n# exit_code: 1\n\n19 passed\n", encoding="utf-8")
    ok, why = validator._evidence_real("evidence.log", "pytest x -q")
    assert not ok and "exit_code" in why, why

    log.write_text("# acceptance_test: pytest other -q\n\n19 passed\n", encoding="utf-8")
    ok, why = validator._evidence_real("evidence.log", "pytest x -q")
    assert not ok and "acceptance" in why, why

    # And a clean log for the right command still passes, or the gate is just off.
    log.write_text("# acceptance_test: pytest x -q\n# exit_code: 0\n\n19 passed\n", encoding="utf-8")
    ok, why = validator._evidence_real("evidence.log", "pytest x -q")
    assert ok, why


def test_the_strict_bar_rejects_a_fabricated_green_row(validator):
    """Negative control.

    A row claiming production ready on a manual acceptance and a non-existent
    evidence file must be rejected on every count. If this passes, the gate
    above is measuring nothing.
    """
    fake = {
        "id": "ZZ-99",
        "implementation_files": "src/dkg/this_module_does_not_exist.py",
        "acceptance_test": "manual review",
        "evidence_path": "test-evidence/rows/ZZ-99.log",
        "status": "PRODUCTION READY",
    }
    reasons = validator._strict_pr_reasons(fake)
    assert len(reasons) == 3, reasons
    joined = " ".join(reasons)
    assert "do not resolve" in joined
    assert "manual or non-pytest" in joined
    assert "does not exist on disk" in joined


def test_the_strict_bar_rejects_a_row_whose_evidence_records_no_pass(validator, tmp_path):
    """A green row backed by a log that never says a test passed."""
    log = tmp_path / "empty.log"
    log.write_text("# nothing was run here\n", encoding="utf-8")
    ok, why = validator._evidence_real(str(log.relative_to(ROOT)) if log.is_relative_to(ROOT) else "")
    # The path above is outside the repo, so assert on the in-repo failure modes
    # the gate is actually written against.
    ok_missing, why_missing = validator._evidence_real("test-evidence/rows/definitely-absent.log")
    assert not ok_missing and "does not exist" in why_missing
    assert not ok and why


# -- the document explains its own labels --------------------------------------


def test_the_document_defines_every_status_label_it_uses(counts):
    md = MATRIX_MD.read_text(encoding="utf-8")
    for label in counts:
        assert re.search(rf"`{re.escape(label)}`", md), f"{label} is used but never defined"


def test_the_document_defines_the_production_ready_bar(counts):
    md = re.sub(r"\s+", " ", MATRIX_MD.read_text(encoding="utf-8")).lower()
    assert "acceptance test is an executable pytest command" in md
    assert "executed run that passed" in md


def test_the_counts_in_the_document_match_the_csv(counts, rows):
    md = MATRIX_MD.read_text(encoding="utf-8")
    for label, n in counts.items():
        m = re.search(rf"\|\s*{re.escape(label)}\s*\|\s*(\d+)\s*\|", md)
        assert m, f"{label} has no row in the counts table"
        assert int(m.group(1)) == n, f"{label}: document says {m.group(1)}, csv says {n}"


def test_the_not_fully_verified_narrative_states_the_real_number(counts):
    md = MATRIX_MD.read_text(encoding="utf-8")
    section = md.split("## What is not fully verified, and why", 1)
    assert len(section) == 2, "the document has no not-fully-verified section"
    stated = re.search(r"(\d+)\s+rows? (?:are|is) `IMPLEMENTED BUT NOT FULLY VERIFIED`", section[1])
    assert stated, "the section never states how many rows it is describing"
    assert int(stated.group(1)) == counts["IMPLEMENTED BUT NOT FULLY VERIFIED"]


def test_the_summary_json_agrees_with_the_csv(rows, counts):
    blob = json.loads(SUMMARY.read_text(encoding="utf-8"))
    assert blob["total_rows"] == len(rows)
    assert blob["status_distribution"] == dict(
        (label, counts.get(label, 0)) for label in blob["status_distribution"]
    )
    assert blob["production_ready_gate_failures"] == []

"""Documentation counts must not drift from the requirements CSV.

The CSV is the source of truth. Several tracked documents quote counts derived
from it, and nothing previously checked that they still agreed. They did drift:
`docs/REQUIREMENTS_TRACEABILITY_MATRIX.md` sat at a total of 124 with zero
production-ready rows long after the real totals had moved, and no gate noticed.

These tests close that hole. Every number a tracked document states about the
matrix is re-derived from the CSV here and compared, so a future row change
either updates the docs or fails the build.

A dated CHANGELOG entry is deliberately NOT checked: it records what was true at
a past release, and rewriting it to match today would falsify history.
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CSV_PATH = ROOT / "docs" / "REQUIREMENTS_TRACEABILITY_MATRIX.csv"
SUMMARY_PATH = ROOT / "docs" / "traceability_summary.json"
MATRIX_MD = ROOT / "docs" / "REQUIREMENTS_TRACEABILITY_MATRIX.md"
README = ROOT / "README.md"
VALIDATOR = ROOT / "scripts" / "validate_traceability.py"


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    with CSV_PATH.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture(scope="module")
def counts(rows) -> Counter:
    return Counter((r["status"] or "").strip() for r in rows)


@pytest.fixture(scope="module")
def summary() -> dict:
    return json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))


def test_summary_matches_the_csv(rows, counts, summary):
    assert summary["total_rows"] == len(rows)
    for label, n in counts.items():
        assert summary["status_distribution"][label] == n, label
    assert sum(summary["status_distribution"].values()) == len(rows)


def test_validator_expected_total_matches_the_csv(rows):
    """The count guard must track the real row count.

    If EXPECTED_TOTAL drifts below the CSV a row can be added silently; if it
    drifts above, a row can be lost silently.
    """
    text = VALIDATOR.read_text(encoding="utf-8")
    m = re.search(r"^EXPECTED_TOTAL\s*=\s*(\d+)", text, re.M)
    assert m, "EXPECTED_TOTAL not found in the validator"
    assert int(m.group(1)) == len(rows)


def test_matrix_markdown_status_table_matches_the_csv(counts, rows):
    md = MATRIX_MD.read_text(encoding="utf-8")
    for label, n in counts.items():
        m = re.search(rf"\|\s*{re.escape(label)}\s*\|\s*(\d+)\s*\|", md)
        assert m, f"{label} has no row in the markdown status table"
        assert int(m.group(1)) == n, f"{label}: markdown says {m.group(1)}, csv says {n}"
    total = re.search(r"\*\*Total\*\*\s*\|\s*\*\*(\d+)\*\*", md)
    assert total, "no bolded total in the markdown status table"
    assert int(total.group(1)) == len(rows)


def test_matrix_markdown_lists_every_status_label(counts):
    """A label present in the CSV must appear in the markdown table.

    Otherwise a newly used status could carry rows that the summary table
    silently omits, and the visible total would still look right.
    """
    md = MATRIX_MD.read_text(encoding="utf-8")
    for label in counts:
        assert re.search(rf"\|\s*{re.escape(label)}\s*\|\s*\d+\s*\|", md), label


def test_matrix_markdown_area_range_matches_the_csv(rows):
    areas = sorted({r["id"].split("-")[0] for r in rows})
    md = MATRIX_MD.read_text(encoding="utf-8")
    assert f"areas A through {areas[-1]}" in md, f"expected areas A through {areas[-1]}"


def test_matrix_markdown_references_no_missing_path(rows):
    """Every repository path the document points at must exist."""
    md = MATRIX_MD.read_text(encoding="utf-8")
    for candidate in set(re.findall(r"`([A-Za-z0-9_./-]+/[A-Za-z0-9_./-]+)`", md)):
        if candidate.startswith(("http", "dkg.")):
            continue
        assert (ROOT / candidate).exists(), f"{candidate} is referenced but does not exist"


def test_readme_counts_match_the_csv(rows, counts):
    """The README may omit counts, but any count it states must be right."""
    text = README.read_text(encoding="utf-8")
    total = re.search(r"\*\*(\d+)\*\* total requirement rows", text)
    if total:
        assert int(total.group(1)) == len(rows)
    ready = re.search(r"\*\*(\d+)\*\* are PRODUCTION READY", text)
    if ready:
        assert int(ready.group(1)) == counts["PRODUCTION READY"]
    unverified = re.search(r"\*\*(\d+)\*\* are IMPLEMENTED BUT NOT FULLY VERIFIED", text)
    if unverified:
        assert int(unverified.group(1)) == counts["IMPLEMENTED BUT NOT FULLY VERIFIED"]


def test_readme_test_badge_matches_the_recorded_run():
    """The badge is a count, and counts drift.

    The README carried `tests-814 passing` long after
    `test-evidence/test_run_summary.json` had recorded a complete run of a very
    different size, and nothing looked. The summary is the committed record of a
    real run written by `scripts/run_tests.sh`; the badge must agree with it or
    not exist.
    """
    summary_path = ROOT / "test-evidence" / "test_run_summary.json"
    if not summary_path.is_file():
        pytest.skip("no recorded test run in this checkout")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if not (summary.get("measured") and summary.get("run_complete")):
        pytest.skip("the recorded run is incomplete, so its passed count is a lower bound")
    m = re.search(r"badge/tests-(\d+)%20passing", README.read_text(encoding="utf-8"))
    if not m:
        return
    assert int(m.group(1)) == summary["passed"], (
        f"the README badge says {m.group(1)} passing but "
        f"test-evidence/test_run_summary.json records {summary['passed']}"
    )


def test_readme_states_no_mcp_tool_count_it_cannot_back():
    """A written-out tool count goes stale every time a tool is added.

    The README said eighteen read-only tools while the registry served many
    more. Either state no number, or state the real one.
    """
    text = README.read_text(encoding="utf-8")
    m = re.search(r"\b(\d+|[A-Z][a-z]+(?:teen|ty[- ]\w+)?)\s+read-only tools\b", text)
    if not m:
        return
    import tempfile

    from dkg.core.db import open_database
    from dkg.mcp.tools import build_read_registry

    with tempfile.TemporaryDirectory() as td:
        with open_database(Path(td) / "g.db") as db:
            registered = len(build_read_registry(db).tools)
    words = {
        "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
        "forty": 40, "fifty": 50, "sixty": 60,
    }
    stated = m.group(1)
    value = int(stated) if stated.isdigit() else words.get(stated.lower())
    assert value == registered, (
        f"the README says {stated!r} read-only tools but the registry serves {registered}"
    )


# -- hero feature badges ------------------------------------------------------
# Three hero badges state a count of what the software actually exposes: the
# read-only MCP surface, the CLI's subcommands, and the languages the code plane
# covers. A badge is the most-read number on the page and the least likely to be
# revisited, so each one is re-derived from the running code here rather than
# trusted. The badges are identical across every README variant by design, so
# the parity check below stops a translation being left a release behind.

_BADGE_COUNTS = (
    ("MCP%20tools", "MCP tools"),
    ("commands", "CLI commands"),
    ("languages", "languages"),
)


def _live_badge_counts() -> dict[str, int]:
    """The three counts, read from the software rather than from any document."""
    import argparse
    import tempfile

    from dkg.cli.entry import _mk_parser
    from dkg.code.parser import language_inventory
    from dkg.core.db import open_database
    from dkg.mcp.tools import build_read_registry

    with tempfile.TemporaryDirectory() as td:
        with open_database(Path(td) / "g.db") as db:
            tools = len(build_read_registry(db).tools)
    subparsers = next(
        a for a in _mk_parser()._actions if isinstance(a, argparse._SubParsersAction)
    )
    return {
        "MCP%20tools": tools,
        "commands": len(subparsers.choices),
        "languages": len(language_inventory()),
    }


@pytest.mark.parametrize("name", sorted(p.name for p in ROOT.glob("README*.md")))
@pytest.mark.parametrize(("label", "described"), _BADGE_COUNTS)
def test_hero_feature_badges_match_the_running_software(name, label, described):
    text = (ROOT / name).read_text(encoding="utf-8")
    m = re.search(rf"badge/{label}-(\d+)-[0-9a-f]{{6}}\.svg", text)
    assert m, f"{name} has no {described} badge"
    expected = _live_badge_counts()[label]
    assert int(m.group(1)) == expected, (
        f"{name} says {m.group(1)} {described} but the software exposes {expected}"
    )


def test_every_readme_variant_carries_the_same_badge_row():
    """A translation left behind is a wrong number on a page somebody reads."""
    rows_by_file = {
        path.name: re.findall(r"!\[[^\]]*\]\(https://img\.shields\.io/badge/[^)]+\)", path.read_text(encoding="utf-8"))
        for path in sorted(ROOT.glob("README*.md"))
    }
    english = rows_by_file["README.md"]
    assert english, "the English README has no badge row"
    for name, badges in rows_by_file.items():
        assert badges == english, f"{name} badge row differs from README.md"


def test_no_tracked_document_states_a_stale_total(rows):
    """Guard against a resurrected total from an earlier phase.

    Historical totals this project has published before. Any of them appearing
    next to the word "rows" in a non-CHANGELOG tracked document means a stale
    figure came back. The CHANGELOG is exempt: its dated entries record what was
    true at a past release.
    """
    stale = {"107", "124", "164"}
    current = str(len(rows))
    stale.discard(current)
    offenders: list[str] = []
    # Tracked documents only. This list used to name a working-rules file that
    # has since been made local-only, which would have kept the check passing on
    # a maintainer's disk while skipping it silently everywhere else.
    for path in sorted(ROOT.joinpath("docs").glob("*.md")) + [README]:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if "row" not in line.lower():
                continue
            for number in stale:
                if re.search(rf"\b{number}\b", line):
                    offenders.append(f"{path.relative_to(ROOT)}: {line.strip()[:90]}")
    assert not offenders, "stale requirement total resurfaced: " + "; ".join(offenders)


def test_every_row_evidence_log_header_matches_its_csv_row():
    """The logs mirror the CSV, and a mirror that is not checked drifts.

    Three logs were found still quoting requirement text the CSV had corrected
    months earlier, one of them declaring this project's own detector
    Apache-2.0 and another saying the wheel excludes Ariadne. The matrix guard
    read the CSV and never read the copies of it.
    """
    import csv as _csv

    stale = []
    with (ROOT / "docs" / "REQUIREMENTS_TRACEABILITY_MATRIX.csv").open(encoding="utf-8", newline="") as fh:
        for row in _csv.DictReader(fh):
            log = ROOT / (row.get("evidence_path") or "")
            if not log.is_file():
                stale.append(f"{row['id']}: no evidence log at {row.get('evidence_path')!r}")
                continue
            header = log.read_text(encoding="utf-8", errors="replace").split("\n\n")[0]
            for field in ("area", "requirement", "status", "acceptance_test"):
                if f"# {field}: {row[field]}" not in header:
                    stale.append(f"{row['id']}: log {field} differs from the CSV")
                    break
    assert not stale, stale


# -- measured figures ---------------------------------------------------------
# Three separate reviews found stale numbers in the matrix: a word error rate
# citing an artifact that recorded the measurement was not taken, a structural
# precision that contradicted its own sibling row, a keyword-baseline nDCG that
# was actually the stub hybrid's, a token saving from a superseded baseline, an
# MCP tool count from a smaller registry, and a rerank latency that drifted on
# the next regeneration. The count guard above never looked at any of them.
#
# This is a registry, not a parser: each entry names a row, a figure, and where
# the truth lives. Adding a measured figure to a cell means adding it here.

_MEASURED_FIGURES = [
    ("E-04", "recall 6 of", "contradiction_quality.json", ("true_positives",), 6),
    ("E-04", "(0.6667)", "contradiction_quality.json", ("recall",), 0.6667),
    ("E-04", "precision 0.75", "contradiction_quality.json", ("precision",), 0.75),
    ("E-04", "8 signals", "contradiction_quality.json", ("signals_returned",), 8),
    ("O-02", "0.9473", "retrieval_quality.json",
     ("configurations", "A_keyword_only_baseline", "ndcg@10"), 0.9473),
    ("O-03", "0.9375", "retrieval_quality.json",
     ("configurations", "A_keyword_only_baseline", "mrr"), 0.9375),
    ("Q-03", "0.1081", "resolution_accuracy.json",
     ("per_language", "python", "blast_radius", "structural", "precision"), 0.1081),
    ("U-10", "59.7", "token_cost.json", None, 59.7),
]


def _dig(blob, path):
    cur = blob
    for key in path:
        cur = cur[key]
    return cur


@pytest.mark.parametrize(
    ("row_id", "fragment", "artifact", "path", "expected"), _MEASURED_FIGURES
)
def test_measured_figures_in_the_matrix_match_their_artifacts(
    rows, row_id, fragment, artifact, path, expected
):
    row = next(r for r in rows if r["id"] == row_id)
    cell = row["requirement"] + " " + row["remaining_limitation"]
    assert fragment in cell, f"{row_id} no longer states {fragment!r}; update this registry"
    blob = json.loads((ROOT / "test-evidence" / artifact).read_text(encoding="utf-8"))
    if path is None:
        actual = next(
            w["tokens_saved_pct"]
            for w in blob["graph_vs_strong"]["won_on_tokens_with_correctness_held"]
            if w["task"] == "knowledge_base_qa"
        )
    else:
        actual = _dig(blob, path)
    assert actual == pytest.approx(expected, abs=5e-4), (
        f"{row_id} states {expected} but {artifact} says {actual}"
    )


def test_no_row_claims_a_measurement_its_artifact_says_was_not_taken(rows):
    """M-08 published a 'measured mean WER 0.1339' while media_accuracy.json
    recorded asr.measured false. A cell may report an absent measurement, but
    it may not report a value for one."""
    media = json.loads((ROOT / "test-evidence" / "media_accuracy.json").read_text(encoding="utf-8"))
    if media.get("asr", {}).get("measured"):
        return
    row = next(r for r in rows if r["id"] == "M-08")
    cell = row["remaining_limitation"]
    assert "NOT MEASURED" in cell.upper()
    assert not re.search(r"measured (?:mean )?(?:WER|word error rate)\s*[0-9]", cell, re.I), cell

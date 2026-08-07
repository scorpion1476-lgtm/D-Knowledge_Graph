"""The roadmap may not claim anything as shipped that the matrix does not back.

This is the mechanical half of the requirement. A roadmap is the easiest
document in a repository to lie in, because nothing normally reads it. So every
bullet in the shipped section has to cite the requirement rows behind it, and
this file re-reads `docs/REQUIREMENTS_TRACEABILITY_MATRIX.csv` and checks them:
the row must exist, and its status must say the work is actually built.

Three deliberate asymmetries:

* Shipped is checked hard. A cited row whose status is NOT IMPLEMENTED or
  PARTIAL fails, because that is exactly the overclaim the requirement names.
* In progress and planned are checked for existence and for contradiction only.
  A row promoted by a later wave should not turn this file red for having once
  been listed as unfinished; the shipped section is where the lie would be.
* No section may claim a row it also disclaims. A row in both shipped and
  planned is a contradiction whichever one is right.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "ROADMAP.md"
CSV_PATH = ROOT / "docs" / "REQUIREMENTS_TRACEABILITY_MATRIX.csv"

ROW_ID = re.compile(r"`([A-Z]-\d{2})`")

BUILT = {"PRODUCTION READY", "IMPLEMENTED BUT NOT FULLY VERIFIED"}


@pytest.fixture(scope="module")
def doc() -> str:
    assert DOC.is_file(), f"{DOC} does not exist"
    return DOC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def status_by_id() -> dict[str, str]:
    with CSV_PATH.open(newline="", encoding="utf-8") as fh:
        return {r["id"]: (r["status"] or "").strip() for r in csv.DictReader(fh)}


def _section(doc: str, heading: str) -> str:
    """The text under a `## ` heading, up to the next `## ` heading."""
    lines = doc.splitlines()
    try:
        start = lines.index(f"## {heading}")
    except ValueError:  # pragma: no cover - guarded by its own test
        raise AssertionError(f"docs/ROADMAP.md has no '## {heading}' section") from None
    body: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        body.append(line)
    return "\n".join(body)


def _bullets(text: str) -> list[str]:
    """Top-level bullets, joined with their continuation lines."""
    out: list[str] = []
    for line in text.splitlines():
        if line.startswith("- "):
            out.append(line[2:].strip())
        elif out and line.startswith("  ") and line.strip():
            out[-1] += " " + line.strip()
        elif not line.strip():
            continue
    return out


# -- the document is shaped the way the requirement asks for -----------------


def test_the_roadmap_separates_shipped_planned_and_ongoing(doc):
    for heading in ("Shipped", "In progress", "Planned", "Ongoing"):
        assert f"## {heading}" in doc, f"missing '## {heading}'"


def test_the_roadmap_explains_how_its_claims_are_checked(doc):
    assert "REQUIREMENTS_TRACEABILITY_MATRIX.csv" in doc
    assert "tests/unit/test_docs_roadmap.py" in doc


# -- the requirement's own clause, checked mechanically ----------------------


def test_every_shipped_bullet_cites_at_least_one_requirement_row(doc):
    uncited = [b for b in _bullets(_section(doc, "Shipped")) if not ROW_ID.search(b)]
    assert not uncited, (
        "these shipped claims cite no requirement row, so nothing backs them: "
        + "; ".join(b[:80] for b in uncited)
    )


def test_every_row_the_roadmap_cites_exists_in_the_matrix(doc, status_by_id):
    unknown = sorted({rid for rid in ROW_ID.findall(doc) if rid not in status_by_id})
    assert not unknown, f"cited rows that are not in the matrix: {unknown}"


def test_no_shipped_claim_cites_a_row_that_is_not_built(doc, status_by_id):
    """The clause the requirement spells out, enforced against the CSV."""
    offenders = []
    for bullet in _bullets(_section(doc, "Shipped")):
        for rid in ROW_ID.findall(bullet):
            status = status_by_id.get(rid, "MISSING")
            if status not in BUILT:
                offenders.append(f"{rid} is {status}: {bullet[:70]}")
    assert not offenders, "shipped claims backed by rows that are not built: " + "; ".join(
        offenders
    )


def test_the_shipped_section_covers_more_than_a_token_handful(doc, status_by_id):
    """A shipped section citing three rows would pass the checks and say nothing."""
    cited = set(ROW_ID.findall(_section(doc, "Shipped")))
    assert len(cited) >= 40, f"only {len(cited)} rows cited as shipped"
    areas = {rid.split("-")[0] for rid in cited}
    assert len(areas) >= 10, f"shipped claims only span areas {sorted(areas)}"


def test_no_row_is_both_shipped_and_unfinished(doc):
    shipped = set(ROW_ID.findall(_section(doc, "Shipped")))
    for heading in ("In progress", "Planned"):
        overlap = shipped & set(ROW_ID.findall(_section(doc, heading)))
        assert not overlap, f"rows listed as shipped and as {heading.lower()}: {sorted(overlap)}"


def test_the_unfinished_sections_are_not_empty(doc):
    """A roadmap with nothing unfinished is a roadmap that is not being honest."""
    for heading in ("In progress", "Planned"):
        assert _bullets(_section(doc, heading)), f"'{heading}' lists nothing"


# -- no hand-typed totals ----------------------------------------------------


def test_the_roadmap_states_no_hand_typed_requirement_total(doc):
    """Counts come from the generated summary, never from a person.

    The project has already had a document sit at a stale total for months. The
    rule is that a report quotes `docs/traceability_summary.json`; this checks
    the roadmap obeys it.
    """
    offenders = [
        line.strip()
        for line in doc.splitlines()
        if re.search(r"\b\d+\s+(?:requirement\s+)?rows?\b", line)
    ]
    assert not offenders, "hand-typed row totals in the roadmap: " + "; ".join(offenders)


def test_the_roadmap_points_at_the_generated_summary_for_counts(doc):
    assert "docs/traceability_summary.json" in doc


# -- the ongoing and not-planned sections say something falsifiable ----------


def test_ongoing_names_the_standing_rules_rather_than_aspirations(doc):
    ongoing = _section(doc, "Ongoing")
    for phrase in ("Honest labelling", "Air-gap default", "Permissive third-party"):
        assert phrase in ongoing, f"'{phrase}' missing from the ongoing section"


def test_the_roadmap_records_what_will_not_be_done(doc):
    """Refusals are part of the roadmap, and two of them are licence terms."""
    section = _section(doc, "What is deliberately not planned")
    assert "hosted service" in section.lower()
    assert "read-only" in section.lower()
    assert "non-commercial" in section.lower()


# -- the reverse direction, added after an adversarial review ---------------
#
# The shipped direction was guarded from the start: a Shipped bullet may not
# cite a row that is not built. The other direction was not, and it drifted
# badly. After a build step promoted twenty-two rows, this document still listed
# sixteen of them as "In progress" or "Planned" while the matrix said they had
# shipped, and every test passed. Understating is a smaller sin than
# overclaiming, but it still contradicts the single source of truth, and the
# same blind spot would hide an overclaim if a row were ever demoted.


def _cited_in(doc: str, heading: str) -> list[str]:
    body = re.search(rf"^## {re.escape(heading)}\b(.*?)(?=^## |\Z)", doc, re.S | re.M)
    assert body, f"no {heading!r} section"
    return sorted(set(re.findall(r"\b([A-Z]-\d{2})\b", body.group(1))))


def test_nothing_listed_as_planned_has_actually_been_built(doc, status_by_id):
    """`Planned` says not started, so every row it cites must be NOT IMPLEMENTED."""
    wrong = [
        (rid, status_by_id[rid])
        for rid in _cited_in(doc, "Planned")
        if rid in status_by_id and status_by_id[rid] != "NOT IMPLEMENTED"
    ]
    assert not wrong, (
        "the roadmap lists these as Planned but the matrix says otherwise, so the roadmap "
        f"contradicts the source of truth: {wrong}"
    )


def test_nothing_listed_as_in_progress_is_finished_or_unstarted(doc, status_by_id):
    """`In progress` means started and incomplete: PARTIAL, and nothing else."""
    wrong = [
        (rid, status_by_id[rid])
        for rid in _cited_in(doc, "In progress")
        if rid in status_by_id and status_by_id[rid] != "PARTIAL"
    ]
    assert not wrong, (
        "the roadmap lists these as In progress but the matrix says they are finished or not "
        f"started: {wrong}"
    )


def test_no_row_appears_in_two_state_sections(doc):
    """A row in two states makes both claims unfalsifiable."""
    sections = {name: set(_cited_in(doc, name)) for name in ("Shipped", "In progress", "Planned")}
    names = list(sections)
    for i, first in enumerate(names):
        for second in names[i + 1 :]:
            overlap = sorted(sections[first] & sections[second])
            assert not overlap, f"cited in both {first!r} and {second!r}: {overlap}"

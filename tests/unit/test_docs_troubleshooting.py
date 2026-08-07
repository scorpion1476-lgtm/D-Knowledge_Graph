"""Every troubleshooting entry must carry a symptom, a cause, and a fix.

Two thirds of a troubleshooting entry is worthless. "It says database is
locked" with no cause leaves you guessing, and a cause with no fix is a
diagnosis you cannot act on. So the structural check here is not decoration: it
walks every `### ` entry in the document and fails the ones missing any of the
three parts.

The rest of the checks are about honesty and about not sending a reader after
something that does not exist:

* every command the document tells a reader to run must be a subcommand this
  build registers, checked against the real argument parser,
* every repository path it cites must exist,
* every environment variable it names must be one the source actually reads,
* every Windows and Linux-subsystem entry must be labelled as inferred from the
  code rather than observed, because no Windows machine was ever used, and the
  matrix row for Windows support says as much.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

from dkg.cli.entry import _mk_parser

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "TROUBLESHOOTING.md"
SRC = ROOT / "src" / "dkg"

REQUIRED_AREAS = (
    "Install and path problems",
    "Server start-up failures",
    "Database lock and staleness",
    "Missing optional components",
    "Windows and the Linux subsystem",
)


@pytest.fixture(scope="module")
def doc() -> str:
    assert DOC.is_file(), f"{DOC} does not exist"
    return DOC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def entries(doc: str) -> dict[str, str]:
    """Every `### ` entry, keyed by its heading, with its body."""
    out: dict[str, str] = {}
    current = None
    body: list[str] = []
    for line in doc.splitlines():
        if line.startswith("### "):
            if current:
                out[current] = "\n".join(body)
            current, body = line[4:].strip(), []
        elif line.startswith("## "):
            if current:
                out[current] = "\n".join(body)
            current, body = None, []
        elif current is not None:
            body.append(line)
    if current:
        out[current] = "\n".join(body)
    return out


@pytest.fixture(scope="module")
def areas(doc: str) -> dict[str, str]:
    """Every `## ` area, keyed by its heading, with everything beneath it."""
    out: dict[str, str] = {}
    current = None
    body: list[str] = []
    for line in doc.splitlines():
        if line.startswith("## "):
            if current:
                out[current] = "\n".join(body)
            current, body = line[3:].strip(), []
        elif current is not None:
            body.append(line)
    if current:
        out[current] = "\n".join(body)
    return out


@pytest.fixture(scope="module")
def subcommands() -> set[str]:
    parser = _mk_parser()
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return set(action.choices)


# -- the requirement's five areas --------------------------------------------


def test_every_required_area_is_covered(areas):
    missing = [a for a in REQUIRED_AREAS if a not in areas]
    assert not missing, f"missing troubleshooting areas: {missing}"


def test_each_required_area_has_more_than_one_entry(areas):
    thin = [
        a
        for a in REQUIRED_AREAS
        if len([line for line in areas[a].splitlines() if line.startswith("### ")]) < 2
    ]
    assert not thin, f"areas with fewer than two entries: {thin}"


# -- the structural rule this document lives by ------------------------------


def test_every_entry_has_a_symptom_a_cause_and_a_fix(entries):
    incomplete: list[str] = []
    for heading, body in entries.items():
        if "**Symptom.**" not in body:
            incomplete.append(f"{heading}: no symptom")
        if not re.search(r"\*\*Cause[^*]*\.\*\*", body):
            incomplete.append(f"{heading}: no cause")
        if "**Fix.**" not in body:
            incomplete.append(f"{heading}: no fix")
    assert not incomplete, "incomplete entries: " + "; ".join(incomplete)


def test_the_three_parts_appear_in_that_order(entries):
    """A fix printed before its cause reads as a cargo-cult instruction."""
    wrong = []
    for heading, body in entries.items():
        symptom = body.find("**Symptom.**")
        cause = re.search(r"\*\*Cause[^*]*\.\*\*", body)
        fix = body.find("**Fix.**")
        if symptom < 0 or cause is None or fix < 0:
            continue
        if not symptom < cause.start() < fix:
            wrong.append(heading)
    assert not wrong, f"entries whose parts are out of order: {wrong}"


def test_there_are_enough_entries_to_be_useful(entries):
    assert len(entries) >= 20, f"only {len(entries)} entries"


# -- honesty about the platform that was never tested ------------------------


def test_the_document_states_which_platforms_were_exercised(doc):
    flat = re.sub(r"\s+", " ", doc)
    assert "Windows and its Linux subsystem are not exercised" in flat
    assert "`L-03`" in doc, "the matrix row that records this must be cited"


def test_every_windows_entry_is_labelled_inferred_rather_than_observed(areas):
    """Nothing in that section may read as though it had been seen happen."""
    section = areas["Windows and the Linux subsystem"]
    entries = re.split(r"^### ", section, flags=re.M)[1:]
    unlabelled = [
        e.splitlines()[0]
        for e in entries
        if "inferred from the code" not in re.sub(r"\s+", " ", e)
    ]
    assert not unlabelled, f"Windows entries not marked as inferred: {unlabelled}"


def test_the_windows_section_asks_for_the_report_that_would_change_it(areas):
    section = re.sub(r"\s+", " ", areas["Windows and the Linux subsystem"])
    assert "from inferred to observed" in section


def test_the_matrix_still_agrees_that_windows_is_unverified():
    """If L-03 is ever promoted, this document's honesty note is stale."""
    import csv

    with (ROOT / "docs" / "REQUIREMENTS_TRACEABILITY_MATRIX.csv").open(
        newline="", encoding="utf-8"
    ) as fh:
        row = next(r for r in csv.DictReader(fh) if r["id"] == "L-03")
    assert row["status"] != "PRODUCTION READY", (
        "L-03 is now production ready, so docs/TROUBLESHOOTING.md must stop saying "
        "Windows was never exercised"
    )


# -- nothing sends the reader somewhere that does not exist ------------------


def test_every_command_the_document_names_is_registered(doc, subcommands):
    named = set(re.findall(r"\bdkg ([a-z][a-z-]+)\b", doc))
    named.discard("home")  # part of the --home option text, not a subcommand
    unknown = sorted(n for n in named if n not in subcommands)
    assert not unknown, f"the document names commands this build does not have: {unknown}"


def test_every_repository_path_the_document_cites_exists(doc):
    cited = set(re.findall(r"`((?:docs|scripts|src|tests)/[A-Za-z0-9_./-]+)`", doc))
    missing = sorted(p for p in cited if not (ROOT / p).exists())
    assert not missing, f"cited paths that do not exist: {missing}"


def test_every_environment_variable_the_document_names_is_read_by_the_code(doc):
    """A variable nobody reads is advice that silently does nothing."""
    named = set(re.findall(r"`(DKG_[A-Z_]+)`", doc))
    read = set()
    for path in SRC.rglob("*.py"):
        read |= set(re.findall(r"DKG_[A-Z_]+", path.read_text(encoding="utf-8")))
    unknown = sorted(named - read)
    assert not unknown, f"variables the source never reads: {unknown}"


def test_the_lock_entry_states_the_real_timeout(doc):
    """The busy timeout quoted here must be the one the database sets."""
    db = (ROOT / "src" / "dkg" / "core" / "db.py").read_text(encoding="utf-8")
    m = re.search(r"PRAGMA busy_timeout = (\d+)", db)
    assert m, "the database no longer sets a busy timeout; update this entry"
    seconds = int(m.group(1)) // 1000
    flat = re.sub(r"\s+", " ", doc)
    assert f"{['zero','one','two','three','four','five'][seconds]} second busy timeout" in flat, (
        f"the document must state the real busy timeout of {seconds} seconds"
    )


def test_the_document_opens_with_the_two_diagnostic_commands(doc):
    head = doc.split("## Install and path problems")[0]
    assert "dkg doctor" in head
    assert "scripts/probe_environment.py" in head
    assert "--offline" in head, "the outbound probe must be disclosed where it is recommended"


def test_the_document_points_at_the_security_policy_for_vulnerabilities(doc):
    flat = re.sub(r"\s+", " ", doc)
    assert "SECURITY.md" in flat
    assert "do not open a public issue" in flat.lower()


# -- hedge vocabulary, added after an adversarial review --------------------
#
# The per-entry check below covers only entries inside the Windows section. An
# adversarial review changed the DOCUMENT preamble's "inferred from the code" to
# "verified and confirmed" and every test still passed, because nothing guarded
# the two places where the label's meaning is actually explained. A reader who
# only skims the preamble would have been told these entries were verified.


def test_the_preamble_still_defines_the_inferred_label_rather_than_claiming_verification(doc):
    flat = re.sub(r"\s+", " ", doc)
    assert "Windows and its Linux subsystem are not exercised" in flat
    assert "inferred from the code" in flat
    assert "not from an observed failure on a Windows machine" in flat
    assert "Treat those entries as leads, not as confirmed behaviour" in flat


def test_the_windows_section_never_claims_the_entries_were_verified(areas):
    """The hedge has to survive as a hedge, not be quietly upgraded."""
    section = re.sub(r"\s+", " ", areas["Windows and the Linux subsystem"])
    for overclaim in (
        "verified and confirmed",
        "confirmed behaviour on Windows",
        "tested on Windows",
        "reproduced on Windows",
        "observed on Windows",
    ):
        assert overclaim.lower() not in section.lower(), (
            f"the Windows section claims {overclaim!r}, but no Windows machine was used"
        )


def test_the_label_appears_on_every_windows_cause_and_in_both_preambles(doc, areas):
    """Pin the count so a label cannot be dropped one at a time.

    Two explanatory uses (the document preamble and the section preamble) plus
    one on each Windows cause. Dropping any single one is what this catches.
    """
    total = doc.count("inferred from the code")
    per_entry = re.sub(r"\s+", " ", areas["Windows and the Linux subsystem"]).count(
        "Cause, inferred from the code"
    )
    assert per_entry >= 5, f"only {per_entry} Windows causes carry the label"
    assert total >= per_entry + 2, (
        f"{total} labels for {per_entry} causes: an explanatory preamble use has been lost"
    )

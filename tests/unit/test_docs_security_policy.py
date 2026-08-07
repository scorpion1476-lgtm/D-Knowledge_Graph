"""The security policy must give a finder a route, and the route must be real.

The requirement names four things: which versions are supported, the private
reporting channel, what a report should contain, and the acknowledgement and fix
timelines. All four are checked here, and so are two project-specific
properties:

* the channel must be the repository's own private vulnerability reporting on
  the forge, not an invented email address that nobody has verified, and
* the supported-version table must agree with the version the package actually
  declares, so it cannot quietly describe a release that does not exist.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from dkg import __version__

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "SECURITY.md"


@pytest.fixture(scope="module")
def doc() -> str:
    assert DOC.is_file(), f"{DOC} does not exist"
    return DOC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def flat(doc: str) -> str:
    return re.sub(r"\s+", " ", doc)


def test_the_policy_sits_at_the_repository_root():
    """The forge surfaces it from the root, and the requirement asks for that."""
    assert DOC.parent == ROOT


# -- supported versions ------------------------------------------------------


def test_it_states_which_versions_are_supported(doc):
    assert "## Supported versions" in doc
    table = _section(doc, "Supported versions")
    rows = [line for line in table.splitlines() if line.strip().startswith("|")]
    assert len(rows) >= 4, "the supported-version table has no rows"


def test_the_supported_version_matches_the_package_version(doc):
    """A policy naming a version the build does not have supports nothing."""
    table = _section(doc, "Supported versions")
    assert f"{__version__} (current)" in table, (
        f"the package is {__version__} but the table does not name it as current"
    )


def test_every_version_the_table_names_appears_in_the_changelog(doc):
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    released = set(re.findall(r"^## \[([0-9][^\]]*)\]", changelog, re.M))
    table = _section(doc, "Supported versions")
    named = set(re.findall(r"\|\s*(\d+\.\d+\.\d+(?:-[A-Za-z0-9.]+)?)", table))
    unknown = sorted(named - released)
    assert not unknown, f"the table names versions the changelog does not record: {unknown}"


def test_it_says_there_is_no_backport_line(flat):
    assert "no backport branch" in flat


def test_it_separates_the_irrevocable_licence_grant_from_support(flat):
    """The old Apache grant is irrevocable; that is not a support promise."""
    assert "2026-08-05" in flat
    assert "irrevocable" in flat
    assert "not a support commitment" in flat


# -- the private reporting channel -------------------------------------------


def test_it_names_the_forge_private_reporting_channel(doc, flat):
    assert "## Reporting a vulnerability" in doc
    assert "private vulnerability reporting" in flat.lower()
    assert "Report a vulnerability" in flat
    assert "Security" in flat


def test_it_forbids_a_public_issue_for_a_security_problem(flat):
    assert "Do not open a public issue" in flat


def test_it_does_not_invent_an_email_address(doc, flat):
    addresses = re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", doc)
    assert not addresses, f"an unverified email address was published: {addresses}"
    assert "No email address is published here on purpose" in flat


def test_it_offers_a_fallback_that_leaks_nothing(flat):
    assert "containing no detail at all" in flat


# -- what a report should contain --------------------------------------------


def test_it_says_what_a_report_should_contain(doc):
    body = _section(doc, "What a report should contain")
    bullets = [line for line in body.splitlines() if line.strip().startswith("- ")]
    assert len(bullets) >= 6, f"only {len(bullets)} items"


def test_the_requested_report_contents_cover_the_actionable_minimum(doc):
    body = re.sub(r"\s+", " ", _section(doc, "What a report should contain"))
    for item in ("version", "reproduction", "impact", "precondition"):
        assert item in body.lower(), f"a report is not asked for its {item}"


def test_it_asks_for_the_diagnostic_that_exists(doc):
    body = _section(doc, "What a report should contain")
    assert "scripts/probe_environment.py" in body
    assert (ROOT / "scripts" / "probe_environment.py").is_file()
    assert "--offline" in body, "the outbound probe must be disclosed where it is recommended"


# -- timelines ---------------------------------------------------------------


def test_it_states_an_acknowledgement_and_a_fix_timeline(doc, flat):
    body = _section(doc, "What happens after you report")
    assert "Acknowledgement" in body
    assert re.search(r"\d+ working days", body), "no acknowledgement window"
    assert re.search(r"Fix for a high-severity issue\s*\|\s*\d+ days", flat), "no fix window"


def test_the_timelines_are_labelled_as_targets_not_guarantees(flat):
    assert "These are targets" in flat
    assert "A schedule nobody can keep is not a commitment" in flat


def test_it_promises_to_report_a_slip_rather_than_go_silent(flat):
    assert "you will be told that it slipped" in flat.lower()


def test_a_security_fix_is_held_to_the_same_evidence_standard(flat):
    assert "a test that fails without it" in flat


def test_it_states_plainly_that_there_is_no_bounty(flat):
    assert "no bug bounty" in flat.lower()


# -- scope -------------------------------------------------------------------


def test_it_says_what_is_in_scope_and_what_is_not(doc):
    assert "## In scope" in doc
    assert "## Out of scope" in doc
    for heading in ("In scope", "Out of scope"):
        bullets = [
            line for line in _section(doc, heading).splitlines() if line.strip().startswith("- ")
        ]
        assert len(bullets) >= 5, f"{heading} has only {len(bullets)} items"


def test_out_of_scope_does_not_quietly_exclude_a_real_class_of_bug(flat):
    """An over-approximate result is not a vulnerability; a missing caveat is a bug."""
    assert "A false positive is not a vulnerability" in flat
    assert "missing caveat" in flat


def test_it_links_the_security_and_threat_models(doc):
    assert "docs/SECURITY_MODEL.md" in doc
    assert "docs/THREAT_MODEL.md" in doc
    assert (ROOT / "docs" / "SECURITY_MODEL.md").is_file()
    assert (ROOT / "docs" / "THREAT_MODEL.md").is_file()


def _section(doc: str, heading: str) -> str:
    lines = doc.splitlines()
    start = level = None
    for i, line in enumerate(lines):
        m = re.match(r"^(#{2,4})\s+(.*)$", line)
        if m and m.group(2).strip() == heading:
            start, level = i, len(m.group(1))
            break
    assert start is not None, f"SECURITY.md has no heading {heading!r}"
    body: list[str] = []
    for line in lines[start + 1 :]:
        m = re.match(r"^(#{1,6})\s", line)
        if m and len(m.group(1)) <= level:
            break
        body.append(line)
    return "\n".join(body)

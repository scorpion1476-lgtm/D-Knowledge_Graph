"""The code of conduct must name a standard, a scope, and a reporting route.

A code of conduct that names no standard is an opinion, one that names no scope
cannot be applied, and one with no reporting route is decorative. Those three
are the requirement, and they are checked here.

Two project-specific properties are checked as well:

* the adopted standard's own licence has to be permissive, and this project does
  not vendor other projects' text, so the document must link the standard rather
  than paste it, and
* no email address may be invented. An unverified address looks like a channel
  and silently drops reports, which is worse than publishing none.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "CODE_OF_CONDUCT.md"


@pytest.fixture(scope="module")
def doc() -> str:
    assert DOC.is_file(), f"{DOC} does not exist"
    return DOC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def flat(doc: str) -> str:
    return re.sub(r"\s+", " ", doc)


def test_the_code_of_conduct_sits_at_the_repository_root():
    """The forge only recognises it at the root, and so do most readers."""
    assert DOC.parent == ROOT


def test_it_names_the_standard_it_adopts_and_the_version(flat):
    assert "Contributor Covenant" in flat
    assert re.search(r"Contributor Covenant\*\*, version 2\.1|Contributor Covenant, version 2\.1", flat), flat[:200]


def test_the_adopted_standard_is_permissively_licensed_and_said_to_be(flat):
    """The requirement is explicit that the standard's own licence matters."""
    assert "CC BY 4.0" in flat
    assert "permissive" in flat


def test_the_standard_is_linked_rather_than_vendored(doc, flat):
    assert "https://www.contributor-covenant.org/version/2/1/code_of_conduct/" in doc
    assert "not vendored into this repository" in flat
    assert len(doc) < 20000, "this reads like the full Covenant text was pasted in"


def test_it_states_its_scope(doc, flat):
    assert "## Scope" in doc
    assert "issues, pull requests" in flat
    assert "representing the project" in flat


def test_the_scope_says_what_it_does_not_cover(flat):
    assert "does **not** extend" in flat or "does not extend" in flat
    assert "outside spaces it controls" in flat


def test_it_says_how_to_report_a_concern(doc, flat):
    assert "## Reporting a concern" in doc
    assert "privately" in flat
    assert "forge" in flat


def test_it_does_not_invent_an_email_address(doc, flat):
    """No unverified address anywhere, and the reason must be stated."""
    addresses = re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", doc)
    assert not addresses, f"an email address was published: {addresses}"
    assert "no email address is published" in flat.lower()


def test_it_says_what_a_useful_report_contains(doc):
    body = _section(doc, "What a useful report contains")
    bullets = [line for line in body.splitlines() if line.strip().startswith("- ")]
    assert len(bullets) >= 4, f"only {len(bullets)} items"


def test_it_says_what_happens_after_a_report(doc, flat):
    assert "## What happens next" in doc
    for stage in ("Acknowledgement", "Review", "Response", "Outcome"):
        assert stage in flat, f"{stage} missing from the process"
    assert "confidential" in flat


def test_it_is_honest_about_being_a_small_project(flat):
    """No promised response time nobody can keep, and the escalation limit stated."""
    assert "No fixed response time is promised" in flat
    assert "there may be nobody else to escalate to" in flat


def test_it_covers_retaliation_in_both_directions(doc, flat):
    assert "## Retaliation" in doc
    assert "good-faith report" in flat
    assert "bad faith" in flat
    assert "Being mistaken is not bad faith" in flat


def test_enforcing_the_technical_standard_is_excluded(doc, flat):
    """Review rigour must not be reportable as unkindness, and it must say so."""
    assert "Applying the technical standards is not a conduct problem" in doc
    assert "honest labelling" in flat
    assert "about the work" in flat or "between the work and the person" in flat


def test_it_points_at_the_security_policy_as_a_separate_process(doc):
    assert "SECURITY.md" in doc
    assert "CONTRIBUTING.md" in doc


def _section(doc: str, heading: str) -> str:
    lines = doc.splitlines()
    start = level = None
    for i, line in enumerate(lines):
        m = re.match(r"^(#{2,4})\s+(.*)$", line)
        if m and m.group(2).strip() == heading:
            start, level = i, len(m.group(1))
            break
    assert start is not None, f"CODE_OF_CONDUCT.md has no heading {heading!r}"
    body: list[str] = []
    for line in lines[start + 1 :]:
        m = re.match(r"^(#{1,6})\s", line)
        if m and len(m.group(1)) <= level:
            break
        body.append(line)
    return "\n".join(body)

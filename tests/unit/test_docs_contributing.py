"""The contributor guide must describe this project, not a generic one.

The failure mode this file exists to prevent is a contributing guide copied from
an open-source project: "fork the repository, make your changes, open a pull
request". Under this licence a published fork carrying modifications is exactly
what is prohibited, so that advice would be wrong in a way that matters.

Every check compares the document against something real: the gate commands
against the scripts that exist, the lint invocation against the configured
target, the extras against `pyproject.toml`, and the licence wording against the
terms the project is actually under.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "CONTRIBUTING.md"


@pytest.fixture(scope="module")
def doc() -> str:
    assert DOC.is_file(), f"{DOC} does not exist"
    return DOC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def flat(doc: str) -> str:
    return re.sub(r"\s+", " ", doc)


# -- development in a clone --------------------------------------------------


def test_the_guide_covers_development_in_a_clone(doc):
    assert "## Development in a clone" in doc
    section = _section(doc, "Development in a clone")
    assert "python3 -m venv .venv" in section
    assert 'pip install -e ".[dev]"' in section


def test_every_extra_the_guide_tells_you_to_install_is_declared(doc):
    """A guide naming an extra that does not exist wastes the reader's time."""
    declared = _declared_extras()
    named: set[str] = set()
    for group in re.findall(r'pip install -e "\.\[([a-z0-9,._-]+)\]"', doc):
        named |= {part.strip() for part in group.split(",")}
    unknown = sorted(named - declared)
    assert not unknown, f"the guide names extras pyproject does not declare: {unknown}"


def test_the_guide_states_the_supported_python_floor(doc, flat):
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'requires-python\s*=\s*">=(\d+\.\d+)"', text)
    assert m, "pyproject no longer declares requires-python"
    assert f"Python {m.group(1)} or newer" in flat, (
        f"the guide must state the real floor, Python {m.group(1)}"
    )


def test_the_guide_states_the_rule_that_makes_capability_detection_honest(flat):
    assert "core must pass with no extra installed" in flat.lower()


# -- test and gate commands, checked against the scripts that exist ----------


def test_every_script_the_gate_table_names_exists(doc):
    named = set(re.findall(r"`(?:python|bash) (scripts/[A-Za-z0-9_.-]+)`", doc))
    assert named, "the guide names no gate scripts at all"
    missing = sorted(p for p in named if not (ROOT / p).is_file())
    assert not missing, f"gate scripts the guide names that do not exist: {missing}"


def test_the_gate_table_covers_the_blocking_gates(doc):
    """The gates the completion hook runs must all be findable here."""
    gate_script = (ROOT / "scripts" / "stop_gate.sh").read_text(encoding="utf-8")
    for script in sorted(set(re.findall(r"scripts/[A-Za-z0-9_.-]+", gate_script))):
        if script in ("scripts/stop_gate.sh",):
            continue
        assert script in doc, f"{script} blocks the completion gate but the guide omits it"


def test_the_lint_command_is_the_one_the_project_uses(doc):
    assert "ruff check src tests" in doc


def test_the_type_gate_is_described_as_a_budget_not_a_clean_bill(doc, flat):
    assert "scripts/mypy_gate.py" in doc
    assert ".mypy_baseline" in doc
    assert "budget" in flat.lower()
    assert "only ever decreases" in flat


def test_the_guide_repeats_the_two_scripts_that_must_not_be_run_wholesale(doc, flat):
    """Both are recorded issues in the project's own working rules."""
    assert "scripts/build_row_evidence.py" in doc
    assert "docker build" in flat
    assert "scripts/promote_rows.py" in doc
    assert "scripts/validate_traceability.py` is the authority" in flat


def test_the_test_commands_run_pytest_the_way_the_project_does(doc):
    section = _section(doc, "The test and gate commands")
    assert "-m pytest -q" in section
    assert "bash scripts/run_tests.sh" in section


# -- coding conventions ------------------------------------------------------


def test_the_guide_states_the_dash_rule_first_among_the_hard_rules(doc, flat):
    section = _section(doc, "Coding conventions")
    assert "No em dash and no en dash" in section
    assert "check_dashes" in doc


def test_the_guide_states_the_two_planes_stay_separate(flat):
    assert "src/dkg/media/" in flat and "src/dkg/code/" in flat
    assert "never share parsers" in flat


def test_the_guide_states_the_conventions_that_are_actually_enforced(doc):
    section = re.sub(r"\s+", " ", _section(doc, "Coding conventions"))
    for phrase in (
        "Capability detection",
        "Deterministic output",
        "nearest-rank percentile",
        "iteratively",
        "Parameterised SQL",
        "read-only",
    ):
        assert phrase.lower() in section.lower(), f"convention missing: {phrase}"


def test_the_guide_demands_a_test_that_has_been_seen_to_fail(doc, flat):
    assert "break the" in flat and "confirm the test goes red" in flat


# -- licence and dependency rules a change must satisfy ----------------------


def test_the_guide_never_treats_this_as_an_open_source_project(doc):
    offenders = [
        line.strip()
        for line in doc.splitlines()
        if not line.startswith("#")
        and re.search(r"open[ -]source|FOSS|free software", line, re.I)
        and not re.search(r"\bnot\b", line, re.I)
    ]
    assert not offenders, "generic open-source framing: " + "; ".join(offenders)


def test_the_guide_states_the_licence_and_its_consequences(flat):
    assert "PolyForm Noncommercial 1.0.0" in flat
    assert "no-modification" in flat
    assert "Forking and publishing your changes is not permitted" in flat
    assert "no separate contributor licence agreement" in flat


def test_the_guide_records_the_earlier_apache_grant_correctly(flat):
    assert "2026-08-05" in flat
    assert "irrevocable" in flat


def test_the_guide_states_the_permissive_only_dependency_rule(flat):
    for licence in ("Apache-2.0", "MIT", "BSD", "ISC", "HPND"):
        assert licence in flat, f"{licence} missing from the permissive list"
    assert "No GPL, LGPL, or AGPL" in flat
    assert "external binaries" in flat


def test_the_guide_states_the_air_gap_rule_and_its_build_time_exception(flat):
    assert "Air-gap default" in flat
    assert "no telemetry" in flat.lower()
    assert "continuous-integration tooling may use the network" in flat


# -- the checklist that the pull-request template mirrors --------------------


def test_the_guide_has_a_checklist_of_what_a_change_must_satisfy(doc):
    section = _section(doc, "What a change has to satisfy")
    boxes = [line for line in section.splitlines() if line.strip().startswith("- [ ]")]
    assert len(boxes) >= 10, f"only {len(boxes)} checklist items"


def test_the_guide_points_at_the_other_governance_documents(doc):
    for path in ("SECURITY.md", "CODE_OF_CONDUCT.md", "docs/COMMANDS.md"):
        assert path in doc, f"{path} not referenced"


def test_the_guide_tells_a_contributor_to_document_a_new_command(doc, flat):
    assert "docs/COMMANDS.md" in doc
    assert "tests/unit/test_docs_commands_complete.py" in doc
    assert "fails when a registered subcommand" in flat


# -- helpers -----------------------------------------------------------------


def _declared_extras() -> set[str]:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    try:
        import tomllib
    except ModuleNotFoundError:
        pass
    else:
        return set(tomllib.loads(text).get("project", {}).get("optional-dependencies", {}))
    inside = False
    found: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "[project.optional-dependencies]":
            inside = True
            continue
        if not inside:
            continue
        if stripped.startswith("[") and stripped.endswith("]") and "=" not in stripped:
            break
        m = re.match(r'^([A-Za-z][A-Za-z0-9._-]*)\s*=\s*\[', stripped)
        if m:
            found.add(m.group(1))
    return found


def _section(doc: str, heading: str) -> str:
    lines = doc.splitlines()
    start = level = None
    for i, line in enumerate(lines):
        m = re.match(r"^(#{2,4})\s+(.*)$", line)
        if m and m.group(2).strip() == heading:
            start, level = i, len(m.group(1))
            break
    assert start is not None, f"CONTRIBUTING.md has no heading {heading!r}"
    body: list[str] = []
    for line in lines[start + 1 :]:
        m = re.match(r"^(#{1,6})\s", line)
        if m and len(m.group(1)) <= level:
            break
        body.append(line)
    return "\n".join(body)


# -- structure, added after an adversarial review ---------------------------
#
# A review deleted the whole "What contributing means under this licence"
# section and all 22 tests still passed: every assertion looked for a phrase
# somewhere in the flattened document, and the phrases it checked happened to
# appear elsewhere too. A guide can therefore lose the section that sets out
# what contributing means here, which is the one section a generic
# open-source-shaped guide would not have, without anything noticing.


REQUIRED_SECTIONS = (
    "What contributing means under this licence",
    "Development in a clone",
    "The test and gate commands",
    "Coding conventions",
    "What a change has to satisfy",
    "Reporting rather than patching",
)


def test_every_required_section_is_present_as_a_heading(doc):
    headings = {line[3:].strip() for line in doc.splitlines() if line.startswith("## ")}
    missing = [s for s in REQUIRED_SECTIONS if s not in headings]
    assert not missing, f"the contributor guide has lost these sections: {missing}"


def test_the_licence_section_is_first_because_it_changes_what_the_rest_means(doc):
    headings = [line[3:].strip() for line in doc.splitlines() if line.startswith("## ")]
    assert headings, "no sections at all"
    assert headings[0] == REQUIRED_SECTIONS[0], (
        f"the guide opens with {headings[0]!r}. Under this licence a contributor cannot publish a "
        "modified version, so that has to be said before the instructions for changing the code."
    )


def test_the_licence_section_says_what_it_has_to_say(doc):
    body = doc.split("## What contributing means under this licence", 1)[1].split("\n## ", 1)[0]
    flat = re.sub(r"\s+", " ", body).lower()
    assert "not permitted" in flat or "prohibited" in flat or "cannot" in flat, (
        "the section does not state a prohibition, so it reads like an ordinary "
        "open-source contributing guide"
    )

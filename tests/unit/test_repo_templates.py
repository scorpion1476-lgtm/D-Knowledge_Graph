"""Issue and pull-request templates must demand what makes a report actionable.

A bug report without the version, the platform, the extras present, a
reproduction, and expected against observed behaviour costs a round trip before
anyone can even look. The requirement names exactly those five, and the bug form
is checked here for each of them, marked required rather than merely suggested.

The pull-request checklist is checked against `CONTRIBUTING.md` rather than
against a list this file invents, so the two cannot drift apart: a rule dropped
from either document fails this.

The forms are parsed structurally. PyYAML is used when it is available, and a
small deliberate parser is used otherwise, because PyYAML is a development
dependency and this suite has to pass without one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / ".github" / "ISSUE_TEMPLATE"
BUG = TEMPLATES / "bug_report.yml"
FEATURE = TEMPLATES / "feature_request.yml"
DOCS = TEMPLATES / "documentation.yml"
CONFIG = TEMPLATES / "config.yml"
PR = ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md"

# The five the requirement names, mapped to the field that must carry each.
REQUIRED_BUG_FIELDS = {
    "version": "version",
    "platform": "platform",
    "extras present": "extras",
    "reproduction": "reproduction",
    "expected behaviour": "expected",
    "observed behaviour": "observed",
}

# Rules that must appear in BOTH the contributor guide and the pull-request
# checklist. Each entry is (a phrase for the guide, a phrase for the template).
MIRRORED_RULES = (
    ("licence", "licence"),
    ("permissive", "permissive"),
    ("GPL", "GPL"),
    ("network call", "network call"),
    ("runtime", "runtime"),
    ("capability-detected", "capability-detected"),
    ("test that can fail", "test"),
    ("test-evidence/", "test-evidence/"),
    ("em dash", "em dash"),
    ("docs/COMMANDS.md", "docs/COMMANDS.md"),
    ("REQUIREMENTS_TRACEABILITY_MATRIX", "requirement rows"),
)


def _fields(path: Path) -> list[dict]:
    """The `body` items of an issue form, as mappings."""
    text = path.read_text(encoding="utf-8")
    try:
        import yaml
    except ImportError:
        return _fields_without_yaml(text)
    loaded = yaml.safe_load(text)
    assert isinstance(loaded, dict), f"{path} is not a mapping"
    return list(loaded.get("body", []))


def _fields_without_yaml(text: str) -> list[dict]:
    """A deliberately small stand-in for the shapes this project's forms use.

    Only enough to answer the two questions the tests ask: which field ids exist,
    and which of them are marked required.
    """
    items: list[dict] = []
    current: list[str] = []
    for line in text.splitlines():
        if re.match(r"^  - type:", line):
            if current:
                items.append(_one_field(current))
            current = [line]
        elif current:
            current.append(line)
    if current:
        items.append(_one_field(current))
    return items


def _one_field(lines: list[str]) -> dict:
    block = "\n".join(lines)
    field: dict = {"type": ""}
    m = re.search(r"^  - type:\s*(\S+)", block, re.M)
    if m:
        field["type"] = m.group(1)
    m = re.search(r"^    id:\s*(\S+)", block, re.M)
    if m:
        field["id"] = m.group(1)
    m = re.search(r"^      label:\s*(.+)$", block, re.M)
    if m:
        field.setdefault("attributes", {})["label"] = m.group(1).strip()
    if re.search(r"^      required:\s*true", block, re.M):
        field["validations"] = {"required": True}
    return field


@pytest.fixture(scope="module")
def bug_fields() -> list[dict]:
    assert BUG.is_file(), f"{BUG} does not exist"
    return _fields(BUG)


# -- the templates exist where the forge looks for them ----------------------


def test_every_template_exists():
    for path in (BUG, FEATURE, DOCS, CONFIG, PR):
        assert path.is_file(), f"{path} does not exist"


def test_blank_issues_are_disabled(_=None):
    text = CONFIG.read_text(encoding="utf-8")
    assert re.search(r"^blank_issues_enabled:\s*false", text, re.M), (
        "a blank issue skips every required field, which defeats the templates"
    )


def test_the_config_routes_security_reports_away_from_public_issues():
    text = CONFIG.read_text(encoding="utf-8")
    assert "Security vulnerability" in text
    assert "SECURITY.md" in text
    assert "Do not open a public issue" in text


def test_the_config_points_at_documents_that_exist():
    text = CONFIG.read_text(encoding="utf-8")
    for cited in re.findall(r"(?:blob/main/)((?:docs/)?[A-Z_]+[A-Za-z_]*\.md)", text):
        assert (ROOT / cited).is_file(), f"the config links {cited}, which does not exist"


# -- the bug form demands the five things the requirement names --------------


def test_the_bug_form_has_a_field_for_each_required_fact(bug_fields):
    ids = {f.get("id") for f in bug_fields}
    missing = sorted(
        label for label, field_id in REQUIRED_BUG_FIELDS.items() if field_id not in ids
    )
    assert not missing, f"the bug form asks for no {missing}"


def test_each_of_those_fields_is_marked_required(bug_fields):
    """Suggested is not required; a suggested field is the one people skip."""
    by_id = {f.get("id"): f for f in bug_fields}
    optional = [
        field_id
        for field_id in REQUIRED_BUG_FIELDS.values()
        if not (by_id.get(field_id, {}).get("validations") or {}).get("required")
    ]
    assert not optional, f"fields that should be required but are not: {sorted(optional)}"


def test_the_bug_form_asks_for_extras_through_the_real_commands():
    text = BUG.read_text(encoding="utf-8")
    assert "scripts/probe_environment.py" in text
    assert (ROOT / "scripts" / "probe_environment.py").is_file()
    assert "dkg capabilities" in text


def test_the_bug_form_routes_security_problems_elsewhere():
    text = BUG.read_text(encoding="utf-8")
    assert "SECURITY.md" in text


def test_the_bug_form_lists_the_platforms_honestly():
    """Windows is not exercised, so a report from it is worth asking for."""
    text = re.sub(r"\s+", " ", BUG.read_text(encoding="utf-8"))
    for option in ("macOS", "Linux (bare metal)", "Windows", "Windows Subsystem for Linux"):
        assert option in text, f"{option} missing from the platform options"
    assert "not exercised" in text


def test_the_bug_form_points_at_the_troubleshooting_document():
    text = BUG.read_text(encoding="utf-8")
    assert "docs/TROUBLESHOOTING.md" in text
    assert (ROOT / "docs" / "TROUBLESHOOTING.md").is_file()


# -- the other forms ---------------------------------------------------------


def test_the_feature_form_points_at_the_roadmap_and_the_command_reference():
    text = FEATURE.read_text(encoding="utf-8")
    assert "docs/ROADMAP.md" in text
    assert "docs/COMMANDS.md" in text
    assert (ROOT / "docs" / "ROADMAP.md").is_file()


def test_the_feature_form_states_the_standing_constraints():
    text = re.sub(r"\s+", " ", FEATURE.read_text(encoding="utf-8"))
    for phrase in ("permissively licensed", "downloads no model at runtime", "read-only"):
        assert phrase in text, f"constraint missing from the feature form: {phrase}"


def test_a_documentation_form_exists_for_an_overstated_claim():
    text = re.sub(r"\s+", " ", DOCS.read_text(encoding="utf-8"))
    assert "honest labelling" in text
    assert "test-evidence/" in text
    assert "missing caveat" in text.lower()


# -- the pull-request checklist mirrors the contributor guide ----------------


def test_the_pull_request_template_has_a_checklist():
    text = PR.read_text(encoding="utf-8")
    boxes = [line for line in text.splitlines() if line.strip().startswith("- [ ]")]
    assert len(boxes) >= 20, f"only {len(boxes)} checklist items"


def test_the_checklist_mirrors_the_contributor_guide():
    """Neither document may drop a rule the other still states."""
    guide = re.sub(r"\s+", " ", (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8"))
    template = re.sub(r"\s+", " ", PR.read_text(encoding="utf-8"))
    missing = []
    for in_guide, in_template in MIRRORED_RULES:
        if in_guide.lower() not in guide.lower():
            missing.append(f"CONTRIBUTING.md no longer states {in_guide!r}")
        if in_template.lower() not in template.lower():
            missing.append(f"the pull-request template omits {in_template!r}")
    assert not missing, "; ".join(missing)


def test_the_template_tells_the_contributor_to_read_the_guide_first():
    text = re.sub(r"\s+", " ", PR.read_text(encoding="utf-8"))
    assert "CONTRIBUTING.md" in text
    assert "PolyForm Noncommercial 1.0.0" in text
    assert "publishing a modified fork is not permitted" in text


def test_the_template_asks_for_the_mutation_that_proved_the_test_can_fail():
    text = re.sub(r"\s+", " ", PR.read_text(encoding="utf-8"))
    assert "Mutation used to prove it fails" in text
    assert "A test that has never failed is not evidence" in text


def test_every_gate_command_in_the_template_names_a_script_that_exists():
    text = PR.read_text(encoding="utf-8")
    named = set(re.findall(r"`(?:python|bash) (scripts/[A-Za-z0-9_.-]+)", text))
    assert named, "the template lists no gate commands"
    missing = sorted(p for p in named if not (ROOT / p).is_file())
    assert not missing, f"gate scripts named in the template that do not exist: {missing}"


def test_the_template_covers_every_gate_the_completion_hook_runs():
    gate = (ROOT / "scripts" / "stop_gate.sh").read_text(encoding="utf-8")
    template = PR.read_text(encoding="utf-8")
    for script in sorted(set(re.findall(r"scripts/[A-Za-z0-9_.-]+", gate))):
        if script == "scripts/stop_gate.sh":
            continue
        assert script in template, f"{script} blocks the gate but the template omits it"

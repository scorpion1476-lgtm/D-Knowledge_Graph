"""The user guide must carry a runnable example for each named workflow.

Acceptance test for matrix row J-05, whose requirement names seven workflows by
name: research, verification, contradiction, export, backup, restore and
orchestration. A manual review can confirm the words appear. It cannot confirm
that the example beside each word is a command this build would accept, and
that is the only failure a reader ever hits.

So each of the seven is bound here to a concrete, checkable obligation: a
section in the document, and at least one ``dkg`` invocation inside it whose
subcommand the real parser registers, whose long flags that subcommand accepts,
and, for the agent workflows, whose workflow name is one the ``agent``
subcommand actually offers. The agent-name check is the one that matters most:
the workflow names are a closed ``choices`` list, so a renamed workflow leaves
the guide printing a command that exits with a parser error.

The guide is also required to keep saying that the agent workflows run with no
model connected. That sentence is the difference between an honest local tool
and one that quietly assumes an API key.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

from dkg.cli.entry import _mk_parser

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "USER_GUIDE.md"

# requirement word -> the heading in the guide that must carry its example
WORKFLOW_SECTIONS = {
    "research": "Multi agent workflows",
    "verification": "Verify evidence",
    "contradiction": "Multi agent workflows",
    "export": "Export and backup",
    "backup": "Export and backup",
    "restore": "Export and backup",
    "orchestration": "Multi agent workflows",
}

# requirement word -> a dkg subcommand that must appear in that section
WORKFLOW_COMMANDS = {
    "verification": "evidence",
    "export": "export",
    "backup": "backup",
    "restore": "restore",
}

# requirement word -> an `agent` workflow name that must appear in that section
WORKFLOW_AGENTS = {
    "research": "research",
    "contradiction": "contradiction",
}


@pytest.fixture(scope="module")
def doc() -> str:
    assert DOC.is_file(), f"{DOC} does not exist"
    return DOC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def sections(doc: str) -> dict[str, str]:
    out: dict[str, str] = {}
    current, body = None, []
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
def subparsers() -> dict[str, argparse.ArgumentParser]:
    action = next(
        a for a in _mk_parser()._actions if isinstance(a, argparse._SubParsersAction)
    )
    return dict(action.choices)


@pytest.fixture(scope="module")
def agent_workflows(subparsers) -> set[str]:
    workflow = next(a for a in subparsers["agent"]._actions if a.dest == "workflow")
    assert workflow.choices, "the agent subcommand no longer offers a closed workflow list"
    return set(workflow.choices)


def _dkg_invocations(text: str) -> list[list[str]]:
    calls: list[list[str]] = []
    for block in re.findall(r"```bash\n(.*?)```", text, re.S):
        for line in block.splitlines():
            line = line.split("#", 1)[0].strip()
            m = re.match(r"^(?:\./)?(?:[\w./-]*/)?dkg\s+(.*)$", line)
            if m:
                calls.append([t for t in m.group(1).split() if t])
    return calls


# -- every named workflow has a section and an example ------------------------


@pytest.mark.parametrize(("workflow", "heading"), sorted(WORKFLOW_SECTIONS.items()))
def test_each_named_workflow_has_a_section(sections, workflow, heading):
    assert heading in sections, f"{workflow}: the guide has no {heading!r} section"
    assert _dkg_invocations(sections[heading]), f"{workflow}: {heading!r} carries no example command"


@pytest.mark.parametrize(("workflow", "command"), sorted(WORKFLOW_COMMANDS.items()))
def test_each_command_workflow_shows_its_own_command(sections, workflow, command):
    heading = WORKFLOW_SECTIONS[workflow]
    subs = {c[0] for c in _dkg_invocations(sections[heading]) if c}
    assert command in subs, f"{workflow}: {heading!r} never runs `dkg {command}`"


@pytest.mark.parametrize(("workflow", "name"), sorted(WORKFLOW_AGENTS.items()))
def test_each_agent_workflow_example_names_a_real_workflow(
    sections, agent_workflows, workflow, name
):
    heading = WORKFLOW_SECTIONS[workflow]
    used = {c[1] for c in _dkg_invocations(sections[heading]) if c and c[0] == "agent" and len(c) > 1}
    assert name in used, f"{workflow}: {heading!r} never runs `dkg agent {name}`"
    assert name in agent_workflows, f"`dkg agent {name}` is not a workflow this build offers"


def test_orchestration_shows_more_than_one_agent(sections, agent_workflows):
    """Orchestration is a distinct requirement from running a single agent.

    One example would satisfy "research" and leave "orchestration" undocumented,
    so the section has to show the coordinator running several.
    """
    used = {
        c[1]
        for c in _dkg_invocations(sections["Multi agent workflows"])
        if c and c[0] == "agent" and len(c) > 1
    }
    assert len(used) >= 3, f"orchestration is shown with only {sorted(used)}"
    unknown = used - agent_workflows
    assert not unknown, f"the guide names agent workflows this build does not offer: {unknown}"


# -- the examples are real ----------------------------------------------------


def test_every_documented_subcommand_exists(doc, subparsers):
    unknown = sorted({c[0] for c in _dkg_invocations(doc) if c and c[0] not in subparsers})
    assert not unknown, f"the user guide names subcommands this build does not register: {unknown}"


def test_every_documented_long_flag_is_accepted_by_its_subcommand(doc, subparsers):
    offenders: list[str] = []
    for call in _dkg_invocations(doc):
        if not call or call[0] not in subparsers:
            continue
        accepted = {opt for action in subparsers[call[0]]._actions for opt in action.option_strings}
        for token in call[1:]:
            if token.startswith("--") and token.split("=", 1)[0] not in accepted:
                offenders.append(f"dkg {call[0]} {token.split('=', 1)[0]}")
    assert not offenders, f"flags the subcommand does not accept: {sorted(set(offenders))}"


def test_documented_export_formats_are_ones_the_command_offers(sections, subparsers):
    """`--format` is a closed choice list, so a stale format is a hard error."""
    action = next(a for a in subparsers["export"]._actions if a.dest == "format")
    offered = set(action.choices or ())
    assert offered, "the export command no longer offers a closed format list"
    shown = {
        call[call.index("--format") + 1]
        for call in _dkg_invocations(sections["Export and backup"])
        if "--format" in call and call.index("--format") + 1 < len(call)
    }
    assert shown, "the export section shows no --format example"
    unknown = shown - offered
    assert not unknown, f"the guide shows export formats this build does not offer: {unknown}"


# -- honesty ------------------------------------------------------------------


def test_the_guide_states_the_agent_workflows_run_without_a_model(sections):
    flat = re.sub(r"\s+", " ", sections["Multi agent workflows"]).lower()
    assert "without a model" in flat, (
        "the orchestration section must keep saying the workflows need no model; "
        "that claim is the reason they are usable offline"
    )

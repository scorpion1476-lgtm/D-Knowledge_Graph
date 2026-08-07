"""`docs/COMMANDS.md` must cover every surface this build actually registers.

The point of this file is that the document cannot quietly fall behind the code.
Nothing here is compared against a string the document also supplies: the
subcommands come from the real `argparse` parser, the tools come from the real
MCP registry, the option strings come from the parser's own actions, and the
tool parameters come from each tool's own input schema.

So when a sibling change adds a subcommand, adds a flag to an existing
subcommand, or registers a new MCP tool, this goes red until the document
catches up. The fix is always to document the command. Relaxing a check here to
make a red run green would defeat the only thing this file is for.

The document is reference material, so the checks are about coverage rather than
wording: a heading for every command, and a mention of every parameter inside
that command's own section.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

from dkg.cli.entry import _mk_parser
from dkg.core.db import open_database
from dkg.mcp.tools import build_read_registry

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "COMMANDS.md"


@pytest.fixture(scope="module")
def doc() -> str:
    assert DOC.is_file(), f"{DOC} does not exist"
    return DOC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def sections(doc: str) -> dict[str, str]:
    """Split the document into its `### ` sections, keyed by the heading text.

    The section body is what a per-command check reads, so a parameter
    documented under some other command does not count.
    """
    out: dict[str, str] = {}
    current = None
    body: list[str] = []
    for line in doc.splitlines():
        if line.startswith("### "):
            if current is not None:
                out[current] = "\n".join(body)
            current = line[4:].strip().strip("`")
            body = []
        elif current is not None:
            body.append(line)
    if current is not None:
        out[current] = "\n".join(body)
    return out


@pytest.fixture(scope="module")
def subcommands() -> dict[str, argparse.ArgumentParser]:
    parser = _mk_parser()
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return dict(action.choices)


@pytest.fixture(scope="module")
def tools(tmp_path_factory) -> dict:
    """The real registry, built against an empty database.

    Empty on purpose: the registry's shape is a property of the build, not of
    any graph, so this must work on a machine with no optional extra installed.
    """
    path = tmp_path_factory.mktemp("commands") / "g.db"
    with open_database(path) as db:
        registry = build_read_registry(db)
        return {name: spec for name, spec in registry.tools.items()}


# -- the document exists and is shaped the way the test expects --------------


def test_the_document_has_both_reference_sections(doc):
    assert "## Command-line subcommands" in doc
    assert "## MCP tools" in doc


def test_the_document_says_how_to_add_a_command(doc):
    """Three sibling surfaces add commands in parallel; the recipe must be here."""
    assert "## How to add a command to this document" in doc


# -- every registered subcommand is documented -------------------------------


def test_every_registered_subcommand_has_a_section(subcommands, sections):
    missing = sorted(
        name for name in subcommands if f"dkg {name}" not in sections
    )
    assert not missing, (
        "docs/COMMANDS.md is missing a section for these registered subcommands: "
        f"{missing}. Add '### `dkg <name>`' with its parameter table."
    )


def test_no_documented_subcommand_has_been_removed(subcommands, sections):
    """A section for a command that no longer exists is just as wrong."""
    documented = {
        heading[len("dkg ") :]
        for heading in sections
        if heading.startswith("dkg ") and " " not in heading[len("dkg ") :]
    }
    stale = sorted(documented - set(subcommands))
    assert not stale, f"documented but no longer registered: {stale}"


def test_every_option_of_every_subcommand_is_documented(subcommands, sections):
    """Each option string, including every alias, inside its own section."""
    missing: list[str] = []
    for name, parser in subcommands.items():
        body = sections.get(f"dkg {name}", "")
        for action in parser._actions:
            if isinstance(action, argparse._HelpAction):
                continue
            wanted = list(action.option_strings) or [action.dest]
            for token in wanted:
                if f"`{token}`" not in body:
                    missing.append(f"dkg {name}: {token}")
    assert not missing, "undocumented parameters: " + "; ".join(sorted(missing))


def test_every_subcommand_section_states_a_default_or_says_it_takes_none(
    subcommands, sections
):
    """A reference without defaults is not a reference."""
    thin: list[str] = []
    for name, parser in subcommands.items():
        body = sections.get(f"dkg {name}", "")
        takes_parameters = any(
            not isinstance(a, argparse._HelpAction) for a in parser._actions
        )
        if takes_parameters:
            if "| Parameter | Kind | Required | Default | Notes |" not in body:
                thin.append(f"dkg {name}: no parameter table")
        elif "Takes no parameter" not in body:
            thin.append(f"dkg {name}: does not say it takes no parameter")
    assert not thin, thin


# -- every registered MCP tool is documented ---------------------------------


def test_every_registered_mcp_tool_has_a_section(tools, sections):
    missing = sorted(name for name in tools if name not in sections)
    assert not missing, (
        "docs/COMMANDS.md is missing a section for these registered MCP tools: "
        f"{missing}. Add '### `<tool name>`' with its parameter table."
    )


def test_no_documented_mcp_tool_has_been_removed(tools, sections):
    documented = {h for h in sections if h.startswith("dkg.")}
    stale = sorted(documented - set(tools))
    assert not stale, f"documented but no longer registered: {stale}"


def test_every_parameter_of_every_mcp_tool_is_documented(tools, sections):
    missing: list[str] = []
    for name, spec in tools.items():
        body = sections.get(name, "")
        for prop in spec.input_schema.get("properties", {}):
            if f"`{prop}`" not in body:
                missing.append(f"{name}: {prop}")
    assert not missing, "undocumented tool parameters: " + "; ".join(sorted(missing))


def test_every_required_mcp_parameter_is_marked_required(tools, sections):
    """Required and optional must not be swapped in the table."""
    wrong: list[str] = []
    for name, spec in tools.items():
        body = sections.get(name, "")
        required = set(spec.input_schema.get("required", []))
        for line in body.splitlines():
            m = re.match(r"\|\s*`(\w+)`\s*\|[^|]*\|\s*(yes|no)\s*\|", line)
            if not m:
                continue
            prop, marked = m.group(1), m.group(2)
            if prop not in spec.input_schema.get("properties", {}):
                continue
            if (prop in required) != (marked == "yes"):
                wrong.append(f"{name}.{prop}: table says required={marked}")
    assert not wrong, wrong


def test_documented_mcp_defaults_match_the_registry_constraints(tools, sections):
    """An enum in the table must be the enum in the schema.

    Cheap to state and easy to get wrong by hand: a documented choice list that
    has drifted from the schema sends a caller to an argument the server will
    reject.
    """
    wrong: list[str] = []
    for name, spec in tools.items():
        body = sections.get(name, "")
        for prop, pspec in spec.input_schema.get("properties", {}).items():
            if "enum" not in pspec:
                continue
            for value in pspec["enum"]:
                row = next(
                    (ln for ln in body.splitlines() if ln.startswith(f"| `{prop}` |")), ""
                )
                if f"`{value}`" not in row:
                    wrong.append(f"{name}.{prop}: table omits the choice {value!r}")
    assert not wrong, wrong


# -- the counts the document implies are the real ones ------------------------


def test_the_document_documents_no_tool_it_invented(tools, sections, subcommands):
    """Every `### ` heading must be a real subcommand or a real tool."""
    unknown = sorted(
        heading
        for heading in sections
        if not (
            heading in tools
            or (heading.startswith("dkg ") and heading[len("dkg ") :] in subcommands)
        )
    )
    assert not unknown, f"headings that name nothing this build registers: {unknown}"

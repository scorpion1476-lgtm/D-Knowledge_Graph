"""R-14: three workflow commands, each naming real tools and real bounds.

The requirement names three recurring tasks: build or update the graph, review
the uncommitted delta, and review a branch or pull-request diff. One shipped
command each.

The test that matters most is the anti-drift one. A workflow document is only
useful if the commands it tells an assistant to run exist, so every CLI name it
claims is checked against the live argument parser and every MCP tool name
against the live read-only registry. A command renamed elsewhere in the project
breaks this file rather than quietly shipping instructions that fail at the
first step.
"""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

import pytest

from dkg.cli.entry import _mk_parser
from dkg.core.db import open_database
from dkg.mcp import artifacts, configure, platforms
from dkg.mcp.tools import build_read_registry

FIXED = configure.Launch(
    command="dkg",
    prefix_args=(),
    runner="console-script",
    installed_by="system",
    resolves=True,
    basis="pinned by the test",
)

#: The three recurring tasks, and the shipped command that covers each.
EXPECTED = {
    "dkg-graph-build": ("build", "update"),
    "dkg-review-uncommitted": ("uncommitted", "working"),
    "dkg-review-branch-diff": ("branch", "diff"),
}


@pytest.fixture(scope="module")
def cli_names() -> set[str]:
    for action in _mk_parser()._actions:
        if action.dest == "cmd" and getattr(action, "choices", None):
            return set(action.choices)
    raise AssertionError("could not find the subcommand action on the CLI parser")


@pytest.fixture(scope="module")
def mcp_names(tmp_path_factory) -> set[str]:
    path = tmp_path_factory.mktemp("registry") / "graph.sqlite"
    with open_database(path) as db:
        return {tool["name"] for tool in build_read_registry(db).list()}


def test_exactly_the_three_recurring_tasks_are_shipped():
    commands = artifacts.workflow_commands()
    assert [c.name for c in commands] == sorted(EXPECTED)
    for command in commands:
        haystack = f"{command.title} {command.description}".lower()
        assert any(word in haystack for word in EXPECTED[command.name]), command.name


def test_the_workflow_documents_are_in_the_shipped_skill_package():
    """Shipped, not generated: they live in the package the wheel installs."""
    shipped = {
        entry.name for entry in resources.files("dkg.skills").iterdir() if entry.name.endswith(".md")
    }
    for command in artifacts.workflow_commands():
        assert f"{command.name}.md" in shipped, command.name
    assert "dkg-usage.md" in shipped


def test_every_cli_name_a_command_claims_is_a_real_subcommand(cli_names):
    for command in artifacts.workflow_commands():
        assert command.cli, command.name
        for name in command.cli:
            assert name in cli_names, (command.name, name)


def test_every_mcp_tool_a_command_claims_is_on_the_live_registry(mcp_names):
    for command in artifacts.workflow_commands():
        assert command.mcp, command.name
        for name in command.mcp:
            assert name in mcp_names, (command.name, name)


def test_every_command_invocation_in_the_body_is_a_real_subcommand(cli_names):
    """The frontmatter could be right while the body invented a command.

    Every ``dkg <word>`` in a fenced block is checked, so an example that would
    fail on the user's first attempt fails here first.
    """
    checked = 0
    for command in artifacts.workflow_commands():
        for invocation in re.findall(r"^\s*dkg ([a-z][a-z-]*)", command.body, flags=re.MULTILINE):
            checked += 1
            assert invocation in cli_names, (command.name, invocation)
    assert checked >= 9, "the bodies contain almost no runnable examples"


def test_every_mcp_tool_named_in_a_body_is_on_the_live_registry(mcp_names):
    checked = 0
    for command in artifacts.workflow_commands():
        for name in re.findall(r"`(dkg\.[a-z_.]+)`", command.body):
            checked += 1
            assert name in mcp_names, (command.name, name)
    assert checked >= 6, "the bodies name almost no MCP tools"


def test_every_flag_used_in_a_body_is_accepted_by_that_subcommand(cli_names):
    """A flag that does not exist is the same failure as a command that does not.

    Each fenced example is parsed by the real parser, so an example using a flag
    that was renamed elsewhere in the project fails here.
    """
    parser = _mk_parser()
    checked = 0
    for command in artifacts.workflow_commands():
        for line in command.body.splitlines():
            stripped = line.strip()
            if not stripped.startswith("dkg ") or "&&" in stripped or "|" in stripped:
                continue
            argv = [part for part in stripped.split() if not part.startswith('"')]
            if any(part.startswith('"') or part.startswith("'") for part in stripped.split()):
                continue
            checked += 1
            parser.parse_args(argv[1:])
    assert checked >= 8, "almost no example was actually parsed"


def test_each_command_names_the_tools_it_drives_and_the_bounds_it_runs_under():
    for command in artifacts.workflow_commands():
        assert command.bounds.strip(), command.name
        body = command.body
        assert "## Tools this drives" in body, command.name
        assert "## Bounds this runs under" in body, command.name
        # The honest caveats this project requires of any structural answer.
        lowered = body.lower()
        assert "read-only" in lowered or "read only" in lowered, command.name
        assert "no network" in lowered or "offline" in lowered, command.name
        for name in command.cli:
            assert f"dkg {name}" in body, (command.name, name)
        for name in command.mcp:
            assert name in body, (command.name, name)


def test_the_review_commands_say_the_analysis_is_advisory_and_over_approximate():
    """The two review workflows quote structural results, so they must caveat them."""
    for name in ("dkg-review-uncommitted", "dkg-review-branch-diff"):
        command = next(c for c in artifacts.workflow_commands() if c.name == name)
        lowered = command.body.lower()
        assert "over-approximate" in lowered, name
        assert "advisory" in lowered, name
        assert "bounded" in lowered or "max-nodes" in lowered, name


def test_the_commands_reach_every_tool_that_takes_a_command_package(tmp_path):
    """Shipping them is not enough; they have to arrive."""
    configure.install_all(
        config_root=tmp_path,
        dkg_home=tmp_path / "h",
        launch=FIXED,
        platform_key="linux",
        only_detected=False,
    )
    with_directories = [
        target
        for target in platforms.platforms()
        if target.commands is not None and target.commands.directory is not None
    ]
    assert len(with_directories) >= 8
    for target in with_directories:
        directory = tmp_path / platforms.resolve_relative(target.commands.directory, "linux")
        written = sorted(p.stem.replace(".prompt", "") for p in directory.iterdir())
        assert written == sorted(EXPECTED), (target.name, written)


def test_the_rendered_command_states_the_tools_and_bounds_in_every_format(tmp_path):
    configure.install_all(
        config_root=tmp_path,
        dkg_home=tmp_path / "h",
        launch=FIXED,
        platform_key="linux",
        only_detected=False,
    )
    rendered = [
        (tmp_path / ".claude" / "commands" / "dkg-graph-build.md"),
        (tmp_path / ".gemini" / "commands" / "dkg-graph-build.toml"),
        (tmp_path / ".aws" / "amazonq" / "prompts" / "dkg-graph-build.md"),
    ]
    command = next(c for c in artifacts.workflow_commands() if c.name == "dkg-graph-build")
    for path in rendered:
        text = path.read_text(encoding="utf-8")
        assert "Drives CLI: " in text, path
        assert "Drives MCP tools: " in text, path
        assert f"Bounds: {command.bounds}." in text, path
        assert artifacts.TEXT_MARKER in text, path


def test_no_dash_characters_that_the_project_forbids():
    # Built from codepoints, not written literally: spelling them out would
    # put them in a tracked file and trip the gate this test is guarding.
    em, en = chr(0x2014), chr(0x2013)
    for entry in resources.files("dkg.skills").iterdir():
        if not entry.name.endswith(".md"):
            continue
        text = Path(str(entry)).read_text(encoding="utf-8")
        assert em not in text, entry.name
        assert en not in text, entry.name

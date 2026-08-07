"""The shipped skill package: the usage document and the workflow commands.

Unit level, so it checks the package contents and the frontmatter parser rather
than what any tool does with them. The cross-checks against the live CLI and
MCP surfaces live in ``tests/integration/test_workflow_commands.py``.
"""

from importlib import resources

import pytest

from dkg.core.errors import ValidationError
from dkg.mcp import artifacts


def test_skill_file_present():
    text = resources.files("dkg.skills").joinpath("dkg-usage.md").read_text(encoding="utf-8")
    assert text.startswith("# dkg-usage")
    assert "Golden path" in text


def test_three_workflow_commands_are_shipped():
    commands = artifacts.workflow_commands()
    assert len(commands) == 3
    assert [c.name for c in commands] == sorted(c.name for c in commands)
    for command in commands:
        assert command.title.strip()
        assert command.description.strip()
        assert command.bounds.strip()
        assert command.cli
        assert command.mcp
        assert command.body.strip()


def test_discovery_is_by_frontmatter_not_by_filename():
    """The usage document has no frontmatter and must not be picked up.

    A filename-prefix rule would have swept it in, and it would then be offered
    as a slash command that does nothing in particular.
    """
    names = {c.name for c in artifacts.workflow_commands()}
    assert "dkg-usage" not in names


def test_the_frontmatter_parser_reads_a_well_formed_block():
    fields, body = artifacts._parse_frontmatter(
        "---\nname: x\ncli: a, b ,c\n---\nthe body\n"
    )
    assert fields == {"name": "x", "cli": "a, b ,c"}
    assert body == "the body\n"
    assert artifacts._split_list(fields["cli"]) == ("a", "b", "c")


def test_a_document_with_no_frontmatter_is_left_whole():
    fields, body = artifacts._parse_frontmatter("# just a heading\n")
    assert fields == {}
    assert body == "# just a heading\n"


def test_an_unterminated_frontmatter_block_is_refused():
    with pytest.raises(ValidationError):
        artifacts._parse_frontmatter("---\nname: x\nno closing fence\n")


def test_a_frontmatter_line_that_is_not_a_pair_is_refused():
    with pytest.raises(ValidationError):
        artifacts._parse_frontmatter("---\nname: x\nnot a pair\n---\nbody\n")


def test_rendering_is_deterministic():
    command = artifacts.workflow_commands()[0]
    for fmt in ("markdown", "markdown-plain", "toml"):
        assert artifacts.render_command(command, fmt) == artifacts.render_command(command, fmt)


def test_an_unknown_render_format_is_refused():
    with pytest.raises(ValidationError):
        artifacts.render_command(artifacts.workflow_commands()[0], "yaml")


def test_the_toml_rendering_escapes_what_toml_needs_escaped():
    for command in artifacts.workflow_commands():
        rendered = artifacts.render_command(command, "toml")
        prompt = rendered.split('prompt = """', 1)[1]
        # The only triple quote left is the one closing the string.
        assert prompt.count('"""') == 1

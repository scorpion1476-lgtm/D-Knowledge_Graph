"""R-09: graph-aware guidance injected inside clearly delimited managed markers.

The three properties the requirement names, each with its own test:

* re-injection replaces only the marked block, and running twice produces a
  byte-identical file,
* a user's own text outside the markers survives verbatim, including text the
  user typed on the line immediately above the begin marker and the line
  immediately below the end marker,
* removal takes out exactly the block and nothing else, so an install followed
  by an uninstall restores a newline-terminated file byte for byte.

Plus the honesty check that matters most for guidance: every MCP tool the block
tells an assistant to call has to exist on the live read-only registry, so the
guidance cannot drift into naming tools this project does not serve.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from dkg.core.db import open_database
from dkg.core.errors import ValidationError
from dkg.mcp import configure, platforms, rules
from dkg.mcp.tools import build_read_registry

FIXED = configure.Launch(
    command="dkg",
    prefix_args=(),
    runner="console-script",
    installed_by="system",
    resolves=True,
    basis="pinned by the test",
)

USER_TEXT = (
    "# My own rules\n"
    "\n"
    "Always run the linter before you claim a change is done.\n"
    "Never touch the vendored directory.\n"
)


def _rules_path(tool: str, root: Path, platform_key: str = "linux") -> Path:
    target = next(p for p in platforms.platforms() if p.name == tool)
    return root / platforms.resolve_relative(target.rules, platform_key)


def test_the_block_is_delimited_and_self_describing():
    block = rules.guidance_block()
    assert block.startswith(rules.BEGIN_MARKER + "\n")
    assert block.endswith(rules.END_MARKER + "\n")
    assert configure.OWNER_MARKER in rules.BEGIN_MARKER
    assert configure.OWNER_MARKER in rules.END_MARKER
    assert configure.SERVER_NAME in block
    # The bounds are part of the guidance, not an afterthought.
    for phrase in ("read-only", "offline", "over-approximate", "advisory"):
        assert phrase in block, phrase


def test_every_mcp_tool_named_in_the_guidance_exists_on_the_live_registry(tmp_path):
    with open_database(tmp_path / "graph.sqlite") as db:
        served = {tool["name"] for tool in build_read_registry(db).list()}
    named = set(re.findall(r"`(dkg\.[a-z_.]+)`", rules.guidance_block()))
    assert named, "the guidance names no MCP tool at all"
    assert named <= served, sorted(named - served)


def test_injection_into_a_users_file_leaves_their_text_verbatim(tmp_path):
    path = tmp_path / "CLAUDE.md"
    path.write_text(USER_TEXT, encoding="utf-8")

    rules.install_rules(path)
    text = path.read_text(encoding="utf-8")

    assert text.startswith(USER_TEXT)
    assert rules.BEGIN_MARKER in text
    assert rules.END_MARKER in text
    before, _, rest = text.partition(rules.BEGIN_MARKER)
    assert before == USER_TEXT


def test_text_immediately_adjacent_to_the_markers_survives(tmp_path):
    """The hard case: no blank line between the user's words and our markers."""
    path = tmp_path / "CLAUDE.md"
    rules.install_rules(path)
    seeded = path.read_text(encoding="utf-8")

    adjacent_above = "a line the user typed right above the marker\n"
    adjacent_below = "a line the user typed right below the marker\n"
    edited = adjacent_above + seeded.rstrip("\n") + "\n" + adjacent_below
    path.write_text(edited, encoding="utf-8")

    rules.install_rules(path)
    after = path.read_text(encoding="utf-8")
    assert after == edited, "re-injection changed text outside the markers"

    stripped, found = rules.strip_text(after)
    assert found is True
    assert stripped == adjacent_above + adjacent_below


def test_reinjection_replaces_only_the_block_and_is_byte_identical(tmp_path):
    path = tmp_path / "CLAUDE.md"
    path.write_text(USER_TEXT, encoding="utf-8")
    rules.install_rules(path)
    first = path.read_bytes()

    result = rules.install_rules(path)
    assert result.changed is False
    assert result.written is False
    assert path.read_bytes() == first


def test_a_stale_block_is_replaced_without_touching_the_rest(tmp_path):
    path = tmp_path / "CLAUDE.md"
    stale = (
        USER_TEXT
        + rules.BEGIN_MARKER
        + "\n\nguidance from an older version that no longer applies\n\n"
        + rules.END_MARKER
        + "\n"
        + "trailing note the user added afterwards\n"
    )
    path.write_text(stale, encoding="utf-8")

    result = rules.install_rules(path)
    assert result.changed is True
    text = path.read_text(encoding="utf-8")
    assert "older version" not in text
    assert text.startswith(USER_TEXT)
    assert text.endswith("trailing note the user added afterwards\n")
    assert rules.guidance_block() in text


def test_removal_takes_out_exactly_the_block(tmp_path):
    path = tmp_path / "CLAUDE.md"
    path.write_text(USER_TEXT, encoding="utf-8")

    rules.install_rules(path)
    result = rules.uninstall_rules(path)

    assert result.changed is True
    assert result.removed_file is False
    assert path.read_text(encoding="utf-8") == USER_TEXT


def test_a_file_holding_only_our_block_is_deleted_on_removal(tmp_path):
    path = tmp_path / "CLAUDE.md"
    rules.install_rules(path)
    assert path.exists()

    result = rules.uninstall_rules(path)
    assert result.removed_file is True
    assert not path.exists()


def test_removal_refuses_a_file_this_project_never_wrote_into(tmp_path):
    path = tmp_path / "CLAUDE.md"
    path.write_text(USER_TEXT, encoding="utf-8")

    result = rules.uninstall_rules(path)
    assert result.changed is False
    assert result.written is False
    assert configure.OWNER_MARKER in result.reason or rules.BEGIN_MARKER in result.reason
    assert path.read_text(encoding="utf-8") == USER_TEXT


def test_a_half_open_block_is_refused_rather_than_guessed_at(tmp_path):
    for broken in (
        USER_TEXT + rules.BEGIN_MARKER + "\nsomething\n",
        USER_TEXT + "something\n" + rules.END_MARKER + "\n",
    ):
        path = tmp_path / "CLAUDE.md"
        path.write_text(broken, encoding="utf-8")
        with pytest.raises(ValidationError):
            rules.install_rules(path)
        with pytest.raises(ValidationError):
            rules.uninstall_rules(path)
        assert path.read_text(encoding="utf-8") == broken


def test_two_blocks_are_refused(tmp_path):
    path = tmp_path / "CLAUDE.md"
    doubled = rules.guidance_block() + rules.guidance_block()
    path.write_text(doubled, encoding="utf-8")
    with pytest.raises(ValidationError):
        rules.install_rules(path)
    assert path.read_text(encoding="utf-8") == doubled


def test_dry_run_writes_nothing(tmp_path):
    path = tmp_path / "CLAUDE.md"
    path.write_text(USER_TEXT, encoding="utf-8")

    result = rules.install_rules(path, dry_run=True)
    assert result.changed is True
    assert result.written is False
    assert path.read_text(encoding="utf-8") == USER_TEXT

    rules.install_rules(path)
    before = path.read_bytes()
    removal = rules.uninstall_rules(path, dry_run=True)
    assert removal.changed is True
    assert removal.written is False
    assert path.read_bytes() == before


def test_every_tool_that_declares_a_rules_file_gets_one(tmp_path):
    configure.install_all(
        config_root=tmp_path,
        dkg_home=tmp_path / "h",
        launch=FIXED,
        platform_key="linux",
        only_detected=False,
    )
    with_rules = [p for p in platforms.platforms() if p.rules is not None]
    assert len(with_rules) >= 10
    for target in with_rules:
        path = _rules_path(target.name, tmp_path)
        assert path.exists(), target.name
        assert rules.BEGIN_MARKER in path.read_text(encoding="utf-8"), target.name

    for target in platforms.platforms():
        if target.rules is None:
            assert len(target.rules_note.strip()) >= 20, target.name


def test_uninstall_all_removes_every_injected_block(tmp_path):
    configure.install_all(
        config_root=tmp_path,
        dkg_home=tmp_path / "h",
        launch=FIXED,
        platform_key="linux",
        only_detected=False,
    )
    configure.uninstall_all(config_roots=[tmp_path], platform_key="linux")
    leftovers = [
        str(p.relative_to(tmp_path))
        for p in tmp_path.rglob("*")
        if p.is_file() and rules.BEGIN_MARKER in p.read_text(encoding="utf-8")
    ]
    assert leftovers == []

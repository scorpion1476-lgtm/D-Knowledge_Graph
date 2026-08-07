"""R-08: native hook definitions and command or skill packages, written atomically.

The claims this file pins down:

* a tool that documents hooks gets them in its own file, in its own shape, and
  every unrelated entry in that shared file survives byte for byte,
* a tool that documents a command or skill directory gets one, rendered into
  that tool's own format rather than one format for everybody,
* a tool that documents neither is recorded as such and gets no file at all,
  because a file the tool will never read is not support,
* writes are atomic: a failure part way through leaves the previous file
  exactly as it was and leaves no temporary file behind,
* re-running is byte-identical, and anything without the ownership marker is
  refused rather than overwritten.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dkg.core.errors import ValidationError
from dkg.mcp import artifacts, configure, platforms

FIXED = configure.Launch(
    command="dkg",
    prefix_args=(),
    runner="console-script",
    installed_by="system",
    resolves=True,
    basis="pinned by the test",
)


def _target(name: str):
    return next(p for p in platforms.platforms() if p.name == name)


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


def test_hooks_are_written_in_the_tools_own_shape(tmp_path):
    configure.install_bundle(
        "claude-code", config_root=tmp_path, dkg_home=tmp_path / "h", launch=FIXED, platform_key="linux"
    )
    path = tmp_path / ".claude" / "settings.json"
    assert path.exists()

    document = _read(path)
    entries = document["hooks"]["PostToolUse"]
    assert isinstance(entries, list) and len(entries) == 1
    entry = entries[0]
    assert entry["matcher"] == "Edit|MultiEdit|Write"
    assert entry[configure.MARKER_KEY] == configure.OWNER_MARKER
    inner = entry["hooks"][0]
    assert inner["type"] == "command"
    assert inner["command"] == "dkg update --repo . --quiet"
    assert inner[configure.MARKER_KEY] == configure.OWNER_MARKER


def test_the_hook_command_carries_no_absolute_path(tmp_path):
    """A settings file is frequently committed, so it must stay portable."""
    configure.install_bundle(
        "claude-code", config_root=tmp_path, dkg_home=tmp_path / "h", launch=FIXED, platform_key="linux"
    )
    command = _read(tmp_path / ".claude" / "settings.json")["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
    assert not os.path.isabs(command.split()[0])
    assert str(tmp_path) not in command


def test_unrelated_entries_in_the_hook_file_survive_untouched(tmp_path):
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    original = {
        "hooks": {
            "PostToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "somebody-elses-script"}]}
            ],
            "SessionStart": [{"hooks": [{"type": "command", "command": "their-other-script"}]}],
        },
        "model": "their-model",
        "permissions": {"allow": ["Bash(ls:*)"]},
    }
    path.write_text(json.dumps(original, indent=2), encoding="utf-8")

    configure.install_bundle(
        "claude-code", config_root=tmp_path, dkg_home=tmp_path / "h", launch=FIXED, platform_key="linux"
    )
    document = _read(path)

    assert document["model"] == "their-model"
    assert document["permissions"] == original["permissions"]
    assert document["hooks"]["SessionStart"] == original["hooks"]["SessionStart"]
    assert document["hooks"]["PostToolUse"][0] == original["hooks"]["PostToolUse"][0]
    assert len(document["hooks"]["PostToolUse"]) == 2
    assert document["hooks"]["PostToolUse"][1][configure.MARKER_KEY] == configure.OWNER_MARKER

    # And removing ours puts the file back exactly as it was found, modulo the
    # canonical formatting this project writes in both directions.
    configure.uninstall_bundle("claude-code", config_root=tmp_path, platform_key="linux")
    assert _read(path) == original


def test_hook_install_is_idempotent(tmp_path):
    path = tmp_path / ".claude" / "settings.json"
    for _ in range(2):
        configure.install_bundle(
            "claude-code", config_root=tmp_path, dkg_home=tmp_path / "h", launch=FIXED, platform_key="linux"
        )
    assert len(_read(path)["hooks"]["PostToolUse"]) == 1
    first = path.read_bytes()
    configure.install_bundle(
        "claude-code", config_root=tmp_path, dkg_home=tmp_path / "h", launch=FIXED, platform_key="linux"
    )
    assert path.read_bytes() == first


def test_the_flat_hook_shape_is_written_for_the_tools_that_use_it(tmp_path):
    """Two hook shapes exist because two shapes are documented.

    A single shape written everywhere would be the tell that this is a
    lookalike rather than each tool's own format.
    """
    configure.install_bundle(
        "cursor", config_root=tmp_path, dkg_home=tmp_path / "h", launch=FIXED, platform_key="linux"
    )
    document = _read(tmp_path / ".cursor" / "hooks.json")
    assert document["version"] == 1
    entries = document["hooks"]["afterFileEdit"]
    assert len(entries) == 1
    assert entries[0]["command"] == "dkg update --repo . --quiet"
    assert entries[0][configure.MARKER_KEY] == configure.OWNER_MARKER
    # Flat, not nested under a matcher.
    assert "hooks" not in entries[0]

    configure.install_bundle(
        "windsurf", config_root=tmp_path, dkg_home=tmp_path / "h", launch=FIXED, platform_key="linux"
    )
    windsurf = _read(tmp_path / ".codeium" / "windsurf" / "hooks.json")
    assert "version" not in windsurf
    assert list(windsurf["hooks"]) == ["post_write_code"]


def test_only_the_events_that_mean_a_file_changed_are_hooked(tmp_path):
    """A hook on every tool call would re-ingest after a search."""
    for target in platforms.platforms():
        if target.hooks is None:
            continue
        assert target.hooks.events, target.name
        for event in target.hooks.events:
            assert any(word in event.lower() for word in ("edit", "write", "tooluse")), (
                target.name,
                event,
            )
        if target.hooks.style == "claude-settings":
            assert target.hooks.matcher, target.name


def test_a_tool_with_no_verified_hook_vocabulary_gets_no_hook_file(tmp_path):
    configure.install_bundle(
        "gemini-cli", config_root=tmp_path, dkg_home=tmp_path / "h", launch=FIXED, platform_key="linux"
    )
    reasons = {s["artifact"]: s["reason"] for s in
               configure.install_bundle("gemini-cli", config_root=tmp_path, dkg_home=tmp_path / "h",
                                        launch=FIXED, platform_key="linux")["skipped"]}
    assert "hooks" in reasons
    assert "verified" in reasons["hooks"]
    assert not (tmp_path / ".gemini" / "hooks.json").exists()


def test_hook_uninstall_removes_nothing_it_did_not_write(tmp_path):
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    raw = json.dumps({"hooks": {"PostToolUse": [{"matcher": "Bash", "hooks": []}]}}, indent=2)
    path.write_text(raw, encoding="utf-8")

    result = artifacts.uninstall_hooks(_target("claude-code"), config_root=tmp_path, platform_key="linux")
    assert result["removed"] == 0
    assert result["written"] is False
    assert path.read_text(encoding="utf-8") == raw


def test_hook_file_with_a_wrong_typed_hooks_key_is_refused(tmp_path):
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    raw = json.dumps({"hooks": ["not", "an", "object"]})
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(ValidationError):
        artifacts.install_hooks(
            _target("claude-code"), config_root=tmp_path, platform_key="linux", launch=FIXED
        )
    assert path.read_text(encoding="utf-8") == raw


# ---------------------------------------------------------------------------
# Command and skill packages
# ---------------------------------------------------------------------------


def test_command_packages_are_written_in_each_tools_own_format(tmp_path):
    configure.install_all(
        config_root=tmp_path,
        dkg_home=tmp_path / "h",
        launch=FIXED,
        platform_key="linux",
        only_detected=False,
    )
    names = [c.name for c in artifacts.workflow_commands()]
    assert len(names) == 3

    markdown = tmp_path / ".claude" / "commands" / f"{names[0]}.md"
    assert markdown.exists()
    text = markdown.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "description: " in text
    assert artifacts.TEXT_MARKER in text

    toml = tmp_path / ".gemini" / "commands" / f"{names[0]}.toml"
    assert toml.exists()
    toml_text = toml.read_text(encoding="utf-8")
    assert toml_text.startswith(f"# {artifacts.TEXT_MARKER}\n")
    assert toml_text.count("description = ") == 1
    assert 'prompt = """' in toml_text
    assert toml_text.rstrip().endswith('"""')

    # The plain-markdown format carries no frontmatter, because the tool that
    # uses it reads the whole file as the prompt.
    plain = tmp_path / ".aws" / "amazonq" / "prompts" / f"{names[0]}.md"
    assert plain.exists()
    plain_text = plain.read_text(encoding="utf-8")
    assert not plain_text.startswith("---\n")
    assert plain_text.startswith(f"<!-- {artifacts.TEXT_MARKER} -->\n")

    # A tool that documents a skill directory but no command directory gets the
    # skill and no invented command directory.
    assert (tmp_path / ".cursor" / "skills" / "d-knowledge-graph" / "SKILL.md").exists()
    assert not (tmp_path / ".cursor" / "commands").exists()


def test_the_skill_package_is_written_where_the_tool_documents_it(tmp_path):
    configure.install_bundle(
        "claude-code", config_root=tmp_path, dkg_home=tmp_path / "h", launch=FIXED, platform_key="linux"
    )
    skill = tmp_path / ".claude" / "skills" / "d-knowledge-graph" / "SKILL.md"
    assert skill.exists()
    text = skill.read_text(encoding="utf-8")
    assert text.startswith("---\nname: d-knowledge-graph\n")
    assert artifacts.TEXT_MARKER in text
    assert "# dkg-usage" in text


def test_a_tool_with_no_command_support_gets_no_command_file(tmp_path):
    result = configure.install_bundle(
        "claude-desktop", config_root=tmp_path, dkg_home=tmp_path / "h", launch=FIXED, platform_key="linux"
    )
    assert result["commands"] is None
    assert result["hooks"] is None
    assert result["rules"] is None
    skipped = {s["artifact"]: s["reason"] for s in result["skipped"]}
    assert set(skipped) == {"commands", "hooks", "rules"}
    for reason in skipped.values():
        assert reason.strip()

    produced = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file())
    assert produced == [".config/Claude/claude_desktop_config.json"]


def test_command_package_is_idempotent_and_deterministic(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    for root in (a, b):
        configure.install_bundle(
            "claude-code", config_root=root, dkg_home=tmp_path / "h", launch=FIXED, platform_key="linux"
        )
    for name in (c.name for c in artifacts.workflow_commands()):
        first = (a / ".claude" / "commands" / f"{name}.md").read_bytes()
        second = (b / ".claude" / "commands" / f"{name}.md").read_bytes()
        assert first == second

    result = configure.install_bundle(
        "claude-code", config_root=a, dkg_home=tmp_path / "h", launch=FIXED, platform_key="linux"
    )
    assert result["commands"]["written"] == []
    assert len(result["commands"]["unchanged"]) == 4


def test_a_command_file_this_project_did_not_write_is_refused(tmp_path):
    name = artifacts.workflow_commands()[0].name
    path = tmp_path / ".claude" / "commands" / f"{name}.md"
    path.parent.mkdir(parents=True)
    raw = "my own command, please do not touch\n"
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(ValidationError) as excinfo:
        configure.install_bundle(
            "claude-code", config_root=tmp_path, dkg_home=tmp_path / "h", launch=FIXED, platform_key="linux"
        )
    assert name in str(excinfo.value)
    assert path.read_text(encoding="utf-8") == raw

    with pytest.raises(ValidationError):
        artifacts.uninstall_commands(_target("claude-code"), config_root=tmp_path, platform_key="linux")
    assert path.read_text(encoding="utf-8") == raw


def test_command_dry_run_writes_nothing(tmp_path):
    result = artifacts.install_commands(
        _target("claude-code"), config_root=tmp_path, platform_key="linux", dry_run=True
    )
    assert len(result["written"]) == 4
    assert list(tmp_path.rglob("*")) == []


# ---------------------------------------------------------------------------
# Atomicity
# ---------------------------------------------------------------------------


def test_a_failed_write_leaves_the_previous_file_and_no_temporary_file(tmp_path, monkeypatch):
    """os.replace is the last step, so a failure there must change nothing."""
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    raw = json.dumps({"model": "theirs"}, indent=2)
    path.write_text(raw, encoding="utf-8")

    def boom(src, dst, **kwargs):
        raise OSError("simulated failure at the moment of replacement")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        artifacts.install_hooks(
            _target("claude-code"), config_root=tmp_path, platform_key="linux", launch=FIXED
        )

    assert path.read_text(encoding="utf-8") == raw
    assert sorted(p.name for p in path.parent.iterdir()) == ["settings.json"]


def test_the_writer_never_truncates_in_place(tmp_path, monkeypatch):
    """The target path is only ever reached through os.replace.

    Opening the destination for writing directly is the bug this guards
    against: it would leave a truncated config behind if the process died.
    """
    opened: list[str] = []
    real_open = open

    def spy_open(file, mode="r", *args, **kwargs):
        if "w" in str(mode) or "a" in str(mode) or "+" in str(mode):
            opened.append(os.fspath(file))
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", spy_open)
    configure.install_bundle(
        "claude-code", config_root=tmp_path, dkg_home=tmp_path / "h", launch=FIXED, platform_key="linux"
    )
    for name in opened:
        assert name.endswith(".tmp"), name

"""R-11: a symmetric uninstall with a dry run and explicit scopes.

Symmetric is the load-bearing word, so the headline test is the strict one:
install everything for every supported tool, uninstall, and compare the tree
byte for byte against a snapshot taken before the install. Every file, every
directory, every byte.

The scopes, each with its own test: every registered repository, keeping the
graph data (and the explicit opposite), and unbinding one tool only. Plus the
refusals, because a removal that is willing to delete something this project
did not write is not symmetric, it is destructive.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dkg.cli.entry import main
from dkg.core.errors import ValidationError
from dkg.mcp import artifacts, configure, platforms
from dkg.watch.registry import Registry

FIXED = configure.Launch(
    command="dkg",
    prefix_args=(),
    runner="console-script",
    installed_by="system",
    resolves=True,
    basis="pinned by the test",
)

PLATFORM = "linux"


def _snapshot(root: Path) -> dict[str, bytes | None]:
    """Every path under ``root``, with file bytes and ``None`` for directories."""
    out: dict[str, bytes | None] = {}
    for path in sorted(root.rglob("*")):
        key = str(path.relative_to(root))
        out[key] = path.read_bytes() if path.is_file() else None
    return out


def _seed_detected_tools(root: Path) -> None:
    """Make every supported tool look installed, without an empty directory.

    The sentinel file matters: an uninstall prunes directories it emptied, so a
    marker directory that held nothing would legitimately disappear and the
    comparison would be measuring the fixture rather than the code.
    """
    for target in platforms.platforms():
        for marker in target.detect:
            path = root / platforms.resolve_relative(marker, PLATFORM)
            if path.suffix == ".json":
                continue
            path.mkdir(parents=True, exist_ok=True)
            (path / "pre-existing.txt").write_text("the user's own file\n", encoding="utf-8")


def test_install_then_uninstall_restores_the_tree_byte_for_byte(tmp_path):
    root = tmp_path / "config-root"
    root.mkdir()
    _seed_detected_tools(root)
    before = _snapshot(root)
    assert before, "the fixture produced an empty tree, so the comparison would be vacuous"

    result = configure.install_all(
        config_root=root, dkg_home=tmp_path / "h", launch=FIXED, platform_key=PLATFORM
    )
    assert result["selected"] == list(platforms.platform_names())
    during = _snapshot(root)
    assert len(during) > len(before), "install wrote nothing, so the round trip proves nothing"

    configure.uninstall_all(config_roots=[root], platform_key=PLATFORM)
    after = _snapshot(root)
    assert after == before


def test_the_round_trip_holds_when_the_user_already_had_content(tmp_path):
    """The harder case: shared files that must come back exactly as found."""
    root = tmp_path / "config-root"
    root.mkdir()
    _seed_detected_tools(root)

    claude_md = root / ".claude" / "CLAUDE.md"
    claude_md.write_text("# my rules\n\nbe careful\n", encoding="utf-8")
    settings = root / ".claude" / "settings.json"
    settings.write_text(
        configure.serialise({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": []}]}, "model": "m"}),
        encoding="utf-8",
    )
    cursor = root / ".cursor" / "mcp.json"
    cursor.write_text(configure.serialise({"mcpServers": {"theirs": {"command": "node"}}}), encoding="utf-8")

    before = _snapshot(root)
    configure.install_all(config_root=root, dkg_home=tmp_path / "h", launch=FIXED, platform_key=PLATFORM)
    configure.uninstall_all(config_roots=[root], platform_key=PLATFORM)
    assert _snapshot(root) == before


def test_uninstall_dry_run_changes_nothing(tmp_path):
    root = tmp_path / "config-root"
    root.mkdir()
    _seed_detected_tools(root)
    configure.install_all(config_root=root, dkg_home=tmp_path / "h", launch=FIXED, platform_key=PLATFORM)
    before = _snapshot(root)

    result = configure.uninstall_all(config_roots=[root], platform_key=PLATFORM, dry_run=True)
    assert result["dry_run"] is True
    assert _snapshot(root) == before

    # And the dry run reported real work rather than nothing.
    removals = [
        bundle
        for entry in result["results"]
        for bundle in entry["results"]
        if bundle["server"]["removed"]
    ]
    assert len(removals) == len(platforms.platform_names())


def test_scope_unbinding_one_tool_only(tmp_path):
    root = tmp_path / "config-root"
    root.mkdir()
    _seed_detected_tools(root)
    configure.install_all(config_root=root, dkg_home=tmp_path / "h", launch=FIXED, platform_key=PLATFORM)
    before = _snapshot(root)

    configure.uninstall_bundle("cursor", config_root=root, platform_key=PLATFORM)
    after = _snapshot(root)

    gone = sorted(set(before) - set(after))
    assert gone, "unbinding one tool removed nothing"
    assert all(path.startswith(".cursor") for path in gone), gone
    unchanged = {k: v for k, v in before.items() if k in after}
    assert all(after[k] == v for k, v in unchanged.items())


def test_scope_every_registered_repository(tmp_path, capsys):
    """Driven through the CLI, because the registry is resolved in that layer."""
    home = tmp_path / "dkg-home"
    home.mkdir()
    user_root = tmp_path / "user"
    user_root.mkdir()
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    for repo in (repo_a, repo_b):
        repo.mkdir()
        (repo / ".cursor").mkdir()
    Registry.in_home(home).add("a", repo_a)
    Registry.in_home(home).add("b", repo_b)

    for root in (user_root, repo_a, repo_b):
        configure.install_bundle(
            "cursor", config_root=root, dkg_home=home, launch=FIXED, platform_key=PLATFORM
        )
        assert (root / ".cursor" / "mcp.json").exists()

    rc = main(
        [
            "--home",
            str(home),
            "--json",
            "mcp-uninstall",
            "cursor",
            "--all-repos",
            "--config-root",
            str(user_root),
            "--target-os",
            PLATFORM,
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["scope"]["all_repos"] is True
    assert payload["scope"]["registered_repos"] == sorted([str(repo_a.resolve()), str(repo_b.resolve())])
    assert payload["data"]["kept"] is True

    for root in (user_root, repo_a, repo_b):
        assert not (root / ".cursor" / "mcp.json").exists(), root


def test_scope_keeping_the_graph_data_is_the_default(tmp_path, capsys):
    home = tmp_path / "dkg-home"
    home.mkdir()
    (home / "graph.sqlite").write_bytes(b"not really a database, but it is the marker\n")
    root = tmp_path / "user"
    (root / ".cursor").mkdir(parents=True)
    configure.install_bundle("cursor", config_root=root, dkg_home=home, launch=FIXED, platform_key=PLATFORM)

    rc = main(
        ["--home", str(home), "--json", "mcp-uninstall", "cursor", "--config-root", str(root), "--target-os", PLATFORM]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["scope"]["keep_data"] is True
    assert payload["data"]["kept"] is True
    assert (home / "graph.sqlite").exists()


def test_scope_purging_the_graph_data_is_explicit_and_gated(tmp_path, capsys):
    home = tmp_path / "dkg-home"
    home.mkdir()
    (home / "graph.sqlite").write_bytes(b"marker\n")
    root = tmp_path / "user"
    (root / ".cursor").mkdir(parents=True)
    configure.install_bundle("cursor", config_root=root, dkg_home=home, launch=FIXED, platform_key=PLATFORM)

    rc = main(
        [
            "--home",
            str(home),
            "--json",
            "mcp-uninstall",
            "cursor",
            "--config-root",
            str(root),
            "--purge-data",
            "--target-os",
            PLATFORM,
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["scope"]["keep_data"] is False
    assert payload["data"]["removed"] is True
    assert not home.exists()


def test_purge_refuses_a_directory_that_is_not_a_graph_home(tmp_path):
    not_a_home = tmp_path / "important-documents"
    not_a_home.mkdir()
    (not_a_home / "thesis.txt").write_text("years of work\n", encoding="utf-8")

    with pytest.raises(ValidationError) as excinfo:
        configure.purge_data(dkg_home=not_a_home)
    assert "graph.sqlite" in str(excinfo.value)
    assert (not_a_home / "thesis.txt").exists()


def test_purge_dry_run_deletes_nothing(tmp_path):
    home = tmp_path / "dkg-home"
    home.mkdir()
    (home / "graph.sqlite").write_bytes(b"marker\n")
    result = configure.purge_data(dkg_home=home, dry_run=True)
    assert result["removed"] is False
    assert home.exists()


def test_uninstall_refuses_every_artifact_it_did_not_write(tmp_path):
    """One refusal per artifact kind, each leaving the bytes alone."""
    root = tmp_path / "config-root"
    claude = root / ".claude"
    claude.mkdir(parents=True)

    # A server entry under our name that we did not write.
    server = root / ".claude.json"
    server_raw = json.dumps({"mcpServers": {configure.SERVER_NAME: {"command": "theirs"}}}, indent=4)
    server.write_text(server_raw, encoding="utf-8")
    with pytest.raises(ValidationError):
        configure.uninstall("claude-code", config_root=root, platform_key=PLATFORM)
    assert server.read_text(encoding="utf-8") == server_raw
    server.unlink()

    # A command file with one of our names that we did not write.
    name = artifacts.workflow_commands()[0].name
    command = claude / "commands" / f"{name}.md"
    command.parent.mkdir()
    command_raw = "their own command\n"
    command.write_text(command_raw, encoding="utf-8")
    with pytest.raises(ValidationError):
        configure.uninstall_bundle("claude-code", config_root=root, platform_key=PLATFORM)
    assert command.read_text(encoding="utf-8") == command_raw
    command.unlink()

    # A rules file with no block of ours: reported, never edited.
    claude_md = claude / "CLAUDE.md"
    rules_raw = "# their rules\n"
    claude_md.write_text(rules_raw, encoding="utf-8")
    result = configure.uninstall_bundle("claude-code", config_root=root, platform_key=PLATFORM)
    assert result["rules"]["changed"] is False
    assert claude_md.read_text(encoding="utf-8") == rules_raw

    # A hook entry we did not write.
    settings = claude / "settings.json"
    settings_raw = configure.serialise({"hooks": {"PostToolUse": [{"matcher": "Bash", "hooks": []}]}})
    settings.write_text(settings_raw, encoding="utf-8")
    result = configure.uninstall_bundle("claude-code", config_root=root, platform_key=PLATFORM)
    assert result["hooks"]["removed"] == 0
    assert settings.read_text(encoding="utf-8") == settings_raw


def test_uninstall_is_safe_to_run_twice(tmp_path):
    root = tmp_path / "config-root"
    root.mkdir()
    _seed_detected_tools(root)
    configure.install_all(config_root=root, dkg_home=tmp_path / "h", launch=FIXED, platform_key=PLATFORM)
    configure.uninstall_all(config_roots=[root], platform_key=PLATFORM)
    before = _snapshot(root)
    configure.uninstall_all(config_roots=[root], platform_key=PLATFORM)
    assert _snapshot(root) == before


def test_whatever_install_writes_uninstall_knows_how_to_remove(tmp_path):
    """Symmetry stated as a property rather than as a file list.

    Install reports the paths it planned; uninstall reports the paths it
    removed. Every artifact kind that install wrote for a tool must appear in
    that tool's uninstall result, so a future artifact kind added to install
    alone would fail here rather than quietly leak.
    """
    root = tmp_path / "config-root"
    root.mkdir()
    _seed_detected_tools(root)

    for target in platforms.platforms():
        installed = configure.install_bundle(
            target.name, config_root=root, dkg_home=tmp_path / "h", launch=FIXED, platform_key=PLATFORM
        )
        removed = configure.uninstall_bundle(target.name, config_root=root, platform_key=PLATFORM)
        for kind in ("hooks", "commands", "rules"):
            assert (installed[kind] is None) == (removed[kind] is None), (target.name, kind)
        assert installed["server"]["written"] is True
        assert removed["server"]["removed"] is True
        if installed["commands"] is not None:
            assert sorted(removed["commands"]["removed"]) == sorted(installed["commands"]["planned"])
        if installed["rules"] is not None:
            assert removed["rules"]["changed"] is True
            assert not Path(installed["rules"]["path"]).exists()
        if installed["hooks"] is not None:
            assert removed["hooks"]["removed"] >= 1


def test_nothing_outside_the_named_roots_is_touched(tmp_path):
    root = tmp_path / "config-root"
    root.mkdir()
    _seed_detected_tools(root)
    bystander = tmp_path / "bystander"
    bystander.mkdir()
    (bystander / ".cursor").mkdir()
    (bystander / ".cursor" / "mcp.json").write_text(
        configure.serialise({"mcpServers": {configure.SERVER_NAME: {"command": "x"}}}), encoding="utf-8"
    )
    keep = (bystander / ".cursor" / "mcp.json").read_bytes()

    configure.install_all(config_root=root, dkg_home=tmp_path / "h", launch=FIXED, platform_key=PLATFORM)
    configure.uninstall_all(config_roots=[root], platform_key=PLATFORM)

    assert (bystander / ".cursor" / "mcp.json").read_bytes() == keep


def test_marker_directories_survive_so_detection_still_works(tmp_path):
    """Pruning must not make a tool look uninstalled to the next detect run."""
    root = tmp_path / "config-root"
    root.mkdir()
    (root / ".cursor").mkdir()
    (root / ".cursor" / "keep.txt").write_text("mine\n", encoding="utf-8")

    configure.install_bundle(
        "cursor", config_root=root, dkg_home=tmp_path / "h", launch=FIXED, platform_key=PLATFORM
    )
    configure.uninstall_bundle("cursor", config_root=root, platform_key=PLATFORM)

    assert (root / ".cursor").is_dir()
    assert configure.detected_tool_names(config_root=root, platform_key=PLATFORM) == ["cursor"]
    assert not os.path.exists(root / ".cursor" / "commands")

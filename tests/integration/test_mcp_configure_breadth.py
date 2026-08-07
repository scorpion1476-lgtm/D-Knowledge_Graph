"""R-07: breadth, auto-detection, one command, per-tool targeting, dry run, marker.

The claims this file pins down:

* the table reaches at least fifteen distinct tools and every record says what
  its configuration path is based on, so a reviewer can check the claim rather
  than take it,
* the paths are sane in the ways that matter for a path joined onto a
  caller-supplied root, on every operating system the table distinguishes,
* detection reports what is actually on disk and creates nothing by looking,
* one command configures every detected tool and skips the rest, while naming
  one tool configures exactly that one,
* a dry run writes nothing at all,
* every written entry carries the ownership marker, and an entry this project
  did not write is refused rather than replaced.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dkg.core.errors import ValidationError
from dkg.mcp import configure, platforms

FIXED = configure.Launch(
    command="dkg",
    prefix_args=(),
    runner="console-script",
    installed_by="system",
    resolves=True,
    basis="pinned by the test",
)

#: The entry styles the writer knows. A record naming anything else would
#: produce an entry shape no tool documents.
KNOWN_STYLES = {
    "mcp-servers",
    "vscode-servers",
    "zed-context-servers",
    "opencode-mcp",
    "copilot-local",
    "cline-transport",
}


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _entry_of(record: dict, document: dict) -> dict:
    return configure.server_map(document, record["servers_key"])[configure.SERVER_NAME]


def test_at_least_fifteen_distinct_tools_are_supported():
    names = list(platforms.platform_names())
    assert len(names) >= 15, names
    assert len(set(names)) == len(names)
    assert names == sorted(names)


def test_every_record_says_what_its_path_is_based_on():
    """Honest labelling, applied to the table itself.

    A configuration path with no stated basis is a guess wearing a record's
    clothing, and a guess creates a directory the user never asked for.
    """
    for target in platforms.platforms():
        assert len(target.note.strip()) >= 40, (target.name, target.note)
        assert target.display.strip()
        assert target.entry_style in KNOWN_STYLES, (target.name, target.entry_style)
        assert target.detect, target.name
        # An unsupported artifact kind must say why, not stay silent.
        for spec, note, label in (
            (target.hooks, target.hooks_note, "hooks"),
            (target.commands, target.commands_note, "commands"),
            (target.rules, target.rules_note, "rules"),
        ):
            if spec is None:
                assert len(note.strip()) >= 20, (target.name, label, note)


@pytest.mark.parametrize("platform_key", platforms.PLATFORM_KEYS)
def test_paths_are_relative_and_confined_on_every_platform(platform_key):
    for record in configure.supported_tools(platform_key=platform_key):
        for label in ("relative_path", "hooks_path", "commands_path", "rules_path"):
            value = record[label]
            if not value:
                continue
            assert not os.path.isabs(value), (record["name"], label, value)
            assert not value.startswith("~"), (record["name"], label, value)
            parts = Path(value).parts
            assert ".." not in parts, (record["name"], label, value)


def test_platform_specific_paths_actually_differ_between_operating_systems():
    """The per-operating-system table is real, not decoration."""
    by_os = {
        key: {r["name"]: r["relative_path"] for r in configure.supported_tools(platform_key=key)}
        for key in platforms.PLATFORM_KEYS
    }
    differing = {
        name
        for name in by_os["linux"]
        if len({by_os[key][name] for key in platforms.PLATFORM_KEYS}) > 1
    }
    assert differing, "no tool has a platform-specific configuration path"
    assert "AppData" in by_os["win32"][sorted(differing)[0]] or "Library" in by_os["darwin"][sorted(differing)[0]]


def test_detection_finds_nothing_in_an_empty_root_and_creates_nothing(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    records = configure.detect_installed(config_root=root, platform_key="linux")
    assert len(records) == len(platforms.platform_names())
    assert [r["name"] for r in records if r["present"]] == []
    assert list(root.iterdir()) == []

    # A root that does not exist at all is answered without creating it.
    missing = tmp_path / "not-here"
    assert configure.detected_tool_names(config_root=missing, platform_key="linux") == []
    assert not missing.exists()


def test_detection_reports_only_the_tools_actually_present(tmp_path):
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".gemini").mkdir()

    records = configure.detect_installed(config_root=tmp_path, platform_key="linux")
    present = sorted(r["name"] for r in records if r["present"])
    assert present == ["cursor", "gemini-cli"]

    by_name = {r["name"]: r for r in records}
    assert by_name["cursor"]["detected_via"] == ".cursor"
    assert by_name["windsurf"]["detected_via"] == ""
    assert by_name["cursor"]["detect_markers"] == sorted(by_name["cursor"]["detect_markers"])


def test_detection_recognises_a_config_file_as_well_as_a_directory(tmp_path):
    (tmp_path / ".claude.json").write_text("{}\n", encoding="utf-8")
    assert configure.detected_tool_names(config_root=tmp_path, platform_key="linux") == ["claude-code"]


def test_one_command_configures_every_detected_tool_and_skips_the_rest(tmp_path):
    for marker in (".cursor", ".gemini", ".kiro"):
        (tmp_path / marker).mkdir()

    result = configure.install_all(
        config_root=tmp_path, dkg_home=tmp_path / "h", launch=FIXED, platform_key="linux"
    )
    assert result["selected"] == ["cursor", "gemini-cli", "kiro"]
    assert "windsurf" in result["skipped_not_present"]

    assert (tmp_path / ".cursor" / "mcp.json").exists()
    assert (tmp_path / ".gemini" / "settings.json").exists()
    assert (tmp_path / ".kiro" / "settings" / "mcp.json").exists()
    assert not (tmp_path / ".codeium").exists()
    assert not (tmp_path / ".junie").exists()


def test_install_all_can_be_told_to_ignore_detection(tmp_path):
    result = configure.install_all(
        config_root=tmp_path,
        dkg_home=tmp_path / "h",
        launch=FIXED,
        platform_key="linux",
        only_detected=False,
    )
    assert result["selected"] == list(platforms.platform_names())
    for record in configure.supported_tools(platform_key="linux"):
        assert (tmp_path / record["relative_path"]).exists(), record["name"]


def test_install_all_dry_run_writes_absolutely_nothing(tmp_path):
    for marker in (".cursor", ".gemini"):
        (tmp_path / marker).mkdir()
    before = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))

    result = configure.install_all(
        config_root=tmp_path,
        dkg_home=tmp_path / "h",
        launch=FIXED,
        platform_key="linux",
        only_detected=False,
        dry_run=True,
    )
    assert result["dry_run"] is True
    assert result["selected"] == list(platforms.platform_names())
    after = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
    assert after == before


def test_per_tool_targeting_touches_exactly_one_tool(tmp_path):
    for marker in (".cursor", ".gemini"):
        (tmp_path / marker).mkdir()

    configure.install_bundle(
        "cursor", config_root=tmp_path, dkg_home=tmp_path / "h", launch=FIXED, platform_key="linux"
    )
    assert (tmp_path / ".cursor" / "mcp.json").exists()
    assert list((tmp_path / ".gemini").iterdir()) == []


def test_every_written_entry_carries_the_ownership_marker(tmp_path):
    configure.install_all(
        config_root=tmp_path,
        dkg_home=tmp_path / "h",
        launch=FIXED,
        platform_key="linux",
        only_detected=False,
    )
    for record in configure.supported_tools(platform_key="linux"):
        document = _read(tmp_path / record["relative_path"])
        entry = _entry_of(record, document)
        assert entry[configure.MARKER_KEY] == configure.OWNER_MARKER, record["name"]
        # Whatever the shape, the launch reaches the read-only stdio server.
        flat = json.dumps(entry)
        assert "mcp-stdio" in flat, record["name"]
        assert "dkg" in flat, record["name"]


def test_entry_shape_matches_the_style_the_record_declares(tmp_path):
    configure.install_all(
        config_root=tmp_path,
        dkg_home=tmp_path / "h",
        launch=FIXED,
        platform_key="linux",
        only_detected=False,
    )
    for target in platforms.platforms():
        record = target.as_dict("linux")
        entry = _entry_of(record, _read(tmp_path / record["relative_path"]))
        if target.entry_style == "mcp-servers":
            assert set(entry) == {configure.MARKER_KEY, "args", "command", "description"}
        elif target.entry_style == "vscode-servers":
            assert entry["type"] == "stdio"
            assert entry["command"] == "dkg"
        elif target.entry_style == "copilot-local":
            assert entry["type"] == "local"
            assert entry["command"] == "dkg"
        elif target.entry_style == "cline-transport":
            assert entry["disabled"] is False
            assert entry["transport"]["type"] == "stdio"
            assert entry["transport"]["command"] == "dkg"
        elif target.entry_style == "zed-context-servers":
            assert set(entry) == {configure.MARKER_KEY, "args", "command"}
        elif target.entry_style == "opencode-mcp":
            assert entry["type"] == "local"
            assert entry["enabled"] is True
            assert isinstance(entry["command"], list)
            assert entry["command"][0] == "dkg"


def test_an_entry_another_installer_wrote_is_refused_not_replaced(tmp_path):
    path = tmp_path / ".cursor" / "mcp.json"
    path.parent.mkdir(parents=True)
    foreign = {
        "mcpServers": {
            configure.SERVER_NAME: {"command": "somebody-elses-binary", "args": ["--their-flag"]},
            "unrelated": {"command": "node"},
        }
    }
    raw = json.dumps(foreign, indent=4)
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(ValidationError) as excinfo:
        configure.install("cursor", config_root=tmp_path, dkg_home=tmp_path / "h", launch=FIXED)
    assert configure.MARKER_KEY in str(excinfo.value)
    assert path.read_text(encoding="utf-8") == raw

    # install_all refuses for the same reason rather than quietly skipping.
    with pytest.raises(ValidationError):
        configure.install_all(config_root=tmp_path, dkg_home=tmp_path / "h", launch=FIXED)
    assert path.read_text(encoding="utf-8") == raw

    # And the override is explicit.
    configure.install("cursor", config_root=tmp_path, dkg_home=tmp_path / "h", launch=FIXED, force=True)
    document = _read(path)
    assert document["mcpServers"][configure.SERVER_NAME][configure.MARKER_KEY] == configure.OWNER_MARKER
    assert document["mcpServers"]["unrelated"] == {"command": "node"}


def test_installing_one_tool_leaves_every_other_tool_untouched(tmp_path):
    configure.install_all(
        config_root=tmp_path,
        dkg_home=tmp_path / "h",
        launch=FIXED,
        platform_key="linux",
        only_detected=False,
    )
    snapshot = {
        p: p.read_bytes() for p in sorted(tmp_path.rglob("*")) if p.is_file()
    }

    configure.install_bundle(
        "kiro", config_root=tmp_path, dkg_home=tmp_path / "other-home", launch=FIXED, platform_key="linux"
    )
    changed = sorted(str(p.relative_to(tmp_path)) for p, before in snapshot.items() if p.read_bytes() != before)
    assert changed == [".kiro/settings/mcp.json"]

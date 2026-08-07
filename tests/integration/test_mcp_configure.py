"""Integration tests for the MCP client-configuration helper.

Every test drives the helper with ``config_root=tmp_path``. Nothing here (and
nothing in the modules under test) may reach a real user configuration
directory, so one test scans the module sources for home-directory lookups,
another spies on the write primitives to prove every path written stays inside
the fixture directory, and a third monkeypatches ``Path.home`` to a decoy and
proves the decoy stays empty.

Launch detection is pinned with an explicit :class:`configure.Launch` wherever
a test asserts on the written command. Letting detection run would make the
expected bytes depend on whether the machine running the suite happens to have
an isolated runner installed, which is exactly the kind of environment
dependence the rest of this project refuses. Detection itself is tested in
``test_runner_detection.py`` against described machines.
"""

import ast
import json
import os
import tempfile
from pathlib import Path

import pytest

from dkg.core.errors import ValidationError
from dkg.mcp import artifacts, configure, platforms, rules

#: A pinned launch so the written bytes do not depend on this machine.
FIXED = configure.Launch(
    command="dkg",
    prefix_args=(),
    runner="console-script",
    installed_by="system",
    resolves=True,
    basis="pinned by the test",
)


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _claude_path(root: Path) -> Path:
    return root / ".claude.json"


def test_supported_tools_records_are_well_formed():
    tools = configure.supported_tools(platform_key="linux")
    names = [t["name"] for t in tools]
    assert "claude-code" in names
    assert len(names) >= 15
    assert names == sorted(names)
    assert len(set(names)) == len(names)
    for record in tools:
        assert record["name"] and record["display"]
        rel = record["relative_path"]
        assert not os.path.isabs(rel)
        assert ".." not in Path(rel).parts


def test_plan_install_writes_nothing(tmp_path):
    root = tmp_path / "nothing-here"
    plan = configure.plan_install(
        "claude-code", config_root=root, dkg_home=tmp_path / "home", launch=FIXED
    )

    assert not root.exists()
    assert not _claude_path(root).exists()
    assert list(tmp_path.iterdir()) == []

    assert plan["path"] == str(_claude_path(root))
    assert plan["file_exists"] is False
    assert plan["replaces_existing"] is False
    assert plan["changed"] is True
    assert plan["dry_run"] is True
    assert plan["written"] is False
    assert plan["entry"]["command"] == "dkg"


def test_install_dry_run_matches_plan_and_writes_nothing(tmp_path):
    root = tmp_path / "root"
    plan = configure.plan_install("cursor", config_root=root, dkg_home=tmp_path / "home", launch=FIXED)
    dry = configure.install(
        "cursor", config_root=root, dkg_home=tmp_path / "home", launch=FIXED, dry_run=True
    )

    assert dry == plan
    assert not root.exists()


def test_install_creates_file_with_readonly_stdio_entry(tmp_path):
    dkg_home = tmp_path / "dkg-home"
    result = configure.install("claude-code", config_root=tmp_path, dkg_home=dkg_home, launch=FIXED)

    path = _claude_path(tmp_path)
    assert result["written"] is True
    assert result["path"] == str(path)
    assert path.exists()

    doc = _read(path)
    assert set(doc) == {"mcpServers"}
    entry = doc["mcpServers"][configure.SERVER_NAME]

    # argv form, never a shell string.
    assert entry["command"] == "dkg"
    assert isinstance(entry["args"], list)
    assert all(isinstance(a, str) for a in entry["args"])
    assert entry["args"] == ["--home", os.path.abspath(dkg_home), "mcp-stdio"]
    # The subcommand that launches the read-only stdio server.
    assert "mcp-stdio" in entry["args"]
    assert "read-only" in entry["description"]
    assert entry[configure.MARKER_KEY] == configure.OWNER_MARKER

    # Deterministic formatting with a trailing newline.
    raw = path.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    assert raw == json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def test_install_honours_command_override(tmp_path):
    result = configure.install(
        "windsurf", config_root=tmp_path, dkg_home=tmp_path / "h", command="/opt/venv/bin/dkg"
    )
    path = tmp_path / ".codeium" / "windsurf" / "mcp_config.json"
    assert path.exists()
    assert result["written"] is True
    assert result["launch"]["runner"] == "explicit"
    entry = _read(path)["mcpServers"][configure.SERVER_NAME]
    assert entry["command"] == "/opt/venv/bin/dkg"


def test_install_preserves_other_servers_and_top_level_keys(tmp_path):
    path = _claude_path(tmp_path)
    original = {
        "mcpServers": {
            "other-server": {"command": "node", "args": ["server.js"], "env": {"K": "v"}},
            "third-party": {"command": "uvx", "args": ["some-tool"]},
        },
        "numberOfStartups": 7,
        "theme": "dark",
        "projects": {"/some/path": {"allowedTools": ["Bash"], "history": [{"display": "hi"}]}},
    }
    path.write_text(json.dumps(original), encoding="utf-8")

    plan = configure.plan_install("claude-code", config_root=tmp_path, dkg_home=tmp_path / "h", launch=FIXED)
    assert plan["file_exists"] is True
    assert plan["preserved_servers"] == ["other-server", "third-party"]

    configure.install("claude-code", config_root=tmp_path, dkg_home=tmp_path / "h", launch=FIXED)
    doc = _read(path)

    assert doc["numberOfStartups"] == original["numberOfStartups"]
    assert doc["theme"] == original["theme"]
    assert doc["projects"] == original["projects"]
    assert doc["mcpServers"]["other-server"] == original["mcpServers"]["other-server"]
    assert doc["mcpServers"]["third-party"] == original["mcpServers"]["third-party"]
    assert set(doc["mcpServers"]) == {"other-server", "third-party", configure.SERVER_NAME}
    # Nothing beyond our own server key was introduced.
    assert set(doc) == set(original)


def test_install_into_config_without_servers_key(tmp_path):
    path = _claude_path(tmp_path)
    path.write_text(json.dumps({"theme": "light"}), encoding="utf-8")

    configure.install("claude-code", config_root=tmp_path, dkg_home=tmp_path / "h", launch=FIXED)
    doc = _read(path)
    assert doc["theme"] == "light"
    assert configure.SERVER_NAME in doc["mcpServers"]


def test_install_is_idempotent(tmp_path):
    path = _claude_path(tmp_path)
    configure.install("claude-code", config_root=tmp_path, dkg_home=tmp_path / "h", launch=FIXED)
    first = path.read_bytes()

    second_result = configure.install(
        "claude-code", config_root=tmp_path, dkg_home=tmp_path / "h", launch=FIXED
    )
    second = path.read_bytes()

    assert first == second
    assert second_result["replaces_existing"] is True
    assert second_result["replaces_unmanaged"] is False
    assert second_result["changed"] is False


def test_install_replaces_a_stale_entry_of_ours(tmp_path):
    path = _claude_path(tmp_path)
    configure.install("claude-code", config_root=tmp_path, dkg_home=tmp_path / "old", launch=FIXED)
    result = configure.install("claude-code", config_root=tmp_path, dkg_home=tmp_path / "new", launch=FIXED)

    assert result["replaces_existing"] is True
    assert result["changed"] is True
    entry = _read(path)["mcpServers"][configure.SERVER_NAME]
    assert entry["args"] == ["--home", os.path.abspath(tmp_path / "new"), "mcp-stdio"]


def test_output_is_deterministic_across_runs(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    seed = {"mcpServers": {"z-server": {"command": "z"}, "a-server": {"command": "a"}}, "zz": 1, "aa": 2}
    for root in (a, b):
        root.mkdir()
        _claude_path(root).write_text(json.dumps(seed), encoding="utf-8")

    configure.install("claude-code", config_root=a, dkg_home=tmp_path / "h", launch=FIXED)
    configure.install("claude-code", config_root=b, dkg_home=tmp_path / "h", launch=FIXED)
    assert _claude_path(a).read_bytes() == _claude_path(b).read_bytes()

    # A different key insertion order in the source file must not change output.
    reordered = {"aa": 2, "zz": 1, "mcpServers": {"a-server": {"command": "a"}, "z-server": {"command": "z"}}}
    c = tmp_path / "c"
    c.mkdir()
    _claude_path(c).write_text(json.dumps(reordered, indent=4), encoding="utf-8")
    configure.install("claude-code", config_root=c, dkg_home=tmp_path / "h", launch=FIXED)
    assert _claude_path(c).read_bytes() == _claude_path(a).read_bytes()


def test_uninstall_removes_only_our_entry(tmp_path):
    path = _claude_path(tmp_path)
    original = {
        "mcpServers": {"other-server": {"command": "node", "args": ["s.js"]}},
        "theme": "dark",
    }
    path.write_text(json.dumps(original), encoding="utf-8")
    configure.install("claude-code", config_root=tmp_path, dkg_home=tmp_path / "h", launch=FIXED)

    result = configure.uninstall("claude-code", config_root=tmp_path)
    assert result["removed"] is True
    assert result["written"] is True
    assert result["removed_file"] is False

    doc = _read(path)
    assert configure.SERVER_NAME not in doc["mcpServers"]
    assert doc["mcpServers"]["other-server"] == original["mcpServers"]["other-server"]
    assert doc["theme"] == "dark"


def test_uninstall_keeps_an_empty_servers_map_when_the_file_holds_anything_else(tmp_path):
    path = _claude_path(tmp_path)
    path.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
    configure.install("claude-code", config_root=tmp_path, dkg_home=tmp_path / "h", launch=FIXED)
    configure.uninstall("claude-code", config_root=tmp_path)

    doc = _read(path)
    assert doc["mcpServers"] == {}
    assert doc["theme"] == "dark"


def test_uninstall_deletes_a_file_that_held_nothing_but_our_entry(tmp_path):
    """Symmetry: a file we created is a file we remove.

    Leaving a config holding an empty server map behind would mean an
    install-then-uninstall round trip did not restore the tree, which is the
    property the uninstall scope tests assert byte for byte.
    """
    path = _claude_path(tmp_path)
    configure.install("claude-code", config_root=tmp_path, dkg_home=tmp_path / "h", launch=FIXED)
    result = configure.uninstall("claude-code", config_root=tmp_path)

    assert result["removed"] is True
    assert result["removed_file"] is True
    assert not path.exists()


def test_uninstall_dry_run_writes_nothing(tmp_path):
    path = _claude_path(tmp_path)
    configure.install("claude-code", config_root=tmp_path, dkg_home=tmp_path / "h", launch=FIXED)
    before = path.read_bytes()

    result = configure.uninstall("claude-code", config_root=tmp_path, dry_run=True)
    assert result["removed"] is True
    assert result["written"] is False
    assert path.read_bytes() == before


def test_uninstall_refuses_an_entry_without_our_marker(tmp_path):
    path = _claude_path(tmp_path)
    foreign = {
        "mcpServers": {
            configure.SERVER_NAME: {"command": "dkg", "args": ["mcp-stdio"]},
            "other-server": {"command": "node"},
        }
    }
    raw = json.dumps(foreign, indent=4)
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(ValidationError) as excinfo:
        configure.uninstall("claude-code", config_root=tmp_path)
    assert configure.MARKER_KEY in str(excinfo.value)
    assert path.read_text(encoding="utf-8") == raw


def test_uninstall_refuses_a_non_object_entry(tmp_path):
    path = _claude_path(tmp_path)
    raw = json.dumps({"mcpServers": {configure.SERVER_NAME: "not-an-object"}})
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(ValidationError):
        configure.uninstall("claude-code", config_root=tmp_path)
    assert path.read_text(encoding="utf-8") == raw


def test_uninstall_reports_nothing_removed_when_absent(tmp_path):
    # No file at all.
    result = configure.uninstall("claude-code", config_root=tmp_path)
    assert result["removed"] is False
    assert result["file_exists"] is False
    assert result["reason"]
    assert not _claude_path(tmp_path).exists()

    # File present, but no entry of ours.
    path = _claude_path(tmp_path)
    raw = json.dumps({"mcpServers": {"other-server": {"command": "node"}}})
    path.write_text(raw, encoding="utf-8")
    result = configure.uninstall("claude-code", config_root=tmp_path)
    assert result["removed"] is False
    assert result["file_exists"] is True
    assert path.read_text(encoding="utf-8") == raw


def test_invalid_json_raises_and_leaves_bytes_untouched(tmp_path):
    path = _claude_path(tmp_path)
    raw = b'{"mcpServers": {"other": {"command": "node"},,}'
    path.write_bytes(raw)

    for call in (
        lambda: configure.plan_install("claude-code", config_root=tmp_path, dkg_home=tmp_path / "h", launch=FIXED),
        lambda: configure.install("claude-code", config_root=tmp_path, dkg_home=tmp_path / "h", launch=FIXED),
        lambda: configure.uninstall("claude-code", config_root=tmp_path),
    ):
        with pytest.raises(ValidationError):
            call()
        assert path.read_bytes() == raw

    # No temporary file was left behind either.
    assert [p.name for p in tmp_path.iterdir()] == [".claude.json"]


def test_non_object_root_raises_and_leaves_bytes_untouched(tmp_path):
    path = _claude_path(tmp_path)
    raw = b'["a", "list", "not", "an", "object"]'
    path.write_bytes(raw)

    with pytest.raises(ValidationError):
        configure.install("claude-code", config_root=tmp_path, dkg_home=tmp_path / "h", launch=FIXED)
    assert path.read_bytes() == raw


def test_wrong_type_servers_key_raises_and_leaves_bytes_untouched(tmp_path):
    path = _claude_path(tmp_path)
    raw = json.dumps({"mcpServers": ["not", "a", "map"], "theme": "dark"}).encode("utf-8")
    path.write_bytes(raw)

    with pytest.raises(ValidationError) as excinfo:
        configure.install("claude-code", config_root=tmp_path, dkg_home=tmp_path / "h", launch=FIXED)
    assert "mcpServers" in str(excinfo.value)
    assert path.read_bytes() == raw

    with pytest.raises(ValidationError):
        configure.uninstall("claude-code", config_root=tmp_path)
    assert path.read_bytes() == raw


def test_unknown_tool_raises_and_lists_supported_names(tmp_path):
    for call in (
        lambda: configure.plan_install("emacs", config_root=tmp_path, launch=FIXED),
        lambda: configure.install("emacs", config_root=tmp_path, launch=FIXED),
        lambda: configure.uninstall("emacs", config_root=tmp_path),
    ):
        with pytest.raises(ValidationError) as excinfo:
            call()
        message = str(excinfo.value)
        assert "emacs" in message
        for name in platforms.platform_names():
            assert name in message
    assert list(tmp_path.iterdir()) == []


def test_every_supported_tool_installs_and_uninstalls(tmp_path):
    for record in configure.supported_tools():
        root = tmp_path / record["name"]
        configure.install(record["name"], config_root=root, dkg_home=tmp_path / "h", launch=FIXED)
        path = root / record["relative_path"]
        assert path.exists()
        document = _read(path)
        assert configure.SERVER_NAME in configure.server_map(document, record["servers_key"])

        result = configure.uninstall(record["name"], config_root=root)
        assert result["removed"] is True
        assert not path.exists()


def test_no_function_writes_outside_the_supplied_config_root(tmp_path, monkeypatch):
    config_root = tmp_path / "config-root"
    sentinel_home = tmp_path / "pretend-home"
    sentinel_home.mkdir()

    written: list[str] = []
    real_replace = os.replace
    real_mkstemp = tempfile.mkstemp
    real_mkdir = Path.mkdir
    real_unlink = Path.unlink
    real_rmdir = Path.rmdir

    def spy_replace(src, dst, **kwargs):
        written.extend([os.fspath(src), os.fspath(dst)])
        return real_replace(src, dst, **kwargs)

    def spy_mkstemp(*args, **kwargs):
        fd, name = real_mkstemp(*args, **kwargs)
        written.append(name)
        return fd, name

    def spy_mkdir(self, *args, **kwargs):
        written.append(os.fspath(self))
        return real_mkdir(self, *args, **kwargs)

    def spy_unlink(self, *args, **kwargs):
        written.append(os.fspath(self))
        return real_unlink(self, *args, **kwargs)

    def spy_rmdir(self, *args, **kwargs):
        written.append(os.fspath(self))
        return real_rmdir(self, *args, **kwargs)

    monkeypatch.setattr(os, "replace", spy_replace)
    monkeypatch.setattr(tempfile, "mkstemp", spy_mkstemp)
    monkeypatch.setattr(Path, "mkdir", spy_mkdir)
    monkeypatch.setattr(Path, "unlink", spy_unlink)
    monkeypatch.setattr(Path, "rmdir", spy_rmdir)

    for record in configure.supported_tools():
        name = record["name"]
        configure.plan_install(name, config_root=config_root, dkg_home=tmp_path / "h", launch=FIXED)
        configure.install(name, config_root=config_root, dkg_home=tmp_path / "h", launch=FIXED, dry_run=True)
        configure.install_bundle(
            name, config_root=config_root, dkg_home=tmp_path / "h", launch=FIXED, dry_run=True
        )
        configure.install_bundle(name, config_root=config_root, dkg_home=tmp_path / "h", launch=FIXED)
        configure.uninstall_bundle(name, config_root=config_root, dry_run=True)
        configure.uninstall_bundle(name, config_root=config_root)

    assert written, "expected the install path to write something"
    root = os.path.realpath(config_root)
    for target in written:
        assert os.path.realpath(target).startswith(root), target

    assert list(sentinel_home.iterdir()) == []


def test_every_file_written_by_install_all_lives_under_the_config_root(tmp_path):
    """Walk the filesystem rather than trusting the spy, and check both ways.

    The spy above proves nothing was written elsewhere through the primitives.
    This proves the complementary claim from the other direction: everything the
    installer produced is inside the root it was given.
    """
    config_root = tmp_path / "root"
    config_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("untouched\n", encoding="utf-8")

    result = configure.install_all(
        config_root=config_root, dkg_home=tmp_path / "h", launch=FIXED, only_detected=False
    )
    assert result["selected"] == list(platforms.platform_names())

    produced = sorted(p for p in config_root.rglob("*") if p.is_file())
    assert produced, "install_all produced no files at all"
    for path in produced:
        assert config_root in path.parents

    assert sorted(p.name for p in outside.iterdir()) == ["keep.txt"]
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "untouched\n"


def test_a_monkeypatched_home_is_never_read_or_written(tmp_path, monkeypatch):
    """Point ``Path.home`` at a decoy and prove the library never goes there.

    A library that quietly defaulted to the user's home would still pass every
    test above, because they all pass a root. This one removes that escape: if
    any code path consulted ``Path.home()``, the decoy would gain files.
    """
    decoy = tmp_path / "decoy-home"
    decoy.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: decoy))

    config_root = tmp_path / "root"
    configure.install_all(config_root=config_root, dkg_home=tmp_path / "h", launch=FIXED, only_detected=False)
    configure.detect_installed(config_root=config_root)
    configure.uninstall_all(config_roots=[config_root])

    assert list(decoy.iterdir()) == []


def test_modules_never_look_up_a_real_home_directory():
    """The only way into the filesystem must be the caller-supplied root.

    A source scan is cheap and catches a future edit that reintroduces a
    home-directory default, which no behavioural test would notice until it had
    already written to a developer's own configuration. Every module in the
    installer is scanned, not only the first one that was written.
    """
    # Attribute form: Path.home(), path.expanduser(), os.environ, os.getenv.
    forbidden_attrs = {"home", "expanduser", "expandvars", "environ", "environb", "getenv", "getenvb"}
    # Bare-name form: the same things pulled in by "from os import environ".
    forbidden_names = {"expanduser", "expandvars", "environ", "environb", "getenv", "getenvb"}
    for module in (configure, platforms, artifacts, rules):
        source = Path(module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                assert node.attr not in forbidden_attrs, (module.__name__, node.attr)
            if isinstance(node, ast.Name):
                assert node.id not in forbidden_names, (module.__name__, node.id)
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    assert alias.name not in forbidden_attrs, (module.__name__, alias.name)


def test_no_dash_characters_that_the_project_forbids():
    """The em dash and en dash gate, applied to this module's own files.

    The forbidden characters are built from their codepoints rather than
    written literally. A test that spells them out puts them in a tracked
    file and trips the very gate it is guarding, which is how this test
    first failed the repository-wide scan.
    """
    em, en = chr(0x2014), chr(0x2013)
    for module in (configure, platforms, artifacts, rules):
        text = Path(module.__file__).read_text(encoding="utf-8")
        assert em not in text, module.__name__
        assert en not in text, module.__name__

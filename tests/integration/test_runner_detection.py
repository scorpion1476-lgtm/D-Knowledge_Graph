"""R-10: detect how the tool was installed, and emit a command that resolves.

Every test here describes a machine rather than inspecting the one running the
suite. The three environment-dependent inputs are injected: what resolves on
PATH, which directory this interpreter installs console scripts into, and
whether a given path exists. A test that depended on whether uv or pipx happens
to be installed on the developer's laptop would pass or fail for reasons that
have nothing to do with the code.

The invariant that holds across every branch, and is asserted in every one of
them: the command written into a shared configuration file is never an absolute
path.
"""

from __future__ import annotations

import json
import os
import shutil
import sysconfig
from pathlib import Path

import pytest

from dkg.mcp import configure, platforms


def _machine(*, on_path=(), script_dir="/usr/local/bin", venv=False):
    """Return the injected ``which``, ``script_dir`` and ``path_exists``."""
    available = set(on_path)
    venv_marker = os.path.join(os.path.dirname(script_dir), "pyvenv.cfg")

    def which(name):
        return f"{script_dir}/{name}" if name in available else None

    def path_exists(path):
        return venv and path == venv_marker

    return {"which": which, "script_dir": script_dir, "path_exists": path_exists}


def test_an_explicit_override_wins_and_no_detection_runs():
    def explode(_name):
        raise AssertionError("detection must not run when an override was given")

    launch = configure.detect_launch(
        override="/opt/venv/bin/dkg", **{**_machine(), "which": explode}
    )
    assert launch.command == "/opt/venv/bin/dkg"
    assert launch.runner == "explicit"
    assert launch.prefix_args == ()
    # The one case where an absolute path is written is the case a human asked
    # for by name, which is why the record reports it rather than hiding it.
    assert launch.absolute is True


def test_a_pipx_install_uses_the_console_script_shim():
    launch = configure.detect_launch(
        **_machine(on_path=("dkg", "pipx", "uvx"), script_dir="/home/u/.local/pipx/venvs/d-knowledge-graph/bin")
    )
    assert launch.installed_by == "pipx"
    assert launch.command == "dkg"
    assert launch.prefix_args == ()
    assert launch.resolves is True
    assert launch.absolute is False
    assert "pipx" in launch.basis


def test_a_uv_tool_install_uses_the_console_script_shim():
    launch = configure.detect_launch(
        **_machine(on_path=("dkg", "uv", "uvx"), script_dir="/home/u/.local/share/uv/tools/d-knowledge-graph/bin")
    )
    assert launch.installed_by == "uv-tool"
    assert launch.command == "dkg"
    assert launch.prefix_args == ()
    assert launch.absolute is False


def test_a_virtualenv_install_prefers_an_isolated_runner():
    """The case detection exists for.

    An editor spawning an MCP server does not activate anything, so the console
    script inside a virtual environment would simply not be found.
    """
    launch = configure.detect_launch(
        **_machine(on_path=("dkg", "uvx", "uv", "pipx"), script_dir="/proj/.venv/bin", venv=True)
    )
    assert launch.installed_by == "venv"
    assert launch.runner == "uvx"
    assert launch.command == "uvx"
    assert launch.prefix_args == ("--from", "d-knowledge-graph", "dkg")
    assert launch.absolute is False
    assert "pyvenv.cfg" in launch.basis


def test_the_isolated_runner_preference_order_is_uvx_then_uv_then_pipx():
    seen = []
    for available, expected_command, expected_prefix in (
        (("uvx", "uv", "pipx"), "uvx", ("--from", "d-knowledge-graph", "dkg")),
        (("uv", "pipx"), "uv", ("tool", "run", "--from", "d-knowledge-graph", "dkg")),
        (("pipx",), "pipx", ("run", "--spec", "d-knowledge-graph", "dkg")),
    ):
        launch = configure.detect_launch(
            **_machine(on_path=available, script_dir="/proj/.venv/bin", venv=True)
        )
        seen.append(launch.command)
        assert launch.command == expected_command
        assert launch.prefix_args == expected_prefix
        assert launch.absolute is False
    assert seen == ["uvx", "uv", "pipx"]


def test_a_virtualenv_with_no_runner_falls_back_and_says_so():
    launch = configure.detect_launch(**_machine(on_path=(), script_dir="/proj/.venv/bin", venv=True))
    assert launch.installed_by == "venv"
    assert launch.runner == "console-script"
    assert launch.command == "dkg"
    assert launch.resolves is False
    assert launch.absolute is False
    assert "may not resolve" in launch.basis


def test_a_virtualenv_defers_to_a_console_script_that_lives_elsewhere():
    """A dkg on PATH outside this environment already works, so use it."""

    def which(name):
        return "/usr/local/bin/dkg" if name == "dkg" else None

    launch = configure.detect_launch(
        **{**_machine(script_dir="/proj/.venv/bin", venv=True), "which": which}
    )
    assert launch.runner == "console-script"
    assert launch.command == "dkg"
    assert launch.resolves is True


def test_a_system_install_uses_the_console_script():
    launch = configure.detect_launch(**_machine(on_path=("dkg",), script_dir="/usr/local/bin"))
    assert launch.installed_by == "system"
    assert launch.runner == "console-script"
    assert launch.command == "dkg"
    assert launch.resolves is True


def test_nothing_on_path_at_all_is_reported_as_unresolved_not_claimed_to_work():
    launch = configure.detect_launch(**_machine(on_path=(), script_dir="/usr/local/bin"))
    assert launch.command == "dkg"
    assert launch.resolves is False
    assert launch.absolute is False
    assert "is on PATH" in launch.basis
    assert "unresolved" in launch.basis


@pytest.mark.parametrize(
    "machine",
    [
        dict(on_path=("dkg",), script_dir="/usr/local/bin"),
        dict(on_path=("dkg", "pipx"), script_dir="/home/u/.local/pipx/venvs/d/bin"),
        dict(on_path=("uvx",), script_dir="/proj/.venv/bin", venv=True),
        dict(on_path=("pipx",), script_dir="/proj/.venv/bin", venv=True),
        dict(on_path=(), script_dir="/proj/.venv/bin", venv=True),
        dict(on_path=(), script_dir="/usr/local/bin"),
    ],
)
def test_no_detected_launch_is_ever_an_absolute_path(machine):
    launch = configure.detect_launch(**_machine(**machine))
    assert launch.absolute is False
    assert not launch.command.startswith("/")
    assert not any(a.startswith("/") for a in launch.prefix_args)


def test_the_prefix_args_precede_the_home_flag_in_the_written_entry(tmp_path):
    launch = configure.detect_launch(
        **_machine(on_path=("uvx",), script_dir="/proj/.venv/bin", venv=True)
    )
    configure.install(
        "claude-code", config_root=tmp_path, dkg_home=tmp_path / "h", launch=launch, platform_key="linux"
    )
    entry = json.loads((tmp_path / ".claude.json").read_text(encoding="utf-8"))["mcpServers"][
        configure.SERVER_NAME
    ]
    assert entry["command"] == "uvx"
    assert entry["args"][:3] == ["--from", "d-knowledge-graph", "dkg"]
    assert entry["args"][3] == "--home"
    assert entry["args"][-1] == "mcp-stdio"


def test_install_consults_detection_when_no_command_is_given(tmp_path, monkeypatch):
    """The default path really detects, rather than hard-coding the script.

    ``shutil.which`` and ``sysconfig.get_path`` are patched at the module level
    they are read from, so this exercises the same entry point a user gets.
    """
    monkeypatch.setattr(shutil, "which", lambda name: "/proj/.venv/bin/uvx" if name == "uvx" else None)
    monkeypatch.setattr(sysconfig, "get_path", lambda name: "/proj/.venv/bin")
    monkeypatch.setattr(os.path, "exists", lambda p: p == "/proj/.venv/pyvenv.cfg")

    result = configure.install(
        "cursor", config_root=tmp_path, dkg_home=tmp_path / "h", platform_key="linux"
    )
    assert result["launch"]["runner"] == "uvx"
    assert result["entry"]["command"] == "uvx"


def test_no_written_config_ever_holds_an_absolute_launch_command(tmp_path):
    """Across every tool and every detection outcome, on a shared file."""
    machines = [
        dict(on_path=("dkg",), script_dir="/usr/local/bin"),
        dict(on_path=("uvx",), script_dir="/proj/.venv/bin", venv=True),
        dict(on_path=(), script_dir="/proj/.venv/bin", venv=True),
    ]
    for index, machine in enumerate(machines):
        launch = configure.detect_launch(**_machine(**machine))
        root = tmp_path / f"root-{index}"
        configure.install_all(
            config_root=root,
            dkg_home=tmp_path / "h",
            launch=launch,
            platform_key="linux",
            only_detected=False,
        )
        for record in configure.supported_tools(platform_key="linux"):
            document = json.loads((root / record["relative_path"]).read_text(encoding="utf-8"))
            entry = configure.server_map(document, record["servers_key"])[configure.SERVER_NAME]
            # Whatever the entry shape, the launch command is either at the top
            # of the entry or inside its transport object.
            command = entry.get("command", entry.get("transport", {}).get("command"))
            first = command[0] if isinstance(command, list) else command
            assert not os.path.isabs(first), (record["name"], first)


def test_supported_tools_all_declare_a_relative_config_path():
    for target in platforms.platforms():
        for key in platforms.PLATFORM_KEYS:
            assert not Path(platforms.resolve_relative(target.config, key)).is_absolute()

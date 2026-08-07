"""End-to-end tests for skills, plugin manifests, hooks, and version.

Covers G-01 (skill package usage), G-02 (plugin manifest validation),
G-03 (hook based health check), and G-08 (version compatibility checks).
"""

from __future__ import annotations

import io
import json
from importlib import resources

import pytest

from dkg.cli.hook_health import run as hook_health_run
from dkg.core.db import open_database
from dkg.core.errors import ConfigError, ValidationError
from dkg.core.version import CURRENT_SCHEMA_MAJOR, record_open
from dkg.plugins.manifest import (
    load_manifest,
    load_schema,
    validate_manifest,
)


def test_skill_package_bundles_usage_doc():
    text = resources.files("dkg.skills").joinpath("dkg-usage.md").read_text(encoding="utf-8")
    assert text.startswith("# dkg-usage")


def test_skill_package_bundles_the_three_workflow_commands():
    """The workflow commands ship in the same package as the usage document."""
    from dkg.mcp import artifacts

    shipped = {
        entry.name for entry in resources.files("dkg.skills").iterdir() if entry.name.endswith(".md")
    }
    names = [command.name for command in artifacts.workflow_commands()]
    assert len(names) == 3
    for name in names:
        assert f"{name}.md" in shipped


def test_skill_package_missing_file_raises():
    # An unregistered skill file must be reported cleanly.
    with pytest.raises(FileNotFoundError):
        resources.files("dkg.skills").joinpath("does_not_exist.md").read_text(encoding="utf-8")


def test_plugin_manifest_example_validates():
    raw = json.loads(
        resources.files("dkg.plugins").joinpath("example.json").read_text(encoding="utf-8")
    )
    validate_manifest(raw)


def test_plugin_manifest_missing_required_field_rejected():
    schema = load_schema()
    with pytest.raises(ValidationError, match="required"):
        validate_manifest({"version": "0.1.0"}, schema)


def test_plugin_manifest_wrong_type_rejected():
    schema = load_schema()
    with pytest.raises(ValidationError):
        validate_manifest(
            {
                "name": "valid-name",
                "version": "0.1.0",
                "capabilities": "not-a-list",
            },
            schema,
        )


def test_plugin_manifest_bad_name_pattern_rejected():
    schema = load_schema()
    with pytest.raises(ValidationError, match="pattern"):
        validate_manifest(
            {
                "name": "UPPER-not-allowed",
                "version": "0.1.0",
                "capabilities": ["dkg.search"],
            },
            schema,
        )


def test_plugin_manifest_unknown_top_level_key_rejected():
    schema = load_schema()
    with pytest.raises(ValidationError, match="unexpected key"):
        validate_manifest(
            {
                "name": "valid",
                "version": "0.1.0",
                "capabilities": ["dkg.search"],
                "malicious_extra_key": "hi",
            },
            schema,
        )


def test_plugin_manifest_load_missing_file_rejected(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_manifest(tmp_path / "no_such.json")


def test_hook_health_ok_on_fresh_home(tmp_path, monkeypatch):
    monkeypatch.setenv("DKG_HOME", str(tmp_path / ".dkg"))
    buf = io.StringIO()
    rc = hook_health_run(out=buf)
    payload = json.loads(buf.getvalue())
    assert rc == 0
    assert payload["ok"] is True
    assert "capabilities" in payload


def test_hook_health_records_capabilities_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("DKG_HOME", str(tmp_path / ".dkg"))
    buf = io.StringIO()
    hook_health_run(out=buf)
    payload = json.loads(buf.getvalue())
    assert isinstance(payload["capabilities"], list)
    for cap in payload["capabilities"]:
        assert set(cap.keys()) >= {"name", "available", "reason"}


def test_version_matches_current_schema(tmp_path):
    with open_database(tmp_path / "graph.sqlite") as db:
        vi = record_open(db)
        assert vi.schema_major == CURRENT_SCHEMA_MAJOR


def test_future_schema_major_rejected(tmp_path):
    with open_database(tmp_path / "graph.sqlite") as db:
        db.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_major', ?);",
            (str(CURRENT_SCHEMA_MAJOR + 1),),
        )
        with pytest.raises(ConfigError, match="upgrade"):
            record_open(db)


def test_past_or_equal_schema_major_accepted(tmp_path):
    # A DB whose recorded schema_major is not greater than the current build
    # is accepted without raising. The value returned is the recorded one;
    # forward migration is separately triggered by apply_migrations.
    with open_database(tmp_path / "graph.sqlite") as db:
        db.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_major', ?);",
            (str(max(0, CURRENT_SCHEMA_MAJOR - 1)),),
        )
        vi = record_open(db)
        assert vi.schema_major <= CURRENT_SCHEMA_MAJOR

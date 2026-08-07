"""Custom-language registration via a project-owned config file.

The config-validation tests are deterministic and need no grammar. The Ruby
worked example is capability-detected: it runs when the user-provided
tree-sitter-ruby grammar is importable, else it skips with an honest reason.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter")

from dkg.code.impact import blast_radius  # noqa: E402
from dkg.code.ingest import ingest_repo  # noqa: E402
from dkg.code.languages import is_permissive, load_registry, parse_config  # noqa: E402
from dkg.code.parser import language_for  # noqa: E402
from dkg.core.db import open_database  # noqa: E402
from dkg.core.errors import ValidationError  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_DIR = ROOT / "examples" / "custom-language"
EXAMPLE_CONFIG = EXAMPLE_DIR / "dkg.languages.json"


# -- deterministic config validation (no grammar required) ------------------


def test_example_config_loads_and_routes_extension():
    registry, warnings = load_registry(EXAMPLE_CONFIG)
    assert warnings == []  # MIT is permissive
    assert registry.get("ruby") is not None
    assert registry.language_for_ext(".rb") == "ruby"
    # Extension routing consults the registry for a non-built-in extension.
    assert language_for("greeter.rb", registry) == "ruby"
    # A built-in extension is never shadowed by the registry.
    assert language_for("a.py", registry) == "python"


def test_config_requires_licence():
    bad = {
        "languages": [
            {
                "name": "ruby",
                "grammar_module": "tree_sitter_ruby",
                "extensions": [".rb"],
                "symbols": {"class": ["class"]},
            }
        ]
    }
    with pytest.raises(ValidationError, match="licence"):
        parse_config(bad)


def test_non_permissive_licence_warns_but_loads():
    cfg = {
        "languages": [
            {
                "name": "somelang",
                "grammar_module": "tree_sitter_somelang",
                "licence": "GPL-3.0",
                "extensions": [".sl"],
                "symbols": {"function": ["function_definition"]},
            }
        ]
    }
    registry, warnings = parse_config(cfg)
    assert registry.get("somelang") is not None
    assert any("permissive" in w for w in warnings)
    assert not is_permissive("GPL-3.0")
    assert is_permissive("MIT") and is_permissive("Apache-2.0")


def test_config_rejects_empty_languages():
    with pytest.raises(ValidationError):
        parse_config({"languages": []})


# -- Ruby worked example, capability-detected -------------------------------


def _git(cwd, *a):
    subprocess.run(["git", *a], cwd=cwd, capture_output=True)


def test_ruby_worked_example_end_to_end(tmp_path):
    pytest.importorskip("tree_sitter_ruby")
    registry, _ = load_registry(EXAMPLE_CONFIG)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "greeter.rb").write_text(
        (EXAMPLE_DIR / "greeter.rb").read_text(encoding="utf-8"), encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=x@x", "-c", "user.name=x", "commit", "-qm", "init")

    with open_database(tmp_path / "g.db") as db:
        result = ingest_repo(db, repo, audit_path=tmp_path / "a.log", languages=registry)
        assert result["parsed_files"] == 1

        classes = {
            r["display"]
            for r in db.fetchall(
                "SELECT display FROM entities WHERE kind='code:class';"
            )
        }
        assert {"Greeter", "LoudGreeter"} <= classes

        calls = db.fetchall(
            "SELECT s.canonical AS src, o.canonical AS dst FROM relationships r "
            "JOIN entities s ON s.entity_id=r.subject_id "
            "JOIN entities o ON o.entity_id=r.object_id "
            "WHERE r.predicate='code:calls';"
        )
        call_pairs = {(c["src"], c["dst"]) for c in calls}
        assert ("greeter.rb::Greeter.greet", "greeter.rb::Greeter.format_message") in call_pairs

        inherits = db.fetchall(
            "SELECT s.canonical AS src, o.canonical AS dst FROM relationships r "
            "JOIN entities s ON s.entity_id=r.subject_id "
            "JOIN entities o ON o.entity_id=r.object_id "
            "WHERE r.predicate='code:inherits';"
        )
        assert ("greeter.rb::LoudGreeter", "greeter.rb::Greeter") in {
            (i["src"], i["dst"]) for i in inherits
        }

        # Impact analysis works over the custom language: whoever calls
        # format_message is in its blast radius.
        radius = blast_radius(db, "greeter.rb::Greeter.format_message")
        impacted = {i["canonical"] for i in radius["impacted"]}
        assert "greeter.rb::Greeter.greet" in impacted

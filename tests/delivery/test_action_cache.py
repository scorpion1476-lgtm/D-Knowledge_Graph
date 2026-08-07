"""Caching the built graph between continuous-integration runs (R-18).

Two halves, because the requirement has two halves.

The DEFINITION half is checked against `action.yml` itself: there is a cache
step, it is pinned to a full commit SHA, and its key genuinely contains all
three components the requirement names (the runner platform, the database schema
version, and the dependency lockfiles). A key missing one of those would restore
a graph built by a different schema or on a different platform, so each is
asserted individually rather than as one blob.

The FALLBACK half is checked as a unit test with a deliberately corrupt
database, because that is the branch a live run would almost never take and the
one whose failure would be silent. A restored file that is not a database, is
truncated, is missing tables, or was written by a newer schema must be removed
so the next step rebuilds in full. Analysing it instead would produce a wrong
graph that nothing downstream could detect.

No hosted run is performed here; see the verification note in the module that
ships the action.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from dkg.code.report import CACHE_REQUIRED_TABLES, prepare_cached_database
from dkg.core.db import open_database
from dkg.core.version import CURRENT_SCHEMA_MAJOR, record_open

ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / "action.yml"


def _action_text() -> str:
    return ACTION.read_text(encoding="utf-8")


def _cache_step(text: str) -> str:
    """The lines of the cache step, from its `uses:` to the next step."""
    lines = text.split("\n")
    start = next(i for i, line in enumerate(lines) if "actions/cache@" in line)
    out = [lines[start]]
    for line in lines[start + 1 :]:
        if re.match(r"^    - name:", line):
            break
        out.append(line)
    return "\n".join(out)


def _good_database(path: Path) -> Path:
    with open_database(path) as db:
        record_open(db)
    return path


# -- the definition ----------------------------------------------------------


def test_the_action_caches_the_built_graph():
    text = _action_text()
    assert "actions/cache@" in text, "the action must restore and save the built graph"
    step = _cache_step(text)
    assert "path:" in step
    assert "dkg-home" in step, "the cache must cover the DKG home that holds the graph"


def test_the_cache_action_is_pinned_to_a_commit_sha():
    text = _action_text()
    refs = re.findall(r"uses:\s*(actions/cache@\S+)", text)
    assert refs, "expected a cache action reference"
    for ref in refs:
        assert re.search(r"@[0-9a-f]{40}$", ref), f"{ref} is not pinned to a 40-hex commit SHA"


def test_the_cache_key_contains_the_runner_platform():
    key = _cache_key(_action_text())
    assert "runner.os" in key, "a graph built on one platform is not portable to another"
    assert "runner.arch" in key


def test_the_cache_key_contains_the_database_schema_version():
    text = _action_text()
    key = _cache_key(text)
    assert "steps.schema.outputs.schema-version" in key
    # And the value really is read from the tool, not guessed in the YAML.
    assert "CURRENT_SCHEMA_MAJOR" in text


def test_the_cache_key_contains_the_dependency_lockfiles():
    key = _cache_key(_action_text())
    assert "hashFiles(" in key
    for lockfile in ("poetry.lock", "package-lock.json", "go.sum", "Cargo.lock", "uv.lock"):
        assert lockfile in key, f"{lockfile} is not covered by the cache key"


def test_the_partial_restore_never_crosses_a_platform_or_schema_boundary():
    step = _cache_step(_action_text())
    restore = step.split("restore-keys:", 1)
    assert len(restore) == 2, "a partial restore is what makes the restore incremental"
    prefix = restore[1]
    assert "runner.os" in prefix
    assert "steps.schema.outputs.schema-version" in prefix


def test_the_analysis_validates_the_restored_database():
    text = _action_text()
    assert "--cache-check" in text
    assert re.search(r"^  cache-status:\s*$", text, re.M), "the cache outcome must be an output"


def _cache_key(text: str) -> str:
    step = _cache_step(text)
    match = re.search(r"^\s*key:\s*(.+)$", step, re.M)
    assert match, "the cache step must declare a key"
    return match.group(1)


# -- the fallback ------------------------------------------------------------


def test_a_missing_database_is_a_clean_miss(tmp_path):
    result = prepare_cached_database(tmp_path / "absent.sqlite")
    assert result["status"] == "miss"
    assert result["usable"] is False
    assert result["removed"] == []


def test_a_sound_database_is_a_hit(tmp_path):
    path = _good_database(tmp_path / "g.sqlite")
    result = prepare_cached_database(path)
    assert result["status"] == "hit"
    assert result["usable"] is True
    assert result["schema_major"] == CURRENT_SCHEMA_MAJOR
    assert path.exists(), "a usable database must not be removed"


def test_a_corrupt_database_is_removed_so_the_next_build_is_full(tmp_path):
    path = tmp_path / "g.sqlite"
    path.write_bytes(b"this is definitely not a database" * 200)
    (tmp_path / "g.sqlite-wal").write_bytes(b"stale write ahead log")

    result = prepare_cached_database(path)
    assert result["status"] == "unusable"
    assert result["usable"] is False
    assert not path.exists(), "an unusable database must be removed, not analysed"
    # The write-ahead log goes with it, or the next open reads a stale page.
    assert not (tmp_path / "g.sqlite-wal").exists()
    assert "g.sqlite" in result["removed"]
    assert "g.sqlite-wal" in result["removed"]


def test_a_truncated_database_is_removed(tmp_path):
    path = _good_database(tmp_path / "g.sqlite")
    raw = path.read_bytes()
    path.write_bytes(raw[: max(1, len(raw) // 3)] + b"\x00" * 64)
    result = prepare_cached_database(path)
    assert result["status"] == "unusable"
    assert not path.exists()


def test_a_database_missing_required_tables_is_removed(tmp_path):
    path = tmp_path / "g.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE something_else(x INTEGER);")
    conn.commit()
    conn.close()
    result = prepare_cached_database(path)
    assert result["status"] == "unusable"
    assert "missing required tables" in result["reason"]
    assert not path.exists()


def test_a_database_from_a_newer_schema_is_removed(tmp_path):
    path = _good_database(tmp_path / "g.sqlite")
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_major', ?);",
        (str(CURRENT_SCHEMA_MAJOR + 5),),
    )
    conn.commit()
    conn.close()
    result = prepare_cached_database(path)
    assert result["status"] == "unusable"
    assert "newer major schema" in result["reason"]
    assert not path.exists()


def test_every_required_table_is_actually_present_in_a_fresh_database(tmp_path):
    # Guards the check itself: if a required table were misspelled, a sound
    # database would be reported unusable on every run and the cache would be
    # useless in a way no other test here would notice.
    path = _good_database(tmp_path / "g.sqlite")
    conn = sqlite3.connect(path)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table';")}
    conn.close()
    assert set(CACHE_REQUIRED_TABLES) <= names


def test_the_full_rebuild_after_a_corrupt_restore_actually_works(tmp_path):
    pytest.importorskip("tree_sitter")
    from dkg.code.ingest import ingest_repo
    from dkg.code.report import build_report

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "lib.py").write_text("def helper():\n    return 1\n", encoding="utf-8")

    path = tmp_path / "g.sqlite"
    path.write_bytes(b"corrupt" * 500)
    result = prepare_cached_database(path)
    assert result["status"] == "unusable"

    # The fallback is real: a full build over the removed file succeeds.
    with open_database(path) as db:
        ingest_repo(db, repo, audit_path=tmp_path / "a.log", incremental=False)
        report = build_report(db, repo)
    assert report["summary"]["files"] == 1
    assert report["summary"]["total_symbols"] >= 1

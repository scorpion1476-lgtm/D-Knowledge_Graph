"""Multi-repo registry and watch daemon.

The registry tests are deterministic and need no extra. The re-ingest tests use
the code extra (skip without it) and are made deterministic by polling for the
expected result with a timeout, never by a fixed sleep, and they assert a clean
stop with no orphaned thread.
"""

from __future__ import annotations

import subprocess
import time

import pytest

from dkg.core.db import open_database
from dkg.core.errors import ValidationError
from dkg.watch.daemon import WatchDaemon, watchfiles_available
from dkg.watch.registry import Registry


def _wait_until(pred, timeout: float = 6.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return pred()


def _git(cwd, *a):
    subprocess.run(["git", *a], cwd=cwd, capture_output=True)


def _make_repo(tmp_path, body: str):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "a.py").write_text(body, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=x@x", "-c", "user.name=x", "commit", "-qm", "init")
    return repo


def _has_symbol(db, name: str) -> bool:
    row = db.fetchone(
        "SELECT 1 AS y FROM entities WHERE kind LIKE 'code:%' AND display=? LIMIT 1;", (name,)
    )
    return row is not None


# -- registry (no extra required) -------------------------------------------


def test_registry_add_list_remove_and_rejects(tmp_path):
    reg = Registry(tmp_path / "registry.json")
    a = tmp_path / "a"
    a.mkdir()
    b = tmp_path / "b"
    b.mkdir()
    reg.add("alpha", a)
    reg.add("bravo", b)
    assert {e.name for e in reg.list()} == {"alpha", "bravo"}

    # persistence across instances
    reg2 = Registry(tmp_path / "registry.json")
    assert len(reg2) == 2

    with pytest.raises(ValidationError, match="already registered"):
        reg2.add("alpha", b)
    with pytest.raises(ValidationError, match="does not exist"):
        reg2.add("charlie", tmp_path / "nope")

    reg2.remove("alpha")
    assert {e.name for e in reg2.list()} == {"bravo"}
    with pytest.raises(ValidationError, match="not registered"):
        reg2.remove("alpha")


def test_backend_selection(tmp_path):
    reg = Registry(tmp_path / "registry.json")
    assert WatchDaemon(tmp_path / "g.db", reg, use_watchfiles=False).backend == "polling"
    auto = WatchDaemon(tmp_path / "g.db", reg, use_watchfiles=None)
    assert auto.backend == ("watchfiles" if watchfiles_available() else "polling")


def test_failed_reingest_is_retried_not_latched(tmp_path):
    # A transient re-ingest failure must not latch the content signature; the
    # next poll of the same (unchanged) repo has to retry. Uses a custom
    # reingest_fn, so no extra is required.
    reg = Registry(tmp_path / "registry.json")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.txt").write_text("x", encoding="utf-8")
    reg.add("r", repo)
    calls = {"n": 0}

    def flaky(_db, _path, _audit):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient failure")
        return {"mode": "ok", "parsed_files": 1, "nodes": 1, "edges": 0}

    daemon = WatchDaemon(tmp_path / "g.db", reg, reingest_fn=flaky, use_watchfiles=False)
    first = daemon.poll_once()
    assert "error" in first["r"]
    assert daemon.health()["repos"][0]["status"] == "error"

    # Same filesystem state, but the failed re-ingest is retried and succeeds.
    second = daemon.poll_once()
    assert second["r"]["changed"] is True and "result" in second["r"]
    assert daemon.health()["repos"][0]["status"] == "ok"


# -- incremental re-ingest (code extra) -------------------------------------


def test_poll_once_incremental_reingest(tmp_path):
    pytest.importorskip("tree_sitter")
    repo = _make_repo(tmp_path, "def alpha():\n    return 1\n")
    reg = Registry(tmp_path / "registry.json")
    reg.add("r", repo)
    daemon = WatchDaemon(tmp_path / "g.db", reg, audit_path=tmp_path / "a.log", use_watchfiles=False)

    first = daemon.poll_once()
    assert first["r"]["changed"] is True
    with open_database(tmp_path / "g.db") as db:
        assert _has_symbol(db, "alpha")

    # No filesystem change: no re-ingest.
    assert daemon.poll_once()["r"]["changed"] is False

    # Modify a tracked file: the git-incremental re-ingest picks it up.
    (repo / "a.py").write_text("def alpha():\n    return 1\ndef beta():\n    return alpha()\n", encoding="utf-8")
    third = daemon.poll_once()
    assert third["r"]["changed"] is True
    assert third["r"]["result"]["mode"] == "git-incremental"
    with open_database(tmp_path / "g.db") as db:
        assert _has_symbol(db, "beta")


def test_daemon_lifecycle_clean_stop_no_orphan(tmp_path):
    pytest.importorskip("tree_sitter")
    repo = _make_repo(tmp_path, "def alpha():\n    return 1\n")
    reg = Registry(tmp_path / "registry.json")
    reg.add("r", repo)
    daemon = WatchDaemon(
        tmp_path / "g.db", reg, audit_path=tmp_path / "a.log", poll_interval=0.03, use_watchfiles=False
    )
    assert daemon.backend == "polling"

    daemon.start()
    try:
        assert _wait_until(lambda: daemon.health()["repos"][0]["reingests"] >= 1)
        base = daemon.health()["repos"][0]["reingests"]
        (repo / "a.py").write_text(
            "def alpha():\n    return 1\ndef beta():\n    return 1\n", encoding="utf-8"
        )
        assert _wait_until(lambda: daemon.health()["repos"][0]["reingests"] >= base + 1)
    finally:
        daemon.stop(timeout=5)

    # Clean shutdown: the worker thread has terminated, no orphan.
    assert not daemon.is_running()
    assert daemon._thread is not None and not daemon._thread.is_alive()

    with open_database(tmp_path / "g.db") as db:
        assert _has_symbol(db, "beta")
    health = daemon.health()
    assert health["healthy"] is True
    assert health["running"] is False

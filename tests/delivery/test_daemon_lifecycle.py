"""R-24: the watcher as a MANAGED background service.

The row lists seven things, and they are listed because each one is a specific
failure of running a watcher as a bare foreground loop:

1. start, stop, restart, status, and log subcommands
2. one supervised worker per repository
3. a process-identity file so a second start cannot double-run
4. per-repository log files
5. reconciliation when the registry changes
6. a health check that restarts a dead worker
7. stopping cleanly, with nothing orphaned

Most of these are tested in-process against the Supervisor, because a test that
must fork to assert anything is a slow test that reports "it did not work"
without saying why. The genuinely cross-process facts, that the identity file is
atomic against a real second process and that a spawned service survives its
parent and stops on a signal, are tested by actually spawning one. Those are
marked and bounded so they cannot hang the suite.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from dkg.watch.registry import Registry
from dkg.watch.service import (
    LOG_MAX_BYTES,
    RepoWorker,
    ServicePaths,
    Supervisor,
    claim_pid_file,
    list_service_logs,
    process_alive,
    read_pid_file,
    read_service_log,
    release_pid_file,
    service_status,
    stop_service,
)

ROOT = Path(__file__).resolve().parents[2]


def _repo(root: Path, name: str = "lib.py", body: str = "def helper():\n    return 1\n") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(body, encoding="utf-8")
    return root


@pytest.fixture
def paths(tmp_path) -> ServicePaths:
    home = tmp_path / "home"
    home.mkdir()
    return ServicePaths(home)


def _supervisor(paths, cfg, registry, **kw) -> Supervisor:
    """A supervisor whose re-ingest is a stub, so these test supervision only."""
    kw.setdefault("reingest_fn", lambda db, path, audit: {"mode": "stub", "parsed_files": 1})
    kw.setdefault("poll_interval", 0.02)
    return Supervisor(cfg.db_path, registry, paths, **kw)


# -- 3. the process-identity file ---------------------------------------------


def test_a_first_claim_succeeds_and_records_the_pid(paths):
    assert claim_pid_file(paths, 4242) is True

    record = read_pid_file(paths)
    assert record["pid"] == 4242
    assert record["started_at"]


def test_a_second_claim_by_a_live_process_is_refused(paths):
    """This is the double-run guard. Two daemons on one database is the bug."""
    assert claim_pid_file(paths, os.getpid()) is True

    assert claim_pid_file(paths, os.getpid() + 1) is False


def test_a_stale_identity_file_is_reclaimed_rather_than_blocking_forever(paths):
    """After a power loss the file outlives its process. It must not be a lock."""
    dead = _a_pid_that_is_not_running()
    paths.home.mkdir(parents=True, exist_ok=True)
    paths.pid_file.write_text(json.dumps({"pid": dead, "started_at": "old"}), encoding="utf-8")

    assert claim_pid_file(paths, os.getpid()) is True
    assert read_pid_file(paths)["pid"] == os.getpid()


def test_a_corrupt_identity_file_is_not_mistaken_for_a_running_service(paths):
    paths.home.mkdir(parents=True, exist_ok=True)
    paths.pid_file.write_text("this is not json", encoding="utf-8")

    assert read_pid_file(paths) is None
    assert service_status(paths)["running"] is False


def test_releasing_only_removes_our_own_identity_file(paths):
    claim_pid_file(paths, 4242)

    release_pid_file(paths, pid=9999)
    assert paths.pid_file.exists(), "another process's identity must not be removed"

    release_pid_file(paths, pid=4242)
    assert not paths.pid_file.exists()


def _a_pid_that_is_not_running() -> int:
    """A pid that has certainly exited: spawn something trivial and reap it."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=30)
    for _ in range(200):
        if not process_alive(proc.pid):
            return proc.pid
        time.sleep(0.01)
    pytest.skip("could not obtain a reliably dead pid on this host")


def test_the_liveness_probe_agrees_with_reality():
    """Negative control: without this the reclaim test could pass vacuously."""
    assert process_alive(os.getpid()) is True
    assert process_alive(_a_pid_that_is_not_running()) is False
    assert process_alive(0) is False
    assert process_alive(-1) is False


# -- 2 and 5. one worker per repository, reconciled against the registry -------


def test_one_worker_is_started_for_each_registered_repository(paths, cfg, tmp_path):
    registry = Registry.in_home(paths.home)
    registry.add("one", _repo(tmp_path / "one"))
    registry.add("two", _repo(tmp_path / "two"))
    sup = _supervisor(paths, cfg, registry)

    changes = sup.reconcile()
    try:
        assert sorted(changes["started"]) == ["one", "two"]
        assert sup.status()["workers"] == 2
        assert all(r["alive"] for r in sup.status()["repos"])
    finally:
        sup.stop()


def test_a_repository_added_while_running_is_picked_up(paths, cfg, tmp_path):
    """The registry is edited while the service runs. That is the normal case."""
    registry = Registry.in_home(paths.home)
    registry.add("one", _repo(tmp_path / "one"))
    sup = _supervisor(paths, cfg, registry)
    sup.reconcile()
    try:
        Registry.in_home(paths.home).add("two", _repo(tmp_path / "two"))

        changes = sup.reconcile()

        assert changes["started"] == ["two"]
        assert sup.status()["workers"] == 2
    finally:
        sup.stop()


def test_a_repository_removed_while_running_has_its_worker_stopped(paths, cfg, tmp_path):
    registry = Registry.in_home(paths.home)
    registry.add("one", _repo(tmp_path / "one"))
    registry.add("two", _repo(tmp_path / "two"))
    sup = _supervisor(paths, cfg, registry)
    sup.reconcile()
    try:
        Registry.in_home(paths.home).remove("two")

        changes = sup.reconcile()

        assert changes["stopped"] == ["two"]
        assert sup.status()["workers"] == 1
        retired = sup.status()["retired"]
        assert [r["name"] for r in retired] == ["two"]
        assert retired[0]["status"] == "unregistered"
    finally:
        sup.stop()


def test_reconciling_twice_with_no_change_starts_nothing(paths, cfg, tmp_path):
    registry = Registry.in_home(paths.home)
    registry.add("one", _repo(tmp_path / "one"))
    sup = _supervisor(paths, cfg, registry)
    sup.reconcile()
    try:
        changes = sup.reconcile()

        assert changes == {"started": [], "stopped": [], "moved": []}
    finally:
        sup.stop()


def test_a_name_repointed_at_another_path_replaces_its_worker(paths, cfg, tmp_path):
    """Otherwise the old path keeps being watched under the new name."""
    registry = Registry.in_home(paths.home)
    registry.add("one", _repo(tmp_path / "one"))
    sup = _supervisor(paths, cfg, registry)
    sup.reconcile()
    try:
        moved_to = _repo(tmp_path / "elsewhere")
        live = Registry.in_home(paths.home)
        live.remove("one")
        live.add("one", moved_to)

        changes = sup.reconcile()

        assert changes["moved"] == ["one"]
        assert sup.status()["repos"][0]["path"] == str(moved_to.resolve())
    finally:
        sup.stop()


# -- 6. the health check -------------------------------------------------------


def test_a_dead_worker_is_restarted(paths, cfg, tmp_path):
    registry = Registry.in_home(paths.home)
    registry.add("one", _repo(tmp_path / "one"))
    sup = _supervisor(paths, cfg, registry)
    sup.reconcile()
    try:
        # Kill the worker the way a crash would: stop its loop without telling
        # the supervisor.
        sup._workers["one"].stop()
        assert sup._workers["one"].is_alive() is False

        health = sup.check_health()

        assert health["restarted"] == ["one"]
        assert sup._workers["one"].is_alive() is True
        assert sup.status()["repos"][0]["restarts"] == 1
    finally:
        sup.stop()


def test_a_restart_carries_the_failure_counters_forward(paths, cfg, tmp_path):
    """A restart that reset the counters would hide a repository that keeps dying."""
    registry = Registry.in_home(paths.home)
    registry.add("one", _repo(tmp_path / "one"))
    sup = _supervisor(paths, cfg, registry)
    sup.reconcile()
    try:
        worker = sup._workers["one"]
        worker.stop()
        worker.health.update({"errors": 3, "last_error": "boom", "scans": 9})

        sup.check_health()

        after = sup.status()["repos"][0]
        assert after["errors"] == 3
        assert after["last_error"] == "boom"
        assert after["restarts"] == 1
    finally:
        sup.stop()


def test_a_live_worker_is_not_restarted(paths, cfg, tmp_path):
    registry = Registry.in_home(paths.home)
    registry.add("one", _repo(tmp_path / "one"))
    sup = _supervisor(paths, cfg, registry)
    sup.reconcile()
    try:
        assert sup.check_health()["restarted"] == []
        assert sup.status()["repos"][0]["restarts"] == 0
    finally:
        sup.stop()


# -- 4. per-repository log files -----------------------------------------------


def test_each_repository_gets_its_own_log_file(paths, cfg, tmp_path):
    registry = Registry.in_home(paths.home)
    registry.add("one", _repo(tmp_path / "one"))
    registry.add("two", _repo(tmp_path / "two"))
    sup = _supervisor(paths, cfg, registry)
    sup.reconcile()
    try:
        for _ in range(100):
            if {"one.log", "two.log"} <= set(list_service_logs(paths)):
                break
            time.sleep(0.02)

        assert {"one.log", "two.log"} <= set(list_service_logs(paths))
        assert "worker started" in read_service_log(paths, "one")["lines"][0]
    finally:
        sup.stop()


def test_a_re_ingest_is_recorded_in_that_repository_s_log(paths, cfg, tmp_path):
    worker = RepoWorker(
        "one",
        str(_repo(tmp_path / "one")),
        str(cfg.db_path),
        paths,
        reingest_fn=lambda db, path, audit: {"mode": "stub", "parsed_files": 1},
    )

    worker.poll_once()

    lines = read_service_log(paths, "one")["lines"]
    assert any("re-ingested" in line for line in lines)


def test_a_failing_re_ingest_is_recorded_rather_than_swallowed(paths, cfg, tmp_path):
    def explode(db, path, audit):
        raise RuntimeError("the parser fell over")

    worker = RepoWorker(
        "one", str(_repo(tmp_path / "one")), str(cfg.db_path), paths, reingest_fn=explode
    )

    result = worker.poll_once()

    assert "the parser fell over" in result["error"]
    assert worker.snapshot()["status"] == "error"
    assert any("re-ingest failed" in line for line in read_service_log(paths, "one")["lines"])


def test_a_repository_name_cannot_choose_where_its_log_is_written(paths):
    """A registry name reaches the filesystem here."""
    hostile = paths.log_file("../../etc/passwd")

    assert hostile.parent == paths.log_dir
    assert ".." not in hostile.name


def test_a_log_file_is_capped_so_a_failing_repository_cannot_fill_the_disk(paths, cfg, tmp_path):
    worker = RepoWorker(
        "one", str(_repo(tmp_path / "one")), str(cfg.db_path), paths,
        reingest_fn=lambda db, path, audit: {"mode": "stub"},
    )
    path = paths.log_file("one")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x" * (LOG_MAX_BYTES + 1000), encoding="utf-8")

    worker.log("after the cap")

    assert path.stat().st_size < LOG_MAX_BYTES
    assert "log truncated" in path.read_text(encoding="utf-8")


# -- status --------------------------------------------------------------------


def test_the_status_file_is_written_atomically_and_reads_back(paths, cfg, tmp_path):
    registry = Registry.in_home(paths.home)
    registry.add("one", _repo(tmp_path / "one"))
    sup = _supervisor(paths, cfg, registry)
    try:
        sup.cycle()

        payload = json.loads(paths.status_file.read_text(encoding="utf-8"))
        assert payload["workers"] == 1
        assert payload["repos"][0]["name"] == "one"
        assert not list(paths.home.glob("*.tmp")), "a temporary file was left behind"
    finally:
        sup.stop()


def test_status_reports_not_running_when_there_is_no_service(paths):
    status = service_status(paths)

    assert status["running"] is False
    assert status["pid"] is None
    assert status["last_status"] is None


def test_status_explains_an_identity_file_whose_process_is_gone(paths):
    paths.home.mkdir(parents=True, exist_ok=True)
    dead = _a_pid_that_is_not_running()
    paths.pid_file.write_text(json.dumps({"pid": dead, "started_at": "old"}), encoding="utf-8")

    status = service_status(paths)

    assert status["running"] is False
    assert "reclaims" in status["note"]


# -- 7. stopping cleanly --------------------------------------------------------


def test_stopping_leaves_no_worker_thread_behind(paths, cfg, tmp_path):
    registry = Registry.in_home(paths.home)
    registry.add("one", _repo(tmp_path / "one"))
    registry.add("two", _repo(tmp_path / "two"))
    before = threading.active_count()
    sup = _supervisor(paths, cfg, registry)
    sup.reconcile()

    sup.stop()

    for _ in range(100):
        if threading.active_count() == before:
            break
        time.sleep(0.02)
    assert threading.active_count() == before, "a worker thread outlived the supervisor"


def test_a_bounded_run_terminates_by_itself(paths, cfg, tmp_path):
    registry = Registry.in_home(paths.home)
    registry.add("one", _repo(tmp_path / "one"))
    sup = _supervisor(paths, cfg, registry)

    started = time.monotonic()
    sup.run(max_seconds=0.2, cycle_interval=0.05)
    elapsed = time.monotonic() - started

    assert elapsed < 10, "a bounded run must terminate"
    assert all(not w.is_alive() for w in sup._workers.values())


def test_stopping_a_service_that_is_not_running_says_so(paths):
    result = stop_service(paths)

    assert result["stopped"] is False
    assert "nothing to stop" in result["reason"]


def test_stopping_a_service_whose_process_is_gone_clears_the_file(paths):
    paths.home.mkdir(parents=True, exist_ok=True)
    dead = _a_pid_that_is_not_running()
    paths.pid_file.write_text(json.dumps({"pid": dead}), encoding="utf-8")

    result = stop_service(paths)

    assert result["stopped"] is False
    assert "already gone" in result["reason"]
    assert not paths.pid_file.exists()


# -- 1. the subcommands, across a real process boundary -------------------------


def _cli(home: Path, *args: str, timeout: float = 60.0) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env["DKG_HOME"] = str(home)
    env["DKG_ALLOW_OUTBOUND"] = "0"
    return subprocess.run(
        [sys.executable, "-m", "dkg", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(ROOT),
        timeout=timeout,
    )


@pytest.mark.slow
def test_the_service_starts_detached_reports_status_and_stops(tmp_path):
    """The one fact only a real process can establish: it outlives its parent.

    Bounded twice over: the spawned service carries --max-seconds so it exits on
    its own even if this test fails, and every CLI call has a timeout.
    """
    home = tmp_path / "home"
    repo = _repo(tmp_path / "proj")

    assert _cli(home, "init").returncode == 0
    assert _cli(home, "registry", "add", "proj", str(repo)).returncode == 0

    started = _cli(home, "service", "start", "--interval", "0.2", "--max-seconds", "30")
    assert started.returncode == 0, started.stderr
    pid = json.loads(started.stdout)["pid"]

    try:
        assert process_alive(pid), "the service did not survive the command that started it"

        for _ in range(200):
            status = json.loads(_cli(home, "service", "status").stdout)
            if status["running"] and status.get("last_status", {}).get("workers"):
                break
            time.sleep(0.05)

        assert status["running"] is True
        assert status["pid"] == pid
        assert status["last_status"]["workers"] == 1
        assert status["last_status"]["repos"][0]["name"] == "proj"

        # A second start must be refused while the first is alive.
        second = _cli(home, "service", "start")
        assert second.returncode != 0
        assert "already running" in second.stderr

        log = _cli(home, "service", "log", "proj", "--lines", "5")
        assert log.returncode == 0
        assert "worker started" in log.stdout

        stopped = json.loads(_cli(home, "service", "stop").stdout)
        assert stopped["stopped"] is True

        assert json.loads(_cli(home, "service", "status").stdout)["running"] is False
    finally:
        if process_alive(pid):
            _cli(home, "service", "stop")
            for _ in range(100):
                if not process_alive(pid):
                    break
                time.sleep(0.05)

    assert not process_alive(pid), "the service process was orphaned"


@pytest.mark.slow
def test_restart_replaces_the_running_service_with_a_new_process(tmp_path):
    home = tmp_path / "home"
    repo = _repo(tmp_path / "proj")
    assert _cli(home, "init").returncode == 0
    assert _cli(home, "registry", "add", "proj", str(repo)).returncode == 0

    first = json.loads(_cli(home, "service", "start", "--max-seconds", "30").stdout)["pid"]
    try:
        second = json.loads(
            _cli(home, "service", "restart", "--max-seconds", "30").stdout
        )["pid"]

        assert second != first
        assert process_alive(second)
        for _ in range(100):
            if not process_alive(first):
                break
            time.sleep(0.05)
        assert not process_alive(first), "restart left the old process running"
    finally:
        _cli(home, "service", "stop")
        for pid in (first, second if "second" in dir() else first):
            for _ in range(100):
                if not process_alive(pid):
                    break
                time.sleep(0.05)

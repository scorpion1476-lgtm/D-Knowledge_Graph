"""R-24: the watcher as a managed background service.

``WatchDaemon`` runs in the foreground of whoever started it, on a single worker
thread that walks every registered repository in turn. That is fine for a
bounded run and useless as a service: closing the terminal kills it, a second
invocation double-runs it, a repository that starts failing is retried forever
with nowhere to read why, and adding a repository to the registry does nothing
until someone restarts the whole thing.

This module is the supervision layer over it. What each piece is actually for:

* **One worker per repository.** A shared loop means one slow or wedged
  repository delays every other one. A worker per repository also gives
  failure somewhere to be contained.
* **A process-identity file.** Two daemons re-ingesting the same database is a
  write-contention bug that presents as random failures much later. The file is
  created with ``O_EXCL`` so the check and the claim are one atomic step, and a
  file whose process is gone is reclaimed rather than blocking forever.
* **Per-repository log files.** "It stopped working" needs an answer that
  outlives the process.
* **Reconciliation.** The registry is edited while the service runs. Each cycle
  the supervisor diffs the registry against its workers and starts or stops the
  difference, so ``dkg registry add`` takes effect without a restart.
* **Health checking.** A worker thread that dies takes its repository's watching
  with it, silently. The supervisor notices a worker that is no longer alive and
  starts a replacement, counting the restarts so a repository that keeps dying
  is visible rather than merely quiet.

Everything is local: no network, no third-party dependency, standard library
only. The supervisor is drivable step by step (``reconcile``, ``check_health``,
``poll_once``) so its behaviour is testable in-process without forking.
"""

from __future__ import annotations

import errno
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.errors import DKGError, ValidationError
from .daemon import _default_reingest, _repo_signature, watchfiles_available
from .registry import Registry

PID_FILENAME = "watch-service.pid"
STATUS_FILENAME = "watch-service-status.json"
LOG_DIRNAME = "watch-logs"

#: How long `stop` waits for a signalled service to exit before reporting that
#: it did not. Deliberately bounded: a stop command that can hang forever is not
#: a stop command.
STOP_TIMEOUT_SECONDS = 10.0

#: Cap on a per-repository log file. Left uncapped, a repository failing in a
#: loop fills the disk, which is a worse outcome than losing old log lines.
LOG_MAX_BYTES = 1_048_576


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ServicePaths:
    """Where the service keeps its identity, its status, and its logs."""

    home: Path

    @property
    def pid_file(self) -> Path:
        return self.home / PID_FILENAME

    @property
    def status_file(self) -> Path:
        return self.home / STATUS_FILENAME

    @property
    def log_dir(self) -> Path:
        return self.home / LOG_DIRNAME

    def log_file(self, name: str) -> Path:
        return self.log_dir / f"{_safe_name(name)}.log"


def _safe_name(name: str) -> str:
    """A registry name reaches the filesystem here, so it is constrained.

    A repository called ``../../etc/passwd`` must not choose where its log is
    written. Anything outside the safe set becomes an underscore.
    """
    cleaned = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in str(name))
    # Collapse dot runs as well as stripping the ends: "../../etc" survives a
    # strip as "_.._etc", which is still a name containing "..", and a name
    # that reaches the filesystem should not contain one at all.
    cleaned = re.sub(r"\.{2,}", "_", cleaned).strip("._") or "repo"
    return cleaned[:100]


def process_alive(pid: int) -> bool:
    """Whether a process id is live, without assuming we may signal it."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # It exists and belongs to somebody else. Existing is what was asked.
        return True
    except OSError as exc:  # pragma: no cover - platform oddity
        return exc.errno != errno.ESRCH
    return True


# -- the process-identity file ------------------------------------------------


def read_pid_file(paths: ServicePaths) -> dict | None:
    """The recorded identity, or None if there is none or it is unreadable."""
    try:
        raw = paths.pid_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        return None
    try:
        record = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(record, dict) or "pid" not in record:
        return None
    try:
        record["pid"] = int(record["pid"])
    except (TypeError, ValueError):
        return None
    return record


def claim_pid_file(paths: ServicePaths, pid: int) -> bool:
    """Atomically claim the identity file. False if somebody live already holds it.

    ``O_EXCL`` makes the check and the claim one step. Checking existence and
    then writing would leave a window in which two starts both see no file.
    """
    paths.home.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"pid": int(pid), "started_at": _now()}, indent=2)
    try:
        fd = os.open(paths.pid_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        existing = read_pid_file(paths)
        if existing and process_alive(existing["pid"]):
            return False
        # The holder is gone. Reclaim rather than refusing forever: a machine
        # that lost power must not need a manual file deletion to start again.
        try:
            paths.pid_file.unlink()
        except FileNotFoundError:
            pass
        return claim_pid_file(paths, pid)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(payload)
    return True


def release_pid_file(paths: ServicePaths, pid: int | None = None) -> None:
    """Remove the identity file, but only if it is still ours."""
    record = read_pid_file(paths)
    if record is None:
        return
    if pid is not None and record["pid"] != pid:
        return
    try:
        paths.pid_file.unlink()
    except FileNotFoundError:
        pass


# -- one worker per repository ------------------------------------------------


class RepoWorker(threading.Thread):
    """Watches exactly one repository and logs what it did to its own file."""

    def __init__(
        self,
        name: str,
        repo_path: str,
        db_path: str,
        paths: ServicePaths,
        *,
        audit_path=None,
        reingest_fn=None,
        poll_interval: float = 1.0,
        languages=None,
    ) -> None:
        super().__init__(name=f"dkg-watch-{_safe_name(name)}", daemon=True)
        self.repo_name = name
        self.repo_path = repo_path
        self.db_path = db_path
        self.paths = paths
        self.audit_path = audit_path
        self.reingest_fn = reingest_fn or _default_reingest(languages)
        self.poll_interval = max(0.02, float(poll_interval))
        # NOT `_stop`: threading.Thread already uses that name for an internal
        # method, and shadowing it with an Event breaks is_alive() and join()
        # in a way that presents as "the worker is dead" long after the fact.
        self._stop_event = threading.Event()
        self._signature: str | None = None
        self._lock = threading.Lock()
        # Annotated rather than inferred: the counters are incremented and the
        # other fields hold strings and dicts, so an inferred `dict[str, object]`
        # makes every `+= 1` below a type error.
        self.health: dict[str, Any] = {
            "name": name,
            "path": repo_path,
            "scans": 0,
            "reingests": 0,
            "errors": 0,
            "restarts": 0,
            "last_scan": None,
            "last_result": None,
            "last_error": None,
            "status": "pending",
        }

    # -- logging ------------------------------------------------------------

    def log(self, message: str) -> None:
        """Append one line to this repository's log, capped in size."""
        path = self.paths.log_file(self.repo_name)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
                # Keep the tail: the recent lines are the ones being read.
                tail = path.read_text(encoding="utf-8", errors="replace")[-LOG_MAX_BYTES // 2 :]
                path.write_text(
                    f"[{_now()}] log truncated to its most recent lines\n{tail}",
                    encoding="utf-8",
                )
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"[{_now()}] {message}\n")
        except OSError:
            # A log that cannot be written must never take the watcher down.
            return

    # -- one pass -----------------------------------------------------------

    def poll_once(self) -> dict:
        from ..core.db import open_database
        from ..core.version import record_open

        with self._lock:
            self.health["scans"] += 1
            self.health["last_scan"] = _now()

        try:
            signature = _repo_signature(self.repo_path)
        except OSError as exc:
            with self._lock:
                self.health["errors"] += 1
                self.health["last_error"] = str(exc)
                self.health["status"] = "error"
            self.log(f"scan failed: {exc}")
            return {"changed": False, "error": str(exc)}

        if self._signature == signature:
            return {"changed": False}

        try:
            with open_database(self.db_path) as db:
                record_open(db)
                result = self.reingest_fn(db, self.repo_path, self.audit_path)
            summary = {k: result.get(k) for k in ("mode", "parsed_files", "nodes", "edges")}
            # Advance only on success, so a transient failure is retried rather
            # than latched and left stale.
            self._signature = signature
            with self._lock:
                self.health["reingests"] += 1
                self.health["last_result"] = summary
                self.health["status"] = "ok"
            self.log(f"re-ingested: {json.dumps(summary, sort_keys=True)}")
            return {"changed": True, "result": summary}
        except Exception as exc:  # honest degradation: record and keep watching
            with self._lock:
                self.health["errors"] += 1
                self.health["last_error"] = str(exc)
                self.health["status"] = "error"
            self.log(f"re-ingest failed: {exc}")
            return {"changed": True, "error": str(exc)}

    # -- lifecycle ----------------------------------------------------------

    def run(self) -> None:
        self.log(f"worker started for {self.repo_path}")
        while not self._stop_event.is_set():
            self.poll_once()
            if self._stop_event.wait(self.poll_interval):
                break
        self.log("worker stopped")

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self.is_alive():
            self.join(timeout)

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self.health)


# -- the supervisor -----------------------------------------------------------


class Supervisor:
    """Runs one worker per registered repository and keeps that set correct."""

    def __init__(
        self,
        db_path: str | Path,
        registry: Registry,
        paths: ServicePaths,
        *,
        audit_path=None,
        reingest_fn=None,
        poll_interval: float = 1.0,
        languages=None,
        worker_factory=None,
    ) -> None:
        self.db_path = str(db_path)
        self.registry = registry
        self.paths = paths
        self.audit_path = audit_path
        self.reingest_fn = reingest_fn
        self.poll_interval = max(0.02, float(poll_interval))
        self.languages = languages
        self._workers: dict[str, RepoWorker] = {}
        # Health of repositories no longer registered is kept out of the live
        # worker map but remembered, so removing a repository does not erase the
        # record of why it was failing.
        self._retired: dict[str, dict] = {}
        self._restarts: dict[str, int] = {}
        self._stop = threading.Event()
        self._worker_factory = worker_factory or self._build_worker

    def _build_worker(self, name: str, path: str) -> RepoWorker:
        return RepoWorker(
            name,
            path,
            self.db_path,
            self.paths,
            audit_path=self.audit_path,
            reingest_fn=self.reingest_fn,
            poll_interval=self.poll_interval,
            languages=self.languages,
        )

    # -- reconciliation ------------------------------------------------------

    def reconcile(self) -> dict:
        """Make the running worker set match the registry, and report the diff."""
        self.registry._load()  # pick up edits made while the service runs
        wanted = {e.name: e.path for e in self.registry.list()}
        started, stopped, moved = [], [], []

        for name in sorted(set(self._workers) - set(wanted)):
            worker = self._workers.pop(name)
            worker.stop()
            self._retired[name] = worker.snapshot() | {"status": "unregistered"}
            stopped.append(name)

        for name, path in sorted(wanted.items()):
            existing = self._workers.get(name)
            if existing is not None and existing.repo_path != path:
                # The same name now points somewhere else. Replace it rather
                # than watching the old path under the new name.
                existing.stop()
                self._workers.pop(name, None)
                existing = None
                moved.append(name)
            if existing is None:
                worker = self._worker_factory(name, path)
                worker.health["restarts"] = self._restarts.get(name, 0)
                self._workers[name] = worker
                if not self._stop.is_set():
                    worker.start()
                started.append(name)

        return {"started": started, "stopped": stopped, "moved": moved}

    # -- health --------------------------------------------------------------

    def check_health(self) -> dict:
        """Replace any worker whose thread has died. Report what was restarted."""
        restarted = []
        for name in sorted(self._workers):
            worker = self._workers[name]
            if worker.is_alive() or self._stop.is_set():
                continue
            count = self._restarts.get(name, 0) + 1
            self._restarts[name] = count
            replacement = self._worker_factory(name, worker.repo_path)
            # Carry the failed worker's counters forward: a restart that reset
            # them would hide a repository that keeps dying.
            previous = worker.snapshot()
            replacement.health.update(
                {
                    "scans": previous["scans"],
                    "reingests": previous["reingests"],
                    "errors": previous["errors"],
                    "last_error": previous["last_error"],
                    "restarts": count,
                }
            )
            replacement.log(f"restarted after the previous worker stopped (restart {count})")
            self._workers[name] = replacement
            replacement.start()
            restarted.append(name)
        return {"restarted": restarted}

    # -- status --------------------------------------------------------------

    def status(self) -> dict:
        repos = [self._workers[name].snapshot() for name in sorted(self._workers)]
        for repo in repos:
            repo["alive"] = self._workers[repo["name"]].is_alive()
        retired = [dict(v) for _, v in sorted(self._retired.items())]
        return {
            "pid": os.getpid(),
            "backend": "watchfiles" if watchfiles_available() else "polling",
            "workers": len(self._workers),
            "repos": repos,
            "retired": retired,
            "healthy": all(r["status"] in ("ok", "pending") and r["alive"] for r in repos),
            "checked_at": _now(),
        }

    def write_status(self) -> dict:
        payload = self.status()
        self.paths.home.mkdir(parents=True, exist_ok=True)
        temp = self.paths.status_file.with_suffix(".json.tmp")
        temp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        # Atomic replace, so a reader never sees a half-written status file.
        temp.replace(self.paths.status_file)
        return payload

    # -- running -------------------------------------------------------------

    def cycle(self) -> dict:
        """One supervision cycle: reconcile, health-check, publish status."""
        changes = self.reconcile()
        health = self.check_health()
        status = self.write_status()
        return {"reconcile": changes, "health": health, "status": status}

    def run(self, *, max_seconds: float | None = None, cycle_interval: float = 1.0) -> None:
        """Supervise until stopped or until the bound elapses. Always terminates."""
        deadline = None if max_seconds is None else time.monotonic() + max_seconds
        try:
            while not self._stop.is_set():
                self.cycle()
                if deadline is not None and time.monotonic() >= deadline:
                    break
                if self._stop.wait(cycle_interval):
                    break
        finally:
            self.stop()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        for name in sorted(self._workers):
            self._workers[name].stop(timeout)
        try:
            self.write_status()
        except OSError:
            pass


# -- the managed lifecycle ----------------------------------------------------


def service_status(paths: ServicePaths) -> dict:
    """What the service is doing, readable without being the service."""
    record = read_pid_file(paths)
    running = bool(record and process_alive(record["pid"]))
    payload: dict = {
        "running": running,
        "pid": record["pid"] if record else None,
        "started_at": record.get("started_at") if record else None,
        "pid_file": str(paths.pid_file),
    }
    if record and not running:
        payload["note"] = (
            "a process-identity file exists but its process is gone; the next "
            "start reclaims it"
        )
    try:
        payload["last_status"] = json.loads(paths.status_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload["last_status"] = None
    return payload


def start_service(
    paths: ServicePaths,
    *,
    home: str | Path,
    interval: float = 1.0,
    max_seconds: float | None = None,
    languages: str | None = None,
    python: str | None = None,
) -> dict:
    """Spawn the supervisor in its own session and record its identity.

    ``start_new_session`` detaches it from the invoking terminal, so closing the
    terminal does not take the service with it. The identity file is claimed by
    the CHILD rather than here, because a parent that claimed it would leave a
    file behind if the child failed to start at all.
    """
    existing = read_pid_file(paths)
    if existing and process_alive(existing["pid"]):
        raise DKGError(
            f"the watch service is already running (pid {existing['pid']}). "
            "Use 'dkg service status', or 'dkg service restart' to replace it."
        )

    paths.log_dir.mkdir(parents=True, exist_ok=True)
    supervisor_log = paths.log_dir / "service.log"
    argv = [
        python or sys.executable,
        "-m",
        "dkg",
        "--home",
        str(home),
        "service",
        "run",
        "--interval",
        str(interval),
    ]
    if max_seconds is not None:
        argv += ["--max-seconds", str(max_seconds)]
    if languages:
        argv += ["--languages", str(languages)]

    with supervisor_log.open("a", encoding="utf-8") as handle:
        handle.write(f"[{_now()}] starting: {' '.join(argv)}\n")
        handle.flush()
        process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            argv,
            stdout=handle,
            stderr=handle,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            cwd=str(Path.cwd()),
        )

    # Wait briefly for the child to claim the identity file, so `start` can
    # report a real outcome rather than an optimistic one.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        record = read_pid_file(paths)
        if record and process_alive(record["pid"]):
            return {"started": True, "pid": record["pid"], "log": str(supervisor_log)}
        if process.poll() is not None:
            raise DKGError(
                f"the watch service exited immediately with code {process.returncode}; "
                f"see {supervisor_log}"
            )
        time.sleep(0.05)

    raise DKGError(
        f"the watch service did not record its identity within 5 seconds; see {supervisor_log}"
    )


def stop_service(paths: ServicePaths, *, timeout: float = STOP_TIMEOUT_SECONDS) -> dict:
    """Signal the service and wait, bounded, for it to go."""
    record = read_pid_file(paths)
    if record is None:
        return {"stopped": False, "reason": "no process-identity file; nothing to stop"}
    pid = record["pid"]
    if not process_alive(pid):
        release_pid_file(paths, pid)
        return {"stopped": False, "pid": pid, "reason": "the recorded process is already gone"}

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        release_pid_file(paths, pid)
        return {"stopped": False, "pid": pid, "reason": "the recorded process is already gone"}
    except PermissionError as exc:
        raise DKGError(f"not permitted to stop pid {pid}: {exc}") from exc

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_alive(pid):
            release_pid_file(paths, pid)
            return {"stopped": True, "pid": pid}
        time.sleep(0.05)

    # Reported rather than escalated to SIGKILL: a supervisor that will not stop
    # is a thing to look at, and killing it hides that.
    return {
        "stopped": False,
        "pid": pid,
        "reason": f"still running {timeout} seconds after SIGTERM",
    }


def read_service_log(paths: ServicePaths, name: str | None = None, *, lines: int = 200) -> dict:
    """The tail of one repository's log, or of the supervisor's own."""
    if name is None:
        path = paths.log_dir / "service.log"
    else:
        path = paths.log_file(name)
    if not path.is_file():
        raise ValidationError(f"no log file at {path}")
    text = path.read_text(encoding="utf-8", errors="replace")
    tail = text.splitlines()[-max(1, int(lines)) :]
    return {"log": str(path), "lines": tail}


def list_service_logs(paths: ServicePaths) -> list[str]:
    if not paths.log_dir.is_dir():
        return []
    return sorted(p.name for p in paths.log_dir.glob("*.log"))

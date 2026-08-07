"""Local watch daemon: re-ingest registered repositories incrementally on change.

Bounded and non-interactive. It watches every registered repository and, when a
repository's files change, re-ingests it through the code plane's git-incremental
path so only changed files are re-parsed. Per-repository health is tracked. The
daemon runs on a worker thread controlled by a threading.Event and stops cleanly
with no orphaned thread or process.

Two backends: the optional ``watch`` extra (watchfiles) is used for event-driven
watching when present; otherwise a standard-library polling watcher runs. All
work is local, no network.
"""

from __future__ import annotations

import hashlib
import os
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from ..core.db import open_database
from ..core.version import record_open
from .registry import Registry

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".dkg", ".venv"}

ReingestFn = Callable[[object, str, object], dict]


def watchfiles_available() -> bool:
    try:
        import watchfiles  # noqa: F401

        return True
    except ImportError:
        return False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _repo_signature(root: str | Path) -> str:
    """A deterministic content signature over a repo's files (path, size, mtime).

    The .git subtree and common build/cache directories are pruned so the
    signature reflects source changes and the walk stays cheap.
    """
    root = Path(root)
    entries: list[tuple[str, int, int]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            fp = Path(dirpath) / fn
            try:
                st = fp.stat()
            except OSError:
                continue
            entries.append((os.path.relpath(fp, root), st.st_size, st.st_mtime_ns))
    entries.sort()
    h = hashlib.sha256()
    for rel, size, mtime in entries:
        h.update(f"{rel}\0{size}\0{mtime}\0".encode())
    return h.hexdigest()


def _default_reingest(languages=None) -> ReingestFn:
    def reingest(db, repo_path, audit_path) -> dict:
        from ..code.ingest import ingest_repo

        return ingest_repo(db, repo_path, audit_path=audit_path, incremental=True, languages=languages)

    return reingest


class WatchDaemon:
    def __init__(
        self,
        db_path: str | Path,
        registry: Registry,
        *,
        audit_path: str | Path | None = None,
        reingest_fn: ReingestFn | None = None,
        poll_interval: float = 1.0,
        use_watchfiles: bool | None = None,
        languages=None,
    ) -> None:
        self.db_path = str(db_path)
        self.registry = registry
        self.audit_path = audit_path
        self.reingest_fn = reingest_fn or _default_reingest(languages)
        self.poll_interval = max(0.02, float(poll_interval))
        if use_watchfiles is None:
            use_watchfiles = watchfiles_available()
        self.use_watchfiles = bool(use_watchfiles) and watchfiles_available()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._health: dict[str, dict] = {}
        self._signatures: dict[str, str] = {}
        self._init_health()

    @property
    def backend(self) -> str:
        return "watchfiles" if self.use_watchfiles else "polling"

    def _init_health(self) -> None:
        for entry in self.registry.list():
            self._health.setdefault(
                entry.name,
                {
                    "name": entry.name,
                    "path": entry.path,
                    "scans": 0,
                    "reingests": 0,
                    "errors": 0,
                    "last_scan": None,
                    "last_result": None,
                    "last_error": None,
                    "status": "pending",
                },
            )

    # -- one pass -----------------------------------------------------------

    def poll_once(self) -> dict:
        """Scan every registered repo once and re-ingest any that changed."""
        results: dict[str, dict] = {}
        for entry in self.registry.list():
            name, path = entry.name, entry.path
            with self._lock:
                h = self._health.setdefault(
                    name,
                    {
                        "name": name, "path": path, "scans": 0, "reingests": 0,
                        "errors": 0, "last_scan": None, "last_result": None,
                        "last_error": None, "status": "pending",
                    },
                )
                h["scans"] += 1
                h["last_scan"] = _now()
            sig = _repo_signature(path)
            if self._signatures.get(name) == sig:
                results[name] = {"changed": False}
                continue
            try:
                with open_database(self.db_path) as db:
                    record_open(db)
                    res = self.reingest_fn(db, path, self.audit_path)
                summary = {k: res.get(k) for k in ("mode", "parsed_files", "nodes", "edges")}
                # Advance the signature only on success, so a transient failure
                # (git mid-operation, a momentary lock) is retried on the next
                # poll instead of being latched and left stale.
                self._signatures[name] = sig
                with self._lock:
                    h["reingests"] += 1
                    h["last_result"] = summary
                    h["status"] = "ok"
                results[name] = {"changed": True, "result": summary}
            except Exception as e:  # honest degradation: record and keep watching
                with self._lock:
                    h["errors"] += 1
                    h["last_error"] = str(e)
                    h["status"] = "error"
                results[name] = {"changed": True, "error": str(e)}
        return results

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        target = self._run_watchfiles if self.use_watchfiles else self._run_polling
        self._thread = threading.Thread(target=target, name="dkg-watch", daemon=True)
        self._thread.start()

    def _run_polling(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            if self._stop.wait(self.poll_interval):
                break

    def _run_watchfiles(self) -> None:
        import watchfiles

        # Ingest the current state once, then wake on each change batch.
        self.poll_once()
        paths = [e.path for e in self.registry.list()]
        if not paths:
            self._stop.wait()
            return
        try:
            for _changes in watchfiles.watch(*paths, stop_event=self._stop, rust_timeout=1000, yield_on_timeout=True):
                if self._stop.is_set():
                    break
                self.poll_once()
        except Exception:
            # If the native watcher fails for any reason, fall back to polling so
            # the daemon keeps working rather than dying silently.
            self._run_polling()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self.join(timeout)

    def join(self, timeout: float = 5.0) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def run(self, max_seconds: float | None = None) -> None:
        """Blocking bounded run for the CLI. Always terminates."""
        self.start()
        try:
            if max_seconds is None:
                while self.is_running():
                    self._stop.wait(0.2)
            else:
                self._stop.wait(max_seconds)
        finally:
            self.stop()

    # -- health -------------------------------------------------------------

    def health(self) -> dict:
        with self._lock:
            repos = [dict(v) for v in self._health.values()]
        healthy = all(r["status"] in ("ok", "pending") for r in repos)
        return {
            "backend": self.backend,
            "running": self.is_running(),
            "repos": repos,
            "healthy": healthy,
            "checked_at": _now(),
        }

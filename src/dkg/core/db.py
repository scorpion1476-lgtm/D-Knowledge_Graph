"""SQLite wrapper.

Enforces parameterised queries (rejects execute() calls with parameters that
include obviously interpolated fragments), sets safe pragmas, exposes a small
migration runner, and provides a connection context manager suitable for CLI
and long-running processes alike.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from importlib import resources
from pathlib import Path

from .errors import MigrationError, SchemaError, StorageError

_MIGRATION_NAME_RE = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")


class Database:
    """Thread-safe SQLite handle with parameter-only query enforcement.

    Only :py:meth:`execute` and :py:meth:`executemany` should be used from
    application code. Both require SQL that has been vetted by the caller and
    passes parameters via bind rather than string interpolation.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = self._open(self.path)

    @staticmethod
    def _open(path: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(path),
            isolation_level=None,  # explicit transactions
            check_same_thread=False,
            timeout=30.0,
        )
        conn.row_factory = sqlite3.Row
        # Safe pragmas
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA temp_store = MEMORY;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        return conn

    # -- transactions ---------------------------------------------------

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE;")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK;")
                raise
            else:
                self._conn.execute("COMMIT;")

    # -- guarded query API ---------------------------------------------

    _FORBIDDEN_PATTERNS = (
        # crude but effective: catches most string interpolation slips
        re.compile(r"'[^']*'\s*\+\s*", re.IGNORECASE),
        re.compile(r"\bexec\s*\(\s*['\"]", re.IGNORECASE),
    )

    def _validate_sql(self, sql: str, parameters: Sequence | dict) -> None:
        if not isinstance(sql, str) or not sql.strip():
            raise SchemaError("SQL statement must be a non-empty string")
        for pattern in self._FORBIDDEN_PATTERNS:
            if pattern.search(sql):
                raise SchemaError("SQL statement contains forbidden interpolation pattern")
        if parameters is None:
            return
        # sqlite3 accepts both tuple/list and dict; nothing else.
        if not isinstance(parameters, (tuple, list, dict)):
            raise SchemaError("parameters must be a tuple, list, or dict")

    def execute(
        self, sql: str, parameters: Sequence | dict | None = None
    ) -> sqlite3.Cursor:
        params: Sequence | dict = parameters if parameters is not None else ()
        self._validate_sql(sql, params)
        with self._lock:
            return self._conn.execute(sql, params)

    def executemany(
        self, sql: str, seq_of_parameters: Iterable[Sequence | dict]
    ) -> sqlite3.Cursor:
        self._validate_sql(sql, ())
        with self._lock:
            return self._conn.executemany(sql, seq_of_parameters)

    def fetchone(
        self, sql: str, parameters: Sequence | dict | None = None
    ) -> sqlite3.Row | None:
        # Hold the lock across execute + fetch so a concurrent thread
        # cannot reuse the connection while this thread is reading rows.
        params: Sequence | dict = parameters if parameters is not None else ()
        self._validate_sql(sql, params)
        with self._lock:
            cur = self._conn.execute(sql, params)
            return cur.fetchone()

    def fetchall(
        self, sql: str, parameters: Sequence | dict | None = None
    ) -> list[sqlite3.Row]:
        # Hold the lock across execute + fetch so a concurrent thread
        # cannot reuse the connection while this thread is reading rows.
        params: Sequence | dict = parameters if parameters is not None else ()
        self._validate_sql(sql, params)
        with self._lock:
            cur = self._conn.execute(sql, params)
            return cur.fetchall()

    # -- lifecycle ------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error as e:
                raise StorageError(f"failed to close database: {e}") from e

    def __enter__(self) -> Database:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


# -- migration runner ---------------------------------------------------


def _list_migration_files() -> list[tuple[str, str]]:
    """Return sorted list of (name, sql_text) tuples from packaged migrations."""
    pkg = resources.files("dkg.core.migrations")
    items: list[tuple[str, str]] = []
    for entry in pkg.iterdir():
        name = entry.name
        m = _MIGRATION_NAME_RE.match(name)
        if not m:
            continue
        items.append((name, entry.read_text(encoding="utf-8")))
    items.sort(key=lambda kv: kv[0])
    return items


def _ensure_migrations_table(db: Database) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            name TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL,
            sha256 TEXT NOT NULL
        );
        """
    )


def applied_migrations(db: Database) -> list[str]:
    _ensure_migrations_table(db)
    rows = db.fetchall("SELECT name FROM schema_migrations ORDER BY name;")
    return [r["name"] for r in rows]


def apply_migrations(db: Database) -> list[str]:
    """Apply any pending migrations. Returns the names that were applied.

    ``executescript`` auto-commits any pending transaction, so we run each
    migration script directly and then record it in a separate statement.
    """
    import hashlib
    from datetime import datetime, timezone

    _ensure_migrations_table(db)
    already = set(applied_migrations(db))
    applied: list[str] = []
    for name, sql in _list_migration_files():
        if name in already:
            continue
        digest = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        try:
            with db._lock:
                db._conn.executescript(sql)
                db._conn.execute(
                    "INSERT INTO schema_migrations(name, applied_at, sha256) VALUES (?,?,?);",
                    (name, datetime.now(timezone.utc).isoformat(), digest),
                )
        except sqlite3.Error as e:
            raise MigrationError(f"migration {name} failed: {e}") from e
        applied.append(name)
    return applied


def open_database(path: Path | str) -> Database:
    """Open a database and run any pending migrations."""
    db = Database(path)
    apply_migrations(db)
    return db

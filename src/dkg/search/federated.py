"""Search across every registered repository, with per-repository attribution.

Each registered repository keeps its own database, which is the design: one
graph per repository means an ingest of one cannot disturb another, and a
repository can be removed by deleting a directory. The cost is that a question
spanning several of them had no answer at all.

This is that answer. It opens each registered repository's database in turn, runs
the SAME bounded search the single-repository surface runs, tags every hit with
the repository it came from, and merges the results under one limit and one
token budget. Nothing about the per-repository search differs, which is what
makes the numbers comparable across repositories.

READ-ONLY, and more carefully than usual, because there are TWO ways an
ordinary open writes to somebody else's repository. It applies pending
migrations, which is the obvious one. Less obviously, it issues
``PRAGMA journal_mode = WAL``, and on a database not already in WAL mode that
rewrites the file header and leaves ``-wal`` and ``-shm`` sidecars behind. Both
would be writes performed as a side effect of searching, so this opens through
SQLite's read-only URI with neither the migration runner nor any journalling
pragma, and a database that cannot be read that way is REPORTED rather than
opened anyway.

A repository that does not exist, has no graph, or cannot be read is reported
with the reason and skipped; it is never silently dropped, because a search that
quietly covered three of five repositories is worse than one that covered none.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ..core.db import Database

# Bounds. A registry with a thousand repositories must not turn one search into
# a thousand database opens.
MAX_REPOS = 50
DEFAULT_PER_REPO_LIMIT = 10
MAX_LIMIT = 200

# Where a registered repository's own graph lives, relative to its root.
REPO_DB_RELATIVE = (".dkg", "graph.sqlite")


def repo_database_path(repo_path: str | Path) -> Path:
    return Path(repo_path).joinpath(*REPO_DB_RELATIVE)


class ReadOnlyDatabase(Database):
    """A ``Database`` that cannot write, and does not write in order to open.

    Not running the migration runner is necessary but NOT sufficient. The
    ordinary open path issues ``PRAGMA journal_mode = WAL``, and on a database
    that is not already in WAL mode that pragma is a PERSISTENT WRITE: it
    rewrites the file header and creates ``-wal`` and ``-shm`` sidecars that
    outlive the call. Searching somebody else's repository must not convert its
    journal mode, so this opens with SQLite's read-only URI, sets no journalling
    pragma at all, and asks the connection itself to refuse writes.

    ``query_only`` is a connection setting rather than a file change, so it
    costs nothing and makes an accidental write inside this process fail loudly
    instead of succeeding quietly.
    """

    def __init__(self, path: Path) -> None:  # noqa: D107 - see class docstring
        import threading

        self.path = Path(path)
        self._lock = threading.RLock()
        self._conn = self._open_read_only_connection(self.path)

    @staticmethod
    def _open_read_only_connection(path: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(
            f"file:{path}?mode=ro",
            uri=True,
            isolation_level=None,
            check_same_thread=False,
            timeout=30.0,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        return conn


def _open_read_only(path: Path) -> Database | None:
    """Open an existing database read-only, or None when it is absent.

    Raises ``sqlite3.Error`` when the file exists but cannot be opened read-only,
    which the caller reports as a per-repository status rather than letting it
    take down the whole search.
    """
    if not path.is_file():
        return None
    return ReadOnlyDatabase(path)


def search_registered(
    query: str,
    *,
    home: str | Path | None = None,
    registry=None,
    limit: int = 20,
    per_repo_limit: int = DEFAULT_PER_REPO_LIMIT,
    token_budget: int | None = None,
    max_repos: int = MAX_REPOS,
) -> dict:
    """Run the same keyword search over every registered repository.

    Returns merged results carrying their repository of origin, plus a
    per-repository status list so a repository that could not be searched is
    visible rather than absent.
    """
    from ..search.keyword import keyword_search

    if registry is None:
        from ..core.config import load_config
        from ..watch.registry import Registry

        cfg = load_config()
        registry = Registry.in_home(home or cfg.home)

    limit = max(1, min(int(limit), MAX_LIMIT))
    per_repo_limit = max(1, min(int(per_repo_limit), MAX_LIMIT))
    entries = registry.list()
    considered = sorted(entries, key=lambda e: e.name)[: max(1, int(max_repos))]

    results: list[dict] = []
    statuses: list[dict] = []
    for entry in considered:
        root = Path(entry.path)
        db_path = repo_database_path(root)
        if not root.exists():
            statuses.append(
                {"repository": entry.name, "searched": False, "reason": "path does not exist"}
            )
            continue
        # Opening is inside the guard too. A repository whose file cannot be
        # opened read-only (permissions, a WAL database with no readable shared
        # -shm) must be reported like any other unsearchable one; letting the
        # error escape would take down the repositories that would have worked.
        db = None
        try:
            db = _open_read_only(db_path)
            if db is None:
                statuses.append(
                    {
                        "repository": entry.name,
                        "searched": False,
                        "reason": f"no graph at {Path(*REPO_DB_RELATIVE)}; ingest it first",
                    }
                )
                continue
            hits = keyword_search(db, query, limit=per_repo_limit)
        except (sqlite3.Error, OSError) as e:
            # A schema too old to query is reported, not migrated. Upgrading
            # another repository as a side effect of searching it is a write.
            statuses.append(
                {
                    "repository": entry.name,
                    "searched": False,
                    "reason": f"could not be read without writing to it: {e}",
                }
            )
            continue
        finally:
            if db is not None:
                db.close()
        for hit in hits:
            enriched = dict(hit)
            enriched["repository"] = entry.name
            enriched["repository_path"] = str(root)
            results.append(enriched)
        statuses.append({"repository": entry.name, "searched": True, "hits": len(hits)})

    # Deterministic merge: score descending, then repository, then chunk id, so
    # two repositories returning equal scores never reorder between runs.
    results.sort(
        key=lambda r: (-float(r.get("score", 0.0)), str(r.get("repository", "")), str(r.get("chunk_id", "")))
    )
    truncated = len(results) > limit
    payload = {
        "query": query,
        "results": results[:limit],
        "total_hits": len(results),
        "returned": min(len(results), limit),
        "truncated": truncated,
        "repositories": statuses,
        "repositories_registered": len(entries),
        "repositories_considered": len(considered),
        "repositories_searched": sum(1 for s in statuses if s["searched"]),
        "limit": limit,
        "per_repo_limit": per_repo_limit,
        "why": {
            "read_only": (
                "each database is opened through SQLite's read-only URI with no "
                "journalling pragma and no migration runner, so searching a "
                "repository neither upgrades its schema nor converts its journal "
                "mode. A database that cannot be read without writing to it is "
                "reported rather than opened anyway."
            ),
            "attribution": (
                "every result carries the repository it came from, because a "
                "merged list without attribution cannot be acted on"
            ),
            "skipped": (
                "a repository that does not exist, has no graph, or cannot be "
                "read is reported with its reason and never silently dropped: a "
                "search that quietly covered some of the registry is worse than "
                "one that covered none"
            ),
            "bounds": (
                f"at most {max_repos} repositories, {per_repo_limit} hits from "
                f"each, and {limit} in the merged result, with the same token "
                "budget the single-repository search honours"
            ),
        },
    }
    if token_budget:
        from ..context.pack import apply_budget

        return apply_budget(payload, budget=token_budget)
    return payload

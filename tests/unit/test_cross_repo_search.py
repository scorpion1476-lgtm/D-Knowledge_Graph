"""F-16: search across every registered repository, with attribution.

Synthetic in-house multi-repository fixtures throughout. Nothing third-party is
vendored to test this, so the suite carries no licence question of its own.
"""

from __future__ import annotations

import pytest

from dkg.core.db import open_database
from dkg.search.federated import repo_database_path, search_registered
from dkg.watch.registry import Registry


def _make_repo(root, name, texts):
    """A repository with its own graph database holding the given chunks."""
    repo = root / name
    repo.mkdir(parents=True, exist_ok=True)
    with open_database(repo_database_path(repo)) as db:
        with db.transaction():
            db.execute(
                "INSERT OR IGNORE INTO sources(source_id, tenant_id, kind, uri, display_name, added_at) "
                "VALUES (?,?,?,?,?,?);",
                (f"src-{name}", "local", "test", f"test://{name}", name, "2026-08-06T00:00:00Z"),
            )
            db.execute(
                "INSERT OR IGNORE INTO documents(document_id, source_id, tenant_id, format, "
                "content_sha256, byte_length, ingested_at, version) VALUES (?,?,?,?,?,?,?,?);",
                (f"doc-{name}", f"src-{name}", "local", "text", "0" * 64, 10, "2026-08-06T00:00:00Z", 1),
            )
            for i, text in enumerate(texts):
                db.execute(
                    "INSERT OR IGNORE INTO chunks(chunk_id, document_id, tenant_id, ord, text, "
                    "text_sha256, start_offset, end_offset) VALUES (?,?,?,?,?,?,?,?);",
                    (f"chunk-{name}-{i}", f"doc-{name}", "local", i, text, f"{i:064d}", 0, len(text)),
                )
    return repo


@pytest.fixture
def registry_with_two(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    _make_repo(tmp_path, "alpha", ["the widget factory builds widgets", "unrelated alpha text"])
    _make_repo(tmp_path, "beta", ["a different widget appears here", "unrelated beta text"])
    registry = Registry.in_home(home)
    registry.add("alpha", tmp_path / "alpha")
    registry.add("beta", tmp_path / "beta")
    return registry


def test_an_empty_registry_searches_nothing_and_says_so(tmp_path):
    registry = Registry.in_home(tmp_path)

    result = search_registered("widget", registry=registry)

    assert result["results"] == []
    assert result["repositories"] == []
    assert result["repositories_registered"] == 0


def test_results_come_from_every_registered_repository(registry_with_two):
    result = search_registered("widget", registry=registry_with_two)

    assert result["repositories_searched"] == 2
    origins = {r["repository"] for r in result["results"]}
    assert origins == {"alpha", "beta"}


def test_every_result_carries_its_repository_of_origin(registry_with_two):
    result = search_registered("widget", registry=registry_with_two)

    assert result["results"], "the fixture has matches in both repositories"
    for hit in result["results"]:
        assert hit["repository"] in {"alpha", "beta"}
        assert hit["repository_path"]
        # The per-repository hit is the same shape the single-repository search
        # returns, with attribution added rather than substituted.
        assert {"chunk_id", "document_id", "snippet"} <= set(hit)


def test_the_merged_limit_is_honoured(registry_with_two):
    unbounded = search_registered("widget", registry=registry_with_two, limit=200)
    assert unbounded["total_hits"] >= 2

    bounded = search_registered("widget", registry=registry_with_two, limit=1)

    assert len(bounded["results"]) == 1
    assert bounded["truncated"] is True
    assert bounded["total_hits"] == unbounded["total_hits"], "the total still reports the truth"


def test_the_per_repository_limit_is_honoured(registry_with_two):
    result = search_registered(
        "unrelated", registry=registry_with_two, per_repo_limit=1, limit=200
    )

    per_repo = {}
    for hit in result["results"]:
        per_repo[hit["repository"]] = per_repo.get(hit["repository"], 0) + 1
    assert all(count <= 1 for count in per_repo.values()), per_repo


def test_the_token_budget_is_honoured_like_the_single_repository_search(registry_with_two):
    """The same lever, applied to the same payload shape, with the same report."""
    unbounded = search_registered("widget", registry=registry_with_two, limit=200)

    bounded = search_registered(
        "widget", registry=registry_with_two, limit=200, token_budget=60
    )

    assert "token_budget" in bounded, bounded.keys()
    assert bounded["token_budget"]["trimmed_for_budget"] is True
    assert bounded["token_budget"]["budget"] == 60
    assert len(bounded["results"]) < len(unbounded["results"])
    # Totals are never rewritten, so a trimmed answer cannot pass as complete.
    assert bounded["total_hits"] == unbounded["total_hits"]


def test_a_budget_large_enough_to_fit_trims_nothing(registry_with_two):
    unbounded = search_registered("widget", registry=registry_with_two, limit=200)

    generous = search_registered(
        "widget", registry=registry_with_two, limit=200, token_budget=100000
    )

    assert generous["results"] == unbounded["results"]


def test_a_registered_repository_with_no_graph_is_reported_not_dropped(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    _make_repo(tmp_path, "has_graph", ["widget one"])
    (tmp_path / "no_graph").mkdir()
    registry = Registry.in_home(home)
    registry.add("has_graph", tmp_path / "has_graph")
    registry.add("no_graph", tmp_path / "no_graph")

    result = search_registered("widget", registry=registry)

    statuses = {s["repository"]: s for s in result["repositories"]}
    assert statuses["has_graph"]["searched"] is True
    assert statuses["no_graph"]["searched"] is False
    assert "no graph" in statuses["no_graph"]["reason"]
    assert result["repositories_searched"] == 1


def test_a_registered_path_that_disappeared_is_reported(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    gone = tmp_path / "gone"
    gone.mkdir()
    registry = Registry.in_home(home)
    registry.add("gone", gone)
    gone.rmdir()

    result = search_registered("widget", registry=registry)

    status = result["repositories"][0]
    assert status["searched"] is False
    assert "does not exist" in status["reason"]


def _logical_snapshot(path):
    """Every row of every table, so no write of any kind can hide.

    Logical rather than a file hash, because SQLite in WAL mode leaves the main
    file unchanged while a committed write sits in the sidecar. Every table is
    read rather than a chosen few, because a migration writes to tables this
    test did not think to name.
    """
    rows = {}
    with open_database(path) as db:
        tables = [
            r["name"]
            for r in db.fetchall(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name;"
            )
        ]
        for table in tables:
            rows[table] = [tuple(r) for r in db.fetchall(f"SELECT * FROM {table};")]
    return rows


def _file_state(path):
    """The facts a write leaves behind that are not rows.

    A journal-mode conversion rewrites the header and creates sidecars without
    changing a single row, so a purely logical comparison cannot see it. The
    file digest, the change counters, the journal mode, and the presence of the
    sidecars are all recorded, and they are read through a connection that is
    itself read-only so the probe cannot cause what it is looking for.
    """
    import hashlib
    import sqlite3

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        user_version = conn.execute("PRAGMA user_version;").fetchone()[0]
    finally:
        conn.close()
    return {
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "journal_mode": mode,
        "user_version": user_version,
        "sidecars": sorted(p.name for p in path.parent.glob(f"{path.name}-*")),
    }


def test_searching_writes_no_row_to_any_repository_database(registry_with_two, tmp_path):
    """A search must never migrate or otherwise modify somebody else's graph."""
    paths = {name: repo_database_path(tmp_path / name) for name in ("alpha", "beta")}
    before = {name: _logical_snapshot(path) for name, path in paths.items()}

    search_registered("widget", registry=registry_with_two)

    assert {name: _logical_snapshot(path) for name, path in paths.items()} == before


def test_the_row_snapshot_would_notice_a_write(registry_with_two, tmp_path):
    """Guard the guard: a detector that cannot see a write proves nothing."""
    path = repo_database_path(tmp_path / "alpha")
    before = _logical_snapshot(path)

    with open_database(path) as db:
        db.execute(
            "INSERT INTO chunks(chunk_id, document_id, tenant_id, ord, text, text_sha256, "
            "start_offset, end_offset) VALUES (?,?,?,?,?,?,?,?);",
            ("probe", "doc-alpha", "local", 99, "probe", "9" * 64, 0, 5),
        )

    assert _logical_snapshot(path) != before


def test_searching_does_not_convert_a_repositorys_journal_mode(tmp_path):
    """The write that leaves no row: PRAGMA journal_mode rewrites the header.

    A repository not already in WAL mode must come back byte-identical, with no
    sidecars created. This is the case a purely logical snapshot cannot see.
    """
    import sqlite3

    home = tmp_path / "home"
    home.mkdir()
    _make_repo(tmp_path, "victim", ["the widget factory"])
    path = repo_database_path(tmp_path / "victim")
    # Put it back into rollback journalling, which is what a database this tool
    # did not create would plausibly be in.
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode = delete;")
    conn.close()
    for sidecar in path.parent.glob(f"{path.name}-*"):
        sidecar.unlink()

    registry = Registry.in_home(home)
    registry.add("victim", tmp_path / "victim")
    before = _file_state(path)
    assert before["journal_mode"] == "delete"

    result = search_registered("widget", registry=registry)

    after = _file_state(path)
    assert result["repositories_searched"] == 1, "it still has to actually search it"
    assert after["journal_mode"] == "delete", "the journal mode must not be converted"
    assert after["sidecars"] == [], "no -wal or -shm may be left behind"
    assert after["sha256"] == before["sha256"], "not one byte of the file may change"


def test_a_repository_that_cannot_be_opened_is_reported_not_fatal(tmp_path):
    """One unreadable repository must not take down the whole search."""
    import os
    import stat

    home = tmp_path / "home"
    home.mkdir()
    _make_repo(tmp_path, "ok", ["the widget factory"])
    _make_repo(tmp_path, "locked", ["another widget"])
    locked = repo_database_path(tmp_path / "locked")
    # Unreadable rather than merely read-only, so the failure is guaranteed on
    # every platform rather than depending on WAL shared-memory behaviour.
    original = locked.stat().st_mode
    os.chmod(locked, 0)
    registry = Registry.in_home(home)
    registry.add("ok", tmp_path / "ok")
    registry.add("locked", tmp_path / "locked")

    try:
        result = search_registered("widget", registry=registry)
    finally:
        os.chmod(locked, original | stat.S_IRUSR | stat.S_IWUSR)

    statuses = {s["repository"]: s for s in result["repositories"]}
    assert statuses["ok"]["searched"] is True, "the readable repository is still searched"
    assert statuses["locked"]["searched"] is False
    assert statuses["locked"]["reason"], "the reason must be reported, not swallowed"
    assert result["repositories_searched"] == 1


def test_the_connection_itself_refuses_a_write(tmp_path):
    """query_only is belt and braces, so assert it is actually on."""
    import sqlite3

    from dkg.search.federated import ReadOnlyDatabase

    _make_repo(tmp_path, "probe", ["widget"])
    db = ReadOnlyDatabase(repo_database_path(tmp_path / "probe"))
    try:
        with pytest.raises(sqlite3.Error):
            db.execute("INSERT INTO sources(source_id, tenant_id, kind, uri, display_name, added_at) VALUES (?,?,?,?,?,?);",
                       ("x", "local", "t", "u", "d", "2026-08-06T00:00:00Z"))
    finally:
        db.close()


def test_the_merge_is_deterministic(registry_with_two):
    first = search_registered("widget", registry=registry_with_two)
    second = search_registered("widget", registry=registry_with_two)

    assert first["results"] == second["results"]


def test_the_repository_cap_bounds_the_number_of_databases_opened(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    registry = Registry.in_home(home)
    for i in range(5):
        _make_repo(tmp_path, f"r{i}", ["widget"])
        registry.add(f"r{i}", tmp_path / f"r{i}")

    result = search_registered("widget", registry=registry, max_repos=2)

    assert result["repositories_considered"] == 2
    assert result["repositories_registered"] == 5


def test_the_tool_is_registered_on_the_read_only_surface(db):
    from dkg.mcp.tools import build_read_registry

    registry = build_read_registry(db)

    assert "dkg.repos.search" in registry.tools
    assert registry.tools["dkg.repos.search"].kind == "read"


def test_the_mcp_tool_returns_attributed_results(db, tmp_path, monkeypatch, registry_with_two):
    from dkg.mcp.tools import build_read_registry

    monkeypatch.setenv("DKG_HOME", str(registry_with_two.path.parent))
    tools = build_read_registry(db)

    result = tools.call("dkg.repos.search", {"query": "widget"})

    assert result["repositories_searched"] == 2
    assert {r["repository"] for r in result["results"]} == {"alpha", "beta"}

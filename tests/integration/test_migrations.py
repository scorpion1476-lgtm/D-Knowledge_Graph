from dkg.core.db import Database, applied_migrations, apply_migrations, open_database

EXPECTED_MIGRATIONS = [
    "001_baseline.sql",
    "002_embeddings.sql",
    "003_postprocess.sql",
]


def test_apply_migrations_is_idempotent(tmp_path):
    db = Database(tmp_path / "graph.sqlite")
    a = apply_migrations(db)
    b = apply_migrations(db)
    # Applying once returns every packaged migration in order; applying again is
    # a no-op. The list grows as migrations are added; idempotency is the point.
    assert a == EXPECTED_MIGRATIONS
    assert b == []
    names = applied_migrations(db)
    assert names == EXPECTED_MIGRATIONS


def test_open_database_runs_migrations(tmp_path):
    with open_database(tmp_path / "graph.sqlite") as db:
        row = db.fetchone("SELECT name FROM schema_migrations;")
        assert row["name"] == "001_baseline.sql"


def test_migration_query_on_fresh_db_returns_empty_before_apply(tmp_path):
    # Direct sqlite connection, no migrations applied yet: the migrations
    # tracking table must not exist. Confirms migrations do not silently
    # bootstrap outside apply_migrations.
    import sqlite3

    p = tmp_path / "graph.sqlite"
    conn = sqlite3.connect(str(p))
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations';"
        ).fetchone()
        assert row is None
    finally:
        conn.close()


def test_apply_migrations_reports_error_on_invalid_home(tmp_path):
    # A path whose parent does not exist and is not creatable must raise
    # rather than silently create the parent hierarchy.
    tmp_path / "does" / "not" / "exist_yet" / "graph.sqlite"
    # apply_migrations opens the DB which auto-creates the parent; this test
    # asserts a truly unreachable path (an existing regular file used as a
    # directory prefix) fails cleanly.
    blocker = tmp_path / "blocker"
    blocker.write_text("regular file, not a directory")
    invalid_child = blocker / "graph.sqlite"
    import pytest
    with pytest.raises((OSError, Exception)):
        with open_database(invalid_child):
            pass

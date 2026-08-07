def test_baseline_tables_exist(db):
    tables = {r["name"] for r in db.fetchall("SELECT name FROM sqlite_master WHERE type='table';")}
    for expected in (
        "meta",
        "tenants",
        "roles",
        "principals",
        "sources",
        "documents",
        "chunks",
        "entities",
        "mentions",
        "claims",
        "relationships",
        "events",
        "citations",
        "provenance",
        "audit_log",
        "task_runs",
        "chunks_fts",
        "schema_migrations",
    ):
        assert expected in tables, f"missing table: {expected}"


def test_local_tenant_bootstrapped(db):
    row = db.fetchone("SELECT * FROM tenants WHERE tenant_id='local';")
    assert row is not None
    assert row["name"] == "local"


def test_local_owner_role(db):
    row = db.fetchone("SELECT * FROM roles WHERE role_id='role_local_owner';")
    assert row is not None
    assert "admin" in row["permissions"]

from dkg.export.backup import make_backup, restore_backup
from dkg.ingest.base import ingest_text
from dkg.tenancy.models import (
    check_quota,
    count_documents,
    create_tenant,
    delete_tenant,
    list_tenants,
)


def test_upgrade_rollback_via_backup(db, cfg, tmp_path):
    ingest_text(db, "hello world", display_name="v1")
    archive = tmp_path / "b.tar.gz"
    make_backup(db, archive)

    # simulate upgrade change: ingest more, then rollback
    ingest_text(db, "goodbye world", display_name="v2")
    doc_count_after = db.fetchone("SELECT COUNT(*) AS n FROM documents;")["n"]
    assert doc_count_after == 2

    new_home = tmp_path / "restored"
    restore_backup(archive, new_home)
    # verify the restored DB matches the earlier snapshot
    from dkg.core.db import open_database
    with open_database(new_home / "graph.sqlite") as db2:
        c = db2.fetchone("SELECT COUNT(*) AS n FROM documents;")["n"]
        assert c == 1


def test_tenant_creation_and_quota(db):
    t = create_tenant(db, "acme", quota_docs=0)  # zero quota to demonstrate over-quota
    ingest_text(db, "a doc", display_name="d", tenant_id=t.tenant_id)
    q = check_quota(db, t.tenant_id)
    assert q["docs"] >= 1
    assert q["over_quota"] is True


def test_tenant_isolation_counts(db):
    t = create_tenant(db, "iso")
    ingest_text(db, "in local", display_name="l", tenant_id="local")
    ingest_text(db, "in iso", display_name="i", tenant_id=t.tenant_id)
    n_local = count_documents(db, "local")
    n_iso = count_documents(db, t.tenant_id)
    assert n_local >= 1
    assert n_iso == 1


def test_local_tenant_cannot_be_deleted(db):
    import pytest

    from dkg.core.errors import ValidationError

    with pytest.raises(ValidationError):
        delete_tenant(db, "local")


def test_list_tenants_includes_local(db):
    tenants = list_tenants(db)
    assert any(t.tenant_id == "local" for t in tenants)

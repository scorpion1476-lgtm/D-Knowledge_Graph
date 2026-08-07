from dkg.ingest.base import ingest_text
from dkg.search.hybrid import hybrid_search
from dkg.tenancy.models import create_tenant


def test_documents_are_tenant_scoped(db):
    t = create_tenant(db, "alt")
    ingest_text(db, "shared word here", display_name="loc", tenant_id="local")
    ingest_text(db, "shared word here too", display_name="alt", tenant_id=t.tenant_id)

    # count per tenant
    n_local = db.fetchone("SELECT COUNT(*) AS n FROM documents WHERE tenant_id='local';")["n"]
    n_alt = db.fetchone(
        "SELECT COUNT(*) AS n FROM documents WHERE tenant_id=?;", (t.tenant_id,)
    )["n"]
    assert n_local == 1
    assert n_alt == 1


def test_search_returns_across_tenants_by_default(db):
    # Note: the search implementation does not filter by tenant by default; a
    # future release will add a tenant-scoped search path. This test locks the
    # current behaviour so a change is a deliberate promotion.
    t = create_tenant(db, "one")
    ingest_text(db, "unique_marker_word", display_name="a", tenant_id="local")
    ingest_text(db, "unique_marker_word other", display_name="b", tenant_id=t.tenant_id)
    results = hybrid_search(db, "unique_marker_word", limit=10)
    assert len(results) >= 2


def test_document_query_for_unknown_tenant_is_empty(db):
    # A tenant that has never been created still produces no rows for a
    # scoped query. Confirms tenant filtering yields deterministic empty.
    ingest_text(db, "some body", display_name="d", tenant_id="local")
    rows = db.fetchall(
        "SELECT document_id FROM documents WHERE tenant_id=?;",
        ("tenant_does_not_exist",),
    )
    assert rows == []


def test_create_tenant_with_empty_name_rejected(db):
    import pytest

    from dkg.core.errors import ValidationError

    with pytest.raises(ValidationError, match="non-empty"):
        create_tenant(db, "")

"""End-to-end tests for tenants, principals, roles, quotas, identity.

Covers I-01 (local single user default), I-02 (self-hosted identity
adapter), I-03 (tenant aware data model), I-04 (role based access model),
I-06 (per tenant quotas), and I-10 (no mandatory cloud identity).
"""

from __future__ import annotations

import pytest

from dkg.core.errors import ValidationError
from dkg.ingest.base import ingest_text
from dkg.tenancy.identity import LocalIdentityAdapter, build_default_identity
from dkg.tenancy.models import (
    check_quota,
    count_documents,
    create_principal,
    create_role,
    create_tenant,
    delete_tenant,
    list_tenants,
)


def test_default_local_tenant_exists(db):
    tenants = list_tenants(db)
    names = {t.name for t in tenants}
    assert "local" in names, "single-user default tenant 'local' must be present"


def test_create_tenant_and_isolation(db):
    t = create_tenant(db, "alpha")
    ingest_text(db, "shared word", display_name="a", tenant_id="local")
    ingest_text(db, "shared word", display_name="b", tenant_id=t.tenant_id)
    assert count_documents(db, "local") == 1
    assert count_documents(db, t.tenant_id) == 1


def test_create_tenant_rejects_empty_name(db):
    with pytest.raises(ValidationError, match="non-empty"):
        create_tenant(db, "")


def test_create_tenant_rejects_slash_in_name(db):
    with pytest.raises(ValidationError):
        create_tenant(db, "bad/name")


def test_role_creation_and_permission_reject(db):
    t = create_tenant(db, "beta")
    r = create_role(db, t.tenant_id, "reader", ["read"])
    assert r.permissions == ["read"]
    with pytest.raises(ValidationError, match="at least one permission"):
        create_role(db, t.tenant_id, "empty", [])


def test_principal_rejects_invalid_kind(db):
    t = create_tenant(db, "gamma")
    with pytest.raises(ValidationError, match="user, service, or agent"):
        create_principal(db, t.tenant_id, "robot", "R2D2")


def test_principal_creation_valid_kinds(db):
    t = create_tenant(db, "delta")
    for kind in ("user", "service", "agent"):
        p = create_principal(db, t.tenant_id, kind, f"principal-{kind}")
        assert p.kind == kind
        assert p.tenant_id == t.tenant_id


def test_quota_reports_when_over(db):
    t = create_tenant(db, "capped", quota_docs=1)
    ingest_text(db, "one", display_name="a", tenant_id=t.tenant_id)
    r = check_quota(db, t.tenant_id)
    assert r["docs"] == 1
    assert r["over_quota"] is False
    ingest_text(db, "two", display_name="b", tenant_id=t.tenant_id)
    r = check_quota(db, t.tenant_id)
    assert r["docs"] == 2
    assert r["over_quota"] is True
    assert any("quota" in reason for reason in r["reasons"])


def test_check_quota_rejects_missing_tenant(db):
    with pytest.raises(ValidationError, match="tenant not found"):
        check_quota(db, "t_does_not_exist")


def test_local_tenant_cannot_be_deleted(db):
    with pytest.raises(ValidationError, match="cannot delete the built-in"):
        delete_tenant(db, "local")


def test_identity_default_is_local_no_cloud_call(db):
    ident = build_default_identity(db)
    assert isinstance(ident, LocalIdentityAdapter)
    # Unbound subject resolves to None; no network call is possible.
    assert ident.resolve("os:missing_user") is None
    assert ident.authenticate("os:missing_user") is None


def test_identity_bind_and_resolve_roundtrip(db):
    t = create_tenant(db, "iden")
    p = create_principal(db, t.tenant_id, "user", "Alice")
    ident = build_default_identity(db)
    ident.bind("os:alice", p.principal_id)
    resolved = ident.resolve("os:alice")
    assert resolved is not None
    assert resolved.principal_id == p.principal_id
    assert resolved.tenant_id == t.tenant_id
    ident.unbind("os:alice")
    assert ident.resolve("os:alice") is None


def test_identity_bind_rejects_empty_input(db):
    ident = build_default_identity(db)
    with pytest.raises(ValidationError):
        ident.bind("", "prin_x")
    with pytest.raises(ValidationError):
        ident.bind("subject", "")

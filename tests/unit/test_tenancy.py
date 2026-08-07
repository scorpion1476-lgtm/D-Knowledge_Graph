import pytest

from dkg.core.errors import ValidationError
from dkg.tenancy.models import create_role, create_tenant, list_tenants


def test_create_tenant_and_role(db):
    t = create_tenant(db, "acme")
    r = create_role(db, t.tenant_id, "reader", ["read", "export"])
    assert r.permissions == ["read", "export"]
    names = {tt.tenant_id for tt in list_tenants(db)}
    assert t.tenant_id in names
    assert "local" in names


def test_role_requires_permissions(db):
    t = create_tenant(db, "x")
    with pytest.raises(ValidationError):
        create_role(db, t.tenant_id, "empty", [])


def test_bad_tenant_name(db):
    with pytest.raises(ValidationError):
        create_tenant(db, "bad/name")

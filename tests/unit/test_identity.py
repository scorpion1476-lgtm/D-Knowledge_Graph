from dkg.tenancy.identity import LocalIdentityAdapter


def test_bind_resolve_local_principal(db):
    a = LocalIdentityAdapter(db)
    a.bind("alice@local", "user_local")
    p = a.authenticate("alice@local")
    assert p is not None
    assert p.principal_id == "user_local"
    assert p.tenant_id == "local"


def test_unknown_subject_is_none(db):
    a = LocalIdentityAdapter(db)
    assert a.authenticate("nobody@nowhere") is None


def test_unbind_removes_mapping(db):
    a = LocalIdentityAdapter(db)
    a.bind("bob@local", "user_local")
    a.unbind("bob@local")
    assert a.authenticate("bob@local") is None

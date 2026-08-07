from dkg.core.policy import PolicyEngine, PolicyRequest


def test_read_action_allowed_by_default():
    eng = PolicyEngine()
    d = eng.evaluate(
        PolicyRequest(
            action="search.hybrid",
            subject_kind="query",
            subject_id="x",
            principal="user_local",
            principal_permissions=frozenset({"read"}),
        )
    )
    assert d.decision == "allow"


def test_network_disabled_denies():
    eng = PolicyEngine(allow_outbound_network=False)
    d = eng.evaluate(
        PolicyRequest(
            action="ingest.web",
            subject_kind="url",
            subject_id="https://example.com",
            principal="user_local",
            principal_permissions=frozenset({"read", "ingest"}),
            network=True,
        )
    )
    assert d.decision == "deny"


def test_external_effect_requires_consent():
    eng = PolicyEngine(allow_outbound_network=True)
    d = eng.evaluate(
        PolicyRequest(
            action="backup.write",
            subject_kind="file",
            subject_id="/tmp/x",
            principal="user_local",
            principal_permissions=frozenset({"read", "admin"}),
            external_effect=True,
        )
    )
    assert d.decision == "require_consent"


def test_consent_grant_unlocks_external_action():
    eng = PolicyEngine(allow_outbound_network=True)
    eng.grant_consent("consent-token-12345")
    d = eng.evaluate(
        PolicyRequest(
            action="backup.write",
            subject_kind="file",
            subject_id="/tmp/x",
            principal="user_local",
            principal_permissions=frozenset({"read", "admin"}),
            external_effect=True,
            context={"consent_grant": "consent-token-12345"},
        )
    )
    assert d.decision == "allow"


def test_unknown_action_denied():
    eng = PolicyEngine()
    d = eng.evaluate(
        PolicyRequest(
            action="not.a.real.action",
            subject_kind="x",
            subject_id="y",
            principal="user_local",
        )
    )
    assert d.decision == "deny"
    assert "unknown" in d.reason.lower()


def test_capability_check_denies_without_permission():
    eng = PolicyEngine()
    d = eng.evaluate(
        PolicyRequest(
            action="ingest.file",
            subject_kind="file",
            subject_id="/tmp/x",
            principal="user_local",
            principal_permissions=frozenset({"read"}),  # no ingest
        )
    )
    assert d.decision == "deny"
    assert d.matched_rule == "capability-check"

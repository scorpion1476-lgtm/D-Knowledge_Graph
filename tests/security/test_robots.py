"""Robots respect test: we do not attempt authenticated crawling.

The web adapter has no login logic and defaults to respecting robots (that
behaviour lives outside the sandbox because it requires the httpx extra). This
test asserts the configuration default and the absence of any credential
handling code path.
"""

from __future__ import annotations


def test_config_respects_robots_by_default():
    from dkg.core.config import NetworkConfig

    assert NetworkConfig().respect_robots is True


def test_web_module_has_no_login_symbols():
    import dkg.ingest.web as web

    for banned in ("login", "authenticate", "cookies", "session_cookie"):
        assert banned not in web.__dict__, f"web adapter must not expose {banned!r}"


def test_ingest_url_denied_when_outbound_disabled(tmp_path):
    # Fetching a URL when the network policy forbids outbound must raise a
    # PolicyError before any socket is opened.
    import pytest

    from dkg.core.config import (
        DKGConfig,
        IngestConfig,
        MCPConfig,
        NetworkConfig,
        OrchestrationConfig,
        SecurityConfig,
        TelemetryConfig,
    )
    from dkg.core.db import open_database
    from dkg.core.errors import PolicyError
    from dkg.ingest.web import ingest_url

    home = tmp_path / ".dkg"
    home.mkdir()
    cfg = DKGConfig(
        home=home,
        db_path=home / "graph.sqlite",
        audit_path=home / "audit.log",
        ledger_path=home / "evidence.ledger",
        network=NetworkConfig(),  # allow_outbound defaults to False
        ingest=IngestConfig(),
        mcp=MCPConfig(),
        orchestration=OrchestrationConfig(),
        security=SecurityConfig(),
        telemetry=TelemetryConfig(),
    )
    with open_database(cfg.db_path) as db:
        with pytest.raises(PolicyError, match="outbound"):
            ingest_url(db, "https://example.invalid/", cfg=cfg)

"""Security tests for the read-only browser research adapter.

Covers B-08. The adapter must refuse to fetch when outbound is disabled,
must run every URL through the SSRF guard, and must not expose auth or
cookie helpers.
"""

from __future__ import annotations

import pytest

from dkg.adapters.browser import UrllibBrowserAdapter, build_default_browser
from dkg.core.errors import IngestError, PolicyError, SecurityError, SSRFError


def test_browser_denies_fetch_when_outbound_disabled():
    adapter = UrllibBrowserAdapter(allow_outbound=False)
    with pytest.raises(PolicyError, match="outbound"):
        adapter.fetch("https://example.com")


def test_browser_available_false_when_outbound_disabled():
    adapter = UrllibBrowserAdapter(allow_outbound=False)
    ok, reason = adapter.available()
    assert ok is False
    assert "disabled" in reason


def test_browser_rejects_private_target_via_ssrf():
    adapter = UrllibBrowserAdapter(allow_outbound=True)
    with pytest.raises((SSRFError, SecurityError, IngestError)):
        adapter.fetch("http://127.0.0.1/")


def test_browser_rejects_link_local_target_via_ssrf():
    adapter = UrllibBrowserAdapter(allow_outbound=True)
    with pytest.raises((SSRFError, SecurityError, IngestError)):
        adapter.fetch("http://169.254.169.254/")


def test_browser_rejects_missing_scheme():
    adapter = UrllibBrowserAdapter(allow_outbound=True)
    with pytest.raises((SSRFError, SecurityError, IngestError, ValueError)):
        adapter.fetch("not-a-real-url")


def test_browser_module_has_no_auth_or_cookie_symbols():
    import dkg.adapters.browser as mod

    # Read-only adapter must not expose login, cookie, or session helpers.
    banned = ("login", "authenticate_user", "cookies", "session_cookie", "set_cookie")
    for name in banned:
        assert name not in dir(mod), f"browser adapter must not expose {name!r}"


def test_build_default_browser_from_offline_config():
    class Cfg:
        class network:
            allow_outbound = False
            allowlist_domains: list[str] = []
            denylist_domains: list[str] = []
            request_timeout_seconds = 5.0
            user_agent = "test-agent"
            max_response_bytes = 1024

    adapter = build_default_browser(Cfg())
    ok, reason = adapter.available()
    assert ok is False
    assert "disabled" in reason

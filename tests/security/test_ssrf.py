import ipaddress

import pytest

from dkg.core.errors import SSRFError
from dkg.security.ssrf import validate_url


def _resolver(mapping):
    def _r(host):
        return [ipaddress.ip_address(ip) for ip in mapping[host]]
    return _r


def test_public_ip_allowed():
    ok = validate_url("https://example.com/", resolver=_resolver({"example.com": ["93.184.216.34"]}))
    assert ok.host == "example.com"
    assert ok.resolved_ip == "93.184.216.34"


def test_private_ip_denied():
    with pytest.raises(SSRFError):
        validate_url("http://internal.local/", resolver=_resolver({"internal.local": ["10.0.0.5"]}))


def test_loopback_denied():
    with pytest.raises(SSRFError):
        validate_url("http://localhost/", resolver=_resolver({"localhost": ["127.0.0.1"]}))


def test_link_local_denied():
    with pytest.raises(SSRFError):
        validate_url("http://169.254.169.254/", resolver=_resolver({"169.254.169.254": ["169.254.169.254"]}))


def test_metadata_ip_denied():
    # Even if a hostname resolves to the metadata IP, we refuse.
    with pytest.raises(SSRFError):
        validate_url("http://metadata.internal/", resolver=_resolver({"metadata.internal": ["169.254.169.254"]}))


def test_denylist_takes_effect():
    with pytest.raises(SSRFError):
        validate_url(
            "https://example.com/",
            denylist_domains=["example.com"],
            resolver=_resolver({"example.com": ["93.184.216.34"]}),
        )


def test_allowlist_enforced():
    with pytest.raises(SSRFError):
        validate_url(
            "https://other.example/",
            allowlist_domains=["approved.example"],
            resolver=_resolver({"other.example": ["93.184.216.34"]}),
        )


def test_unsupported_scheme_denied():
    with pytest.raises(SSRFError):
        validate_url("file:///etc/passwd", resolver=lambda h: [])

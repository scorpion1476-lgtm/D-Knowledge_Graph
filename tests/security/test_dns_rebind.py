import ipaddress

import pytest

from dkg.core.errors import SSRFError
from dkg.security.ssrf import validate_url


def test_multi_answer_with_any_private_denied():
    def _resolver(_host):
        return [ipaddress.ip_address("93.184.216.34"), ipaddress.ip_address("10.0.0.1")]

    with pytest.raises(SSRFError):
        validate_url("https://rebind.example/", resolver=_resolver)


def test_public_only_multi_answer_allowed():
    def _resolver(_host):
        return [ipaddress.ip_address("93.184.216.34"), ipaddress.ip_address("2606:2800:220:1::1")]

    ok = validate_url("https://safe.example/", resolver=_resolver)
    assert ok.resolved_ip in ("93.184.216.34", "2606:2800:220:1::1")


def test_pinned_getaddrinfo_forces_ip_and_restores():
    # The pin closes the DNS-rebinding window: inside the context the host
    # resolves to the pre-validated IP; afterwards the real resolver is back.
    import socket

    from dkg.security.ssrf import pinned_getaddrinfo

    original = socket.getaddrinfo
    with pinned_getaddrinfo("rebind.example", "93.184.216.34"):
        assert socket.getaddrinfo is not original
        infos = socket.getaddrinfo(
            "rebind.example", 443, socket.AF_INET, socket.SOCK_STREAM
        )
        addrs = {info[4][0] for info in infos}
        assert addrs == {"93.184.216.34"}
    assert socket.getaddrinfo is original


def test_rss_urllib_fallback_refuses_redirect(monkeypatch, cfg):
    # When httpx is not installed the RSS fetch falls back to urllib. That
    # fallback must not silently follow a 3xx to an unvalidated host.
    import contextlib
    import sys
    import urllib.request

    from dkg.core.errors import IngestError
    from dkg.ingest import rss as rssmod
    from dkg.security import ssrf

    monkeypatch.setitem(sys.modules, "httpx", None)  # force the urllib fallback

    class _Target:
        host = "feed.example"
        resolved_ip = "93.184.216.34"

    monkeypatch.setattr(ssrf, "validate_url", lambda *a, **k: _Target())

    @contextlib.contextmanager
    def _noop_pin(*_a, **_k):
        yield

    monkeypatch.setattr(ssrf, "pinned_getaddrinfo", _noop_pin)

    class _Resp:
        status = 302  # a redirect the fallback must refuse

        def read(self, *_a):
            return b""

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    class _Opener:
        def open(self, *_a, **_k):
            return _Resp()

    monkeypatch.setattr(urllib.request, "build_opener", lambda *_a, **_k: _Opener())

    with pytest.raises(IngestError, match="redirect"):
        rssmod._fetch("http://feed.example/rss", cfg)

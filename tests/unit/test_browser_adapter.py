import ipaddress

import pytest

from dkg.adapters.browser import UrllibBrowserAdapter, _extract_text
from dkg.core.errors import PolicyError, SSRFError


def test_disabled_when_network_off():
    a = UrllibBrowserAdapter(allow_outbound=False)
    ok, reason = a.available()
    assert not ok
    with pytest.raises(PolicyError):
        a.fetch("https://example.com/")


def test_ssrf_guard_blocks_private_ip():
    def resolver(_host):
        return [ipaddress.ip_address("10.0.0.1")]

    a = UrllibBrowserAdapter(allow_outbound=True, resolver=resolver)
    with pytest.raises(SSRFError):
        a.fetch("http://internal.local/")


def test_extract_text_html():
    body = b"<html><head><title>T</title></head><body><h1>Hi</h1><p>Body</p><a href='https://x'>x</a></body></html>"
    text, title, links = _extract_text(body, "text/html")
    assert "Hi" in text
    assert "Body" in text
    assert title == "T"
    assert links == ["https://x"]


def test_extract_text_plain():
    text, title, links = _extract_text(b"plain body", "text/plain")
    assert text == "plain body"
    assert title == ""
    assert links == []

"""SSRF and DNS-rebinding guard.

Given a URL, resolve its host to IP addresses, refuse anything that maps to a
private, loopback, link-local, multicast, unspecified, or cloud-metadata range,
and refuse schemes other than http/https. Callers must reuse the resolved IP
for the connection to prevent DNS-rebinding attacks; a helper is provided that
returns a safe (host, ip) pair for pinning.
"""

from __future__ import annotations

import contextlib
import ipaddress
import socket
from collections.abc import Iterator
from dataclasses import dataclass
from urllib.parse import urlparse

from ..core.errors import SSRFError

_IPAddr = ipaddress.IPv4Address | ipaddress.IPv6Address

_METADATA_IPS = {
    ipaddress.ip_address("169.254.169.254"),  # AWS/Azure/GCP metadata
    ipaddress.ip_address("100.100.100.200"),  # Alibaba
    ipaddress.ip_address("fd00:ec2::254"),    # AWS IPv6 metadata (illustrative)
}

_ALLOWED_SCHEMES = {"http", "https"}


@dataclass
class SafeTarget:
    scheme: str
    host: str
    port: int
    resolved_ip: str
    path: str


def _is_disallowed_ip(ip: _IPAddr) -> bool:
    if any(ip == mdip for mdip in _METADATA_IPS):
        return True
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _resolve(host: str) -> list[_IPAddr]:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise SSRFError(f"could not resolve host: {host}") from e
    ips: list[_IPAddr] = []
    for family, _st, _proto, _cn, sockaddr in infos:
        if family == socket.AF_INET:
            ip_str = str(sockaddr[0])
        elif family == socket.AF_INET6:
            ip_str = str(sockaddr[0]).split("%", 1)[0]
        else:
            continue
        ips.append(ipaddress.ip_address(ip_str))
    if not ips:
        raise SSRFError(f"no usable address for host: {host}")
    return ips


def validate_url(
    url: str,
    *,
    allowlist_domains: list[str] | None = None,
    denylist_domains: list[str] | None = None,
    resolver=None,
) -> SafeTarget:
    """Validate a URL and return a SafeTarget with a pinned IP.

    Passing ``resolver`` allows tests to inject a stub without touching DNS.
    The resolver signature is ``callable(host: str) -> list[ipaddress]``.
    """
    if not isinstance(url, str) or not url:
        raise SSRFError("URL must be a non-empty string")
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise SSRFError(f"scheme not allowed: {parsed.scheme!r}")
    if not parsed.hostname:
        raise SSRFError("URL has no hostname")
    host = parsed.hostname.lower()
    if allowlist_domains and not any(host == d or host.endswith("." + d) for d in allowlist_domains):
        raise SSRFError(f"host not on allowlist: {host}")
    if denylist_domains and any(host == d or host.endswith("." + d) for d in denylist_domains):
        raise SSRFError(f"host on denylist: {host}")
    ips = (resolver or _resolve)(host)
    for ip in ips:
        if _is_disallowed_ip(ip):
            raise SSRFError(f"host resolves to disallowed address: {host} -> {ip}")
    # Pin to the first address that satisfied the check.
    resolved = str(ips[0])
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return SafeTarget(
        scheme=parsed.scheme,
        host=host,
        port=port,
        resolved_ip=resolved,
        path=parsed.path or "/",
    )


@contextlib.contextmanager
def pinned_getaddrinfo(host: str, ip: str) -> Iterator[None]:
    """Force resolution of ``host`` to the pre-validated ``ip`` for the body.

    ``validate_url`` resolves and screens the host, but the HTTP stack then
    re-resolves the hostname when it connects, which reopens a DNS-rebinding
    time-of-check to time-of-use window. Wrapping the actual fetch in this
    context manager makes ``socket.getaddrinfo`` return the already-validated
    address for that host, so the connection lands on the screened IP. Other
    hosts resolve normally. The fetch paths are synchronous, so replacing the
    module resolver for the duration of a single request is sufficient; it is
    not intended for concurrent use.
    """
    real = socket.getaddrinfo
    target_host = host.lower()

    def _pinned(h, *args, **kwargs):  # type: ignore[no-untyped-def]
        if isinstance(h, str) and h.lower() == target_host:
            return real(ip, *args, **kwargs)
        return real(h, *args, **kwargs)

    socket.getaddrinfo = _pinned  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.getaddrinfo = real  # type: ignore[assignment]

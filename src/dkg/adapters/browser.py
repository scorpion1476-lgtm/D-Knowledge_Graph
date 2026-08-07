"""Read-only browser research adapter.

A deliberate zero-dependency default that uses only the Python standard library:
``urllib.request`` for transport, the SSRF guard for target validation,
and a minimal HTML text extractor built on ``html.parser``. This means
the platform ships a working browser research capability with no third
party dependency and no headless browser process.

The adapter is read-only. It does not run JavaScript, does not accept or
send cookies, does not authenticate, does not follow more than a small
bounded number of redirects, and refuses any URL that would resolve to a
private, loopback, link-local, multicast, reserved, unspecified, or
cloud metadata address.

Callers that need JavaScript rendering can swap in a heavier backend by
implementing the :class:`BrowserAdapter` interface and registering it on
the capability registry. The core platform has no dependency on any such
backend.
"""

from __future__ import annotations

import gzip
import re
import urllib.error
import urllib.request
import zlib
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser

from ..core.errors import (
    AdapterUnavailableError,
    IngestError,
    PolicyError,
)
from ..security.ssrf import SafeTarget, validate_url

DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_USER_AGENT = "D-Knowledge_Graph/0.1 (+local; read-only)"


@dataclass
class BrowseResult:
    url: str
    final_url: str
    status: int
    content_type: str
    title: str
    text: str
    links: list[str] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)
    bytes_read: int = 0


class BrowserAdapter(ABC):
    """Read-only research adapter interface."""

    name: str

    @abstractmethod
    def fetch(self, url: str) -> BrowseResult: ...

    @abstractmethod
    def available(self) -> tuple[bool, str]: ...


class UrllibBrowserAdapter(BrowserAdapter):
    """Standard-library browser adapter.

    Uses ``urllib.request`` for transport and the SSRF guard on every
    hop, including redirects. No third party dependency.
    """

    name = "urllib"

    def __init__(
        self,
        *,
        allow_outbound: bool,
        allowlist_domains: list[str] | None = None,
        denylist_domains: list[str] | None = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_redirects: int = 3,
        user_agent: str = DEFAULT_USER_AGENT,
        resolver: Callable | None = None,
    ) -> None:
        self.allow_outbound = bool(allow_outbound)
        self.allowlist_domains = list(allowlist_domains or [])
        self.denylist_domains = list(denylist_domains or [])
        self.max_bytes = int(max_bytes)
        self.timeout_seconds = float(timeout_seconds)
        self.max_redirects = int(max_redirects)
        self.user_agent = str(user_agent)
        self.resolver = resolver

    def available(self) -> tuple[bool, str]:
        if not self.allow_outbound:
            return False, "network disabled by configuration"
        return True, "urllib-based read-only browser"

    def fetch(self, url: str) -> BrowseResult:
        if not self.allow_outbound:
            raise PolicyError("outbound network is disabled in configuration")

        seen: set[str] = set()
        current = url
        hops = 0
        while True:
            self._validate(current)
            resp = self._request(current)
            status = resp.getcode()
            headers = {k.lower(): v for k, v in resp.headers.items()}
            if status in (301, 302, 303, 307, 308):
                loc = resp.headers.get("Location")
                if not loc:
                    raise IngestError(f"redirect without Location header from {current!r}")
                if loc in seen:
                    raise IngestError(f"redirect loop at {loc!r}")
                seen.add(current)
                current = loc
                hops += 1
                if hops > self.max_redirects:
                    raise IngestError(f"too many redirects: {hops}")
                continue
            if status >= 400:
                raise IngestError(f"fetch failed: HTTP {status}")

            body = self._read_body(resp)
            ctype = headers.get("content-type", "").split(";", 1)[0].strip() or "text/html"
            text, title, links = _extract_text(body, ctype)
            return BrowseResult(
                url=url,
                final_url=current,
                status=status,
                content_type=ctype,
                title=title,
                text=text,
                links=links,
                headers=headers,
                bytes_read=len(body),
            )

    # -- internals ---------------------------------------------------

    def _validate(self, url: str) -> SafeTarget:
        return validate_url(
            url,
            allowlist_domains=self.allowlist_domains or None,
            denylist_domains=self.denylist_domains or None,
            resolver=self.resolver,
        )

    def _request(self, url: str):
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "text/html, text/plain;q=0.9, */*;q=0.5",
                "Accept-Encoding": "gzip, deflate, identity",
            },
        )
        # NoRedirectHandler: we do our own SSRF revalidation on redirects
        opener = urllib.request.build_opener(_NoRedirect())
        try:
            return opener.open(req, timeout=self.timeout_seconds)
        except urllib.error.HTTPError as e:
            return e  # treat as response so redirect handling still applies
        except urllib.error.URLError as e:
            raise IngestError(f"URL error: {e}") from e

    def _read_body(self, resp) -> bytes:
        cl = resp.headers.get("Content-Length")
        if cl and int(cl) > self.max_bytes:
            raise IngestError(f"response too large by Content-Length: {cl}")
        data = resp.read(self.max_bytes + 1)
        if len(data) > self.max_bytes:
            raise IngestError(f"response body exceeds max_bytes={self.max_bytes}")
        enc = (resp.headers.get("Content-Encoding") or "").lower()
        if "gzip" in enc:
            data = gzip.decompress(data)
        elif "deflate" in enc:
            data = zlib.decompress(data)
        if len(data) > self.max_bytes:
            raise IngestError(f"decompressed body exceeds max_bytes={self.max_bytes}")
        return data


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ARG002
        return None


# -- HTML/text extraction -------------------------------------------


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title: str = ""
        self.links: list[str] = []
        self._in_title = False
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "a":
            for k, v in attrs:
                if k == "href" and v:
                    self.links.append(v)

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self._skip = max(0, self._skip - 1)
        elif tag == "title":
            self._in_title = False
        elif tag in ("p", "br", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6"):
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self.title += data
        else:
            self.parts.append(data)


_WS_COLLAPSE = re.compile(r"[ \t\f\v]+")
_NL_COLLAPSE = re.compile(r"\n{3,}")


def _extract_text(body: bytes, content_type: str) -> tuple[str, str, list[str]]:
    text = body.decode("utf-8", errors="replace")
    ct = (content_type or "").lower()
    if "html" in ct or "<html" in text[:2000].lower():
        p = _TextExtractor()
        try:
            p.feed(text)
            p.close()
        except Exception as e:  # pragma: no cover - defensive
            raise IngestError(f"HTML parse failed: {e}") from e
        joined = "".join(p.parts)
        joined = _WS_COLLAPSE.sub(" ", joined)
        joined = _NL_COLLAPSE.sub("\n\n", joined).strip()
        title = p.title.strip()
        return joined, title, list(dict.fromkeys(p.links))
    # plain text and other content types
    return text.strip(), "", []


# -- helpers exposed to the CLI --------------------------------------


def build_default_browser(cfg) -> BrowserAdapter:
    """Build the default adapter using the current config."""
    if cfg is None:
        raise AdapterUnavailableError("configuration required")
    return UrllibBrowserAdapter(
        allow_outbound=cfg.network.allow_outbound,
        allowlist_domains=cfg.network.allowlist_domains,
        denylist_domains=cfg.network.denylist_domains,
        max_bytes=cfg.network.max_response_bytes,
        timeout_seconds=cfg.network.request_timeout_seconds,
        user_agent=cfg.network.user_agent,
    )

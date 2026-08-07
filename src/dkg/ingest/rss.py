"""RSS/Atom ingestion.

The default parser is stdlib-only (:mod:`dkg.ingest.rss_stdlib`) and
covers RSS 2.0 and Atom 1.0. If ``feedparser`` is installed and
``prefer=feedparser`` is passed, that parser is used instead. Fetching
requires the ``web`` extra (``httpx``) or the built-in urllib browser.
"""

from __future__ import annotations

from typing import Literal

from ..core.config import DKGConfig
from ..core.db import Database
from ..core.errors import AdapterUnavailableError, IngestError, PolicyError
from .base import ingest_text
from .rss_stdlib import ParsedFeed, parse_feed


def ingest_feed(
    db: Database,
    url: str,
    *,
    cfg: DKGConfig,
    prefer: Literal["stdlib", "feedparser"] = "stdlib",
) -> dict:
    if not cfg.network.allow_outbound:
        raise PolicyError("outbound network is disabled in configuration")

    # If the caller specifically asks for the feedparser backend, verify
    # its availability before touching the network so the error is early
    # and clear.
    if prefer == "feedparser":
        try:
            import feedparser  # type: ignore[import-not-found]  # noqa: F401
        except ImportError as e:
            raise AdapterUnavailableError(
                "prefer='feedparser' requires the 'rss' extra"
            ) from e

    body = _fetch(url, cfg)

    parsed: ParsedFeed
    if prefer == "feedparser":
        import feedparser  # type: ignore[import-not-found]

        parsed_alt = feedparser.parse(body)
        parsed = ParsedFeed(
            dialect="feedparser",
            title=getattr(parsed_alt.feed, "title", ""),
            link=getattr(parsed_alt.feed, "link", ""),
            entries=[
                _from_feedparser_entry(e) for e in getattr(parsed_alt, "entries", [])
            ],
        )
    else:
        parsed = parse_feed(body)

    added = 0
    for entry in parsed.entries[:200]:
        title = entry.title or "(untitled)"
        summary = entry.summary or ""
        if not title and not summary:
            continue
        ingest_text(
            db,
            text=f"# {title}\n\n{summary}",
            display_name=title,
            kind="feed-entry",
            metadata={
                "feed_url": url,
                "entry_id": entry.id,
                "entry_link": entry.link,
                "dialect": parsed.dialect,
            },
        )
        added += 1
    return {
        "message": f"ingested {added} feed entries",
        "entries": added,
        "dialect": parsed.dialect,
        "feed_title": parsed.title,
    }


def _fetch(url: str, cfg: DKGConfig) -> bytes:
    # Prefer the stdlib browser adapter so RSS works without any extra.
    from ..security.ssrf import pinned_getaddrinfo, validate_url

    target = validate_url(
        url,
        allowlist_domains=cfg.network.allowlist_domains or None,
        denylist_domains=cfg.network.denylist_domains or None,
    )
    try:
        import httpx  # type: ignore[import-not-found]
    except ImportError:
        # Fall back to urllib via the browser adapter's raw request path.
        import urllib.request

        # Reuse the browser adapter's no-redirect handler so the fallback
        # cannot silently follow a 3xx to an unvalidated host.
        from ..adapters.browser import _NoRedirect

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": cfg.network.user_agent,
                "Accept": (
                    "application/rss+xml, application/atom+xml, "
                    "application/xml, text/xml"
                ),
                "Accept-Encoding": "identity",
            },
        )
        opener = urllib.request.build_opener(_NoRedirect())
        try:
            with pinned_getaddrinfo(target.host, target.resolved_ip):
                with opener.open(  # noqa: S310 - SSRF validated and IP pinned above
                    req, timeout=cfg.network.request_timeout_seconds
                ) as r:
                    if r.status >= 300:
                        raise IngestError(
                            f"feed fetch failed or refused redirect: HTTP {r.status}"
                        )
                    return r.read(cfg.network.max_response_bytes + 1)[
                        : cfg.network.max_response_bytes
                    ]
        except IngestError:
            raise
        except Exception as e:
            raise IngestError(f"feed fetch failed: {e}") from e

    with httpx.Client(
        timeout=cfg.network.request_timeout_seconds,
        headers={"User-Agent": cfg.network.user_agent},
        follow_redirects=False,
    ) as client:
        with pinned_getaddrinfo(target.host, target.resolved_ip):
            r = client.get(url)
        if r.status_code >= 400:
            raise IngestError(f"feed fetch failed: HTTP {r.status_code}")
        body = r.content
        if len(body) > cfg.network.max_response_bytes:
            raise IngestError(f"feed body too large: {len(body)}")
        return body


def _from_feedparser_entry(e):
    return type(
        "FeedparserEntry",
        (),
        {
            "id": getattr(e, "id", "") or getattr(e, "link", ""),
            "title": getattr(e, "title", ""),
            "summary": getattr(e, "summary", "") or getattr(e, "description", ""),
            "link": getattr(e, "link", ""),
            "published": getattr(e, "published", ""),
            "updated": getattr(e, "updated", ""),
            "authors": [],
        },
    )

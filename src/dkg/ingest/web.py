"""Safe web fetch. Requires the 'web' extra (httpx)."""

from __future__ import annotations

from ..core.config import DKGConfig
from ..core.db import Database
from ..core.errors import AdapterUnavailableError, IngestError, PolicyError
from ..security.ssrf import pinned_getaddrinfo, validate_url
from .base import ingest_text


def ingest_url(db: Database, url: str, *, cfg: DKGConfig) -> dict:
    if not cfg.network.allow_outbound:
        raise PolicyError("outbound network is disabled in configuration")
    try:
        import httpx  # type: ignore[import-not-found]
    except ImportError as e:
        raise AdapterUnavailableError(
            "web fetch requires the 'web' extra: pip install d-knowledge-graph[web]"
        ) from e
    target = validate_url(
        url,
        allowlist_domains=cfg.network.allowlist_domains or None,
        denylist_domains=cfg.network.denylist_domains or None,
    )
    limits = httpx.Limits(max_connections=1, max_keepalive_connections=1)
    with httpx.Client(
        timeout=cfg.network.request_timeout_seconds,
        headers={"User-Agent": cfg.network.user_agent},
        follow_redirects=False,
        limits=limits,
    ) as client:
        # Pin the validated IP so the connection cannot be rebound to an
        # internal address between validation and connect.
        with pinned_getaddrinfo(target.host, target.resolved_ip):
            r = client.get(url)
        if r.status_code >= 400:
            raise IngestError(f"fetch failed: HTTP {r.status_code}")
        content_length = int(r.headers.get("Content-Length", "0") or 0)
        if content_length and content_length > cfg.network.max_response_bytes:
            raise IngestError(f"response too large: {content_length}")
        body = r.content
        if len(body) > cfg.network.max_response_bytes:
            raise IngestError(f"response too large: {len(body)}")
        text = body.decode(r.encoding or "utf-8", errors="replace")

    report = ingest_text(db, text=text, display_name=target.host, kind="url")
    return {"message": f"ingested {target.host}", **report.to_dict()}

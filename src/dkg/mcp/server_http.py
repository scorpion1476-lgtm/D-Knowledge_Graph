"""Loopback-default HTTP JSON-RPC server built on http.server.

This is deliberately minimal: it binds to loopback by default, authorises by
credential, enforces a request-size cap, applies a simple per-token rate limit,
and returns structured errors. Use a reverse proxy for TLS in real deployments.

Authorisation never rests on the peer address. A page in a browser on this
machine connects from 127.0.0.1 exactly like a legitimate local client does, so
trusting loopback would hand the graph to any site the user visits. Every
request passes ``http_guard`` first: Host, Origin, content type, then
credential. See that module for why each check is there.
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from ..core.config import DKGConfig
from ..core.db import Database
from ..core.errors import ConfigError, DKGError
from ..security.redact import redact_dict
from .http_guard import expected_authorities, guard_request, startup_refusal
from .protocol import (
    ERR_INTERNAL,
    ERR_INVALID,
    ERR_METHOD_NOT_FOUND,
    make_error,
    make_response,
    parse_request,
)
from .tools import build_read_registry


def build_app(db: Database, cfg: DKGConfig):
    reg = build_read_registry(db, allowlist=cfg.mcp.tool_allowlist or None)
    token_env = cfg.mcp.http_bearer_token_env
    token = os.environ.get(token_env, "")
    # Optional shared secret for a minimal client-credentials exchange
    # against ``/token``. This is off unless DKG_MCP_CLIENT_SECRET is
    # set. When set, callers can POST client_secret to /token and
    # receive an opaque bearer token they use for subsequent /rpc calls.
    client_secret = os.environ.get("DKG_MCP_CLIENT_SECRET", "")
    max_bytes = int(cfg.mcp.http_max_request_bytes)
    rate = _RateWindow(cfg.mcp.http_rate_limit_per_minute)
    bind = cfg.mcp.http_bind
    authorities = set(expected_authorities(bind, int(cfg.mcp.http_port)))
    authorities.update(cfg.mcp.http_allowed_hosts or [])
    allowed_origins = set(cfg.mcp.http_allowed_origins or [])
    allow_unauth_loopback = bool(cfg.mcp.http_allow_unauthenticated_loopback)

    # In-memory issued tokens (server lifetime only).
    issued: set[str] = set()

    class Handler(BaseHTTPRequestHandler):
        server_version = "dkg-mcp/0.1"

        def log_message(self, fmt, *args):  # silence default access log
            pass

        def _guard(self, *, require_auth: bool, require_json: bool):
            """Run the inbound guard for this request.

            Returns None when the request may proceed, or writes the refusal and
            returns the decision when it may not. The refusal body names the
            reason but never echoes a header value back, so a rejected request
            cannot use the error as a reflection primitive.
            """
            return guard_request(
                path=self.path,
                host_header=self.headers.get("Host", ""),
                origin_header=self.headers.get("Origin", ""),
                referer_header=self.headers.get("Referer", ""),
                content_type=self.headers.get("Content-Type", ""),
                authorization_header=self.headers.get("Authorization", ""),
                peer=self.client_address[0],
                allowed_authorities=authorities,
                allowed_origins=allowed_origins,
                configured_token=token,
                issued_tokens=issued,
                client_secret_configured=bool(client_secret),
                bind=bind,
                allow_unauthenticated_loopback=allow_unauth_loopback,
                require_auth=require_auth,
                require_json=require_json,
            )

        def do_GET(self):  # noqa: N802
            # The liveness probe cannot require a credential, but Host and
            # Origin still apply: a rebound name must not get a 200 either.
            decision = self._guard(require_auth=False, require_json=False)
            if decision.denied:
                return self._write_json(decision.status, {"error": decision.reason})
            if self.path == "/healthz":
                return self._write_json(200, {"ok": True})
            return self._write_json(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            if self.path == "/token":
                if not client_secret:
                    return self._write_json(404, {"error": "token endpoint disabled"})
                # The exchange establishes the credential, so it cannot demand
                # one; it still must pass Host, Origin, and the JSON content
                # type so a browser page cannot mint a token either.
                decision = self._guard(require_auth=False, require_json=True)
                if decision.denied:
                    return self._write_json(decision.status, {"error": decision.reason})
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 4096:
                    return self._write_json(400, {"error": "empty or oversize body"})
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                except json.JSONDecodeError:
                    return self._write_json(400, {"error": "invalid json"})
                sup = payload.get("client_secret", "")
                if sup != client_secret:
                    return self._write_json(401, {"error": "invalid client_secret"})
                import secrets as _secrets
                tok = _secrets.token_hex(24)
                issued.add(tok)
                return self._write_json(
                    200,
                    {"access_token": tok, "token_type": "Bearer", "expires_in": 3600},
                )

            if self.path not in ("/rpc", "/"):
                return self._write_json(404, {"error": "not found"})

            decision = self._guard(require_auth=True, require_json=True)
            if decision.denied:
                return self._write_json(decision.status, {"error": decision.reason})

            client_id = self.client_address[0] + "|" + (self.headers.get("Authorization") or "")
            if not rate.check(client_id):
                return self._write_json(429, {"error": "rate limit exceeded"})

            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > max_bytes:
                return self._write_json(413, {"error": f"request too large or empty (limit {max_bytes})"})
            body = self.rfile.read(length).decode("utf-8", errors="replace")

            try:
                req = parse_request(body)
            except ValueError as e:
                return self._write_json(400, make_error(None, ERR_INVALID, str(e)))

            method = req.get("method")
            params = req.get("params") or {}
            rid = req.get("id")

            if method == "initialize":
                out = make_response(rid, {"server": "d-knowledge-graph", "protocol": "jsonrpc-2.0"})
            elif method == "tools/list":
                out = make_response(rid, {"tools": reg.list()})
            elif method == "tools/call":
                name = params.get("name")
                args = params.get("arguments") or {}
                try:
                    result = reg.call(name, args)
                    out = make_response(rid, redact_dict(result))
                except DKGError as e:
                    out = make_error(rid, ERR_INTERNAL, e.to_dict()["message"], data=e.to_dict())
            else:
                out = make_error(rid, ERR_METHOD_NOT_FOUND, f"method not found: {method}")
            return self._write_json(200, out)

        def _write_json(self, status: int, obj: Any) -> None:
            data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


class _RateWindow:
    def __init__(self, per_minute: int) -> None:
        self.per_minute = int(per_minute)
        self._counters: dict[str, list[int]] = {}

    def check(self, key: str) -> bool:
        import time
        now = int(time.time())
        window = 60
        bucket = self._counters.setdefault(key, [])
        bucket[:] = [t for t in bucket if t > now - window]
        if len(bucket) >= self.per_minute:
            return False
        bucket.append(now)
        return True


def serve_http(db: Database, *, host: str, port: int, cfg: DKGConfig) -> None:
    # The bind actually used must be the one the guard reasons about, otherwise
    # a --bind flag could widen the surface while the guard still believed it
    # was loopback and kept allowing the unauthenticated path.
    cfg.mcp.http_bind = host
    cfg.mcp.http_port = int(port)
    refusal = startup_refusal(
        bind=host,
        configured_token=os.environ.get(cfg.mcp.http_bearer_token_env, ""),
        client_secret_configured=bool(os.environ.get("DKG_MCP_CLIENT_SECRET", "")),
    )
    if refusal:
        raise ConfigError(refusal)
    handler = build_app(db, cfg)
    httpd = ThreadingHTTPServer((host, int(port)), handler)
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()

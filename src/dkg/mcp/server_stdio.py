"""Stdio JSON-RPC server."""

from __future__ import annotations

import json
import sys
from typing import IO

from ..core.db import Database
from ..core.errors import DKGError
from ..security.redact import redact_dict
from .protocol import (
    ERR_INTERNAL,
    ERR_INVALID,
    ERR_METHOD_NOT_FOUND,
    make_error,
    make_response,
    parse_request,
)
from .tools import build_read_registry


def _handle(reg, request: dict) -> dict:
    method = request.get("method")
    params = request.get("params") or {}
    rid = request.get("id")

    if method == "initialize":
        return make_response(rid, {"server": "d-knowledge-graph", "protocol": "jsonrpc-2.0"})
    if method == "tools/list":
        return make_response(rid, {"tools": reg.list()})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            out = reg.call(name, args)
        except DKGError as e:
            return make_error(rid, ERR_INTERNAL, e.to_dict()["message"], data=e.to_dict())
        return make_response(rid, redact_dict(out))
    return make_error(rid, ERR_METHOD_NOT_FOUND, f"method not found: {method}")


def serve_stdio(
    db: Database,
    *,
    stdin: IO[str] | None = None,
    stdout: IO[str] | None = None,
) -> None:
    reg = build_read_registry(db)
    inp = stdin or sys.stdin
    out = stdout or sys.stdout
    for line in inp:
        line = line.strip()
        if not line:
            continue
        try:
            req = parse_request(line)
        except ValueError as e:
            out.write(json.dumps(make_error(None, ERR_INVALID, str(e))) + "\n")
            out.flush()
            continue
        response = _handle(reg, req)
        out.write(json.dumps(response, ensure_ascii=False) + "\n")
        out.flush()


def handle_line(db: Database, line: str) -> str:
    """Test helper: process a single JSON-RPC line and return the response line."""
    reg = build_read_registry(db)
    try:
        req = parse_request(line)
    except ValueError as e:
        return json.dumps(make_error(None, ERR_INVALID, str(e)))
    return json.dumps(_handle(reg, req), ensure_ascii=False)

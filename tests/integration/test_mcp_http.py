"""HTTP MCP integration tests.

These tests bind a real TCP socket on 127.0.0.1 and exercise the HTTP MCP
surface end-to-end. They are the ONLY tests in the suite that require the
process to be permitted to bind a socket, so they carry a narrow skip
guard.

Skip policy:
- Skip only when ``socket.socket().bind(('127.0.0.1', 0))`` raises
  ``PermissionError`` or ``OSError``. Any other failure fails the test.
- The skip message includes the exact exception (typically
  ``[Errno 1] Operation not permitted`` in the D-Knowledge_Graph sandbox
  environment) so evidence bundles can quote it verbatim.
- On any host that permits loopback socket bind (developer laptop, CI
  runner, container with the CAP_NET_BIND_SERVICE capability the
  loopback range does not require) these tests run normally without a
  skip. There is no separate test file; the same file serves both paths.

To force the "normal host" path in a constrained environment, run the
suite in a location that permits loopback bind (e.g. a Docker container
started without additional network restrictions). The test does not
attempt to lift the restriction on its own.
"""

import json
import threading
import time
import urllib.request

from dkg.core.config import MCPConfig
from dkg.mcp.server_http import serve_http


def _find_port() -> int:
    import socket

    import pytest

    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
    except (PermissionError, OSError) as e:
        s.close()
        # Preserve the exact error message so downstream evidence bundles
        # can quote it. The skip is narrow: only socket-bind failures on
        # loopback trigger it.
        pytest.skip(
            f"socket bind not permitted in this environment: {e!r}"
        )
    port = s.getsockname()[1]
    s.close()
    return port


def _start(db, cfg, host="127.0.0.1"):
    port = _find_port()
    # These tests exercise transport behaviour (framing, size caps, error
    # shapes), not authorisation, so they opt in to the unauthenticated
    # loopback mode explicitly. That opt-in is exactly what F-19 made
    # mandatory: it used to be the silent default, which is what let a page in
    # a browser drive the server. The authorisation policy itself is tested in
    # tests/security/test_http_origin_guard.py.
    cfg.mcp = MCPConfig(
        http_enabled=True,
        http_bind=host,
        http_port=port,
        http_bearer_token_env="DKG_MCP_TOKEN",
        http_allow_unauthenticated_loopback=True,
    )
    t = threading.Thread(target=serve_http, args=(db,), kwargs={"host": host, "port": port, "cfg": cfg}, daemon=True)
    t.start()
    # wait for server to bind
    for _ in range(50):
        try:
            with urllib.request.urlopen(f"http://{host}:{port}/healthz", timeout=1) as r:
                if r.status == 200:
                    return port
        except Exception:
            time.sleep(0.05)
    raise RuntimeError("server did not start")


def test_health_endpoint(db, cfg):
    port = _start(db, cfg)
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz") as r:
        assert r.status == 200
        assert json.loads(r.read().decode("utf-8"))["ok"] is True


def test_tools_list_via_http_from_loopback(db, cfg):
    port = _start(db, cfg)
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/rpc",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as r:
        data = json.loads(r.read().decode("utf-8"))
    assert "tools" in data["result"]


def test_unauthorized_request_denied_when_token_required(db, cfg):
    # Configure the server to require a bearer token, then hit it without one.
    port = _find_port()
    cfg.mcp = MCPConfig(
        http_enabled=True,
        http_bind="127.0.0.1",
        http_port=port,
        http_bearer_token_env="DKG_TEST_MCP_TOKEN_REQUIRED",
    )
    import os

    os.environ["DKG_TEST_MCP_TOKEN_REQUIRED"] = "s3cret_test_token_value"
    try:
        t = threading.Thread(
            target=serve_http,
            args=(db,),
            kwargs={"host": "127.0.0.1", "port": port, "cfg": cfg},
            daemon=True,
        )
        t.start()
        # wait for bind
        for _ in range(50):
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/healthz", timeout=1
                ) as r:
                    if r.status == 200:
                        break
            except Exception:
                time.sleep(0.05)

        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/rpc",
            data=json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req)
            assert False, "expected 401 for missing bearer token"
        except urllib.error.HTTPError as e:
            assert e.code == 401
    finally:
        os.environ.pop("DKG_TEST_MCP_TOKEN_REQUIRED", None)


def test_invalid_json_returns_400(db, cfg):
    port = _start(db, cfg)
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/rpc",
        data=b"this is not json",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req)
        assert False, "expected 400 for invalid JSON"
    except urllib.error.HTTPError as e:
        assert e.code == 400


def test_size_limit_returns_413(db, cfg):
    port = _start(db, cfg)
    body = b"{" + b"a" * (5 * 1024 * 1024) + b"}"
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/rpc",
        data=body,
        headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
        method="POST",
    )
    try:
        urllib.request.urlopen(req)
        assert False, "expected 413 or connection reset"
    except urllib.error.HTTPError as e:
        # Server responded with the intended 413 before consuming the body.
        assert e.code == 413
    except (urllib.error.URLError, ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
        # Some client stacks receive a broken pipe or connection reset when
        # the server rejects the oversized body before reading it. That
        # behaviour also demonstrates the size limit is enforced. Windows
        # sockets raise ConnectionResetError directly rather than wrapping
        # in URLError.
        pass

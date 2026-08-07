"""Bounded loopback server for the generated offline viewer (R-23).

Exercises the real socket on loopback: the server is started, requested against
with http.client from the standard library, and stopped, and the thread count is
checked before and after so a leaked thread fails the test rather than passing
quietly.

The refusals are the point of the row, so they are tested as hard as the happy
path: a non-loopback bind must raise before any socket exists, an unknown path
must not reach the filesystem, and every bound must actually bind.
"""

from __future__ import annotations

import http.client
import socket
import threading
import time

import pytest

from dkg.core.db import open_database
from dkg.export.serve import (
    DEFAULT_MAX_REQUEST_BYTES,
    DEFAULT_MAX_REQUESTS,
    DEFAULT_REQUEST_TIMEOUT,
    NonLoopbackBindError,
    ServerLimits,
    ViewerServer,
    resolve_bind_host,
)
from dkg.export.viz import export_html
from dkg.export.viz_cli import COMMANDS, dispatch, register

VIEWER_MARKER = b"D-Knowledge_Graph visualization"


def _free_port() -> int:
    """An unused loopback port. The port is then passed explicitly, as the row requires."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _viewer(tmp_path):
    with open_database(tmp_path / "g.db") as db:
        db.execute(
            "INSERT INTO entities(entity_id, tenant_id, kind, canonical, display, metadata_json) "
            "VALUES (?,?,?,?,?,?);",
            ("e1", "local", "code:class", "pkg::A", "AlphaNode", "{}"),
        )
        return export_html(db, tmp_path / "viewer.html")


def _get(server, path="/", method="GET", body=None, timeout=5.0):
    conn = http.client.HTTPConnection(server.host, server.port, timeout=timeout)
    try:
        conn.request(method, path, body=body)
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


def _raw(server, request: bytes, timeout=5.0) -> bytes:
    """Send a request byte for byte, so the size caps can be exercised exactly."""
    with socket.create_connection((server.host, server.port), timeout=timeout) as sock:
        sock.sendall(request)
        chunks = []
        while True:
            piece = sock.recv(4096)
            if not piece:
                break
            chunks.append(piece)
    return b"".join(chunks)


def _wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


# --------------------------------------------------------------------------
# Loopback only
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host",
    ["0.0.0.0", "::", "192.168.1.10", "10.0.0.7", "8.8.8.8", "example.com", "graph.internal", "", "   "],
)
def test_a_non_loopback_bind_is_refused(host):
    with pytest.raises(NonLoopbackBindError):
        resolve_bind_host(host)


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", "127.0.0.1"),
        ("127.0.0.5", "127.0.0.5"),
        ("localhost", "127.0.0.1"),
        ("LOCALHOST", "127.0.0.1"),
        ("::1", "::1"),
        ("[::1]", "::1"),
    ],
)
def test_loopback_addresses_are_accepted(host, expected):
    assert resolve_bind_host(host) == expected


def test_the_guard_actually_stops_a_socket_from_being_created(tmp_path):
    """The refusal must happen before a listening socket exists, not after."""
    viewer = _viewer(tmp_path)
    port = _free_port()
    with pytest.raises(NonLoopbackBindError):
        ViewerServer(viewer, host="0.0.0.0", port=port)
    # Nothing is listening on that port, so a connection attempt must fail.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1.0)
        with pytest.raises(OSError):
            probe.connect(("127.0.0.1", port))


def test_the_port_is_always_explicit(tmp_path):
    viewer = _viewer(tmp_path)
    for bad in (0, -1, 70000, None, "8080", True):
        with pytest.raises((ValueError, TypeError)):
            ViewerServer(viewer, host="127.0.0.1", port=bad)


def test_a_missing_viewer_file_is_refused(tmp_path):
    with pytest.raises(FileNotFoundError):
        ViewerServer(tmp_path / "nope.html", host="127.0.0.1", port=_free_port())


# --------------------------------------------------------------------------
# Serving exactly one file
# --------------------------------------------------------------------------


def test_the_viewer_is_served_byte_for_byte(tmp_path):
    viewer = _viewer(tmp_path)
    with ViewerServer(viewer, host="127.0.0.1", port=_free_port()) as server:
        status, headers, body = _get(server, "/")
        assert status == 200
        assert body == viewer.read_bytes()
        assert VIEWER_MARKER in body
        assert headers["Content-Type"].startswith("text/html")
        # The response forbids the page from loading anything, a second layer
        # under the file being self-contained in the first place.
        assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert not server.running


def test_index_html_is_the_same_document_and_a_query_string_is_ignored(tmp_path):
    viewer = _viewer(tmp_path)
    with ViewerServer(viewer, host="127.0.0.1", port=_free_port()) as server:
        for path in ("/", "/index.html", "/index.html?zoom=2"):
            status, _headers, body = _get(server, path)
            assert status == 200, path
            assert body == viewer.read_bytes()


@pytest.mark.parametrize(
    "path",
    [
        "/secret.txt",
        "/viewer.html",
        "/../secret.txt",
        "/../../etc/passwd",
        "/%2e%2e/secret.txt",
        "//secret.txt",
        "/./secret.txt",
        "/index.html/../secret.txt",
        "/g.db",
    ],
)
def test_nothing_but_the_viewer_can_be_reached(tmp_path, path):
    viewer = _viewer(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("this must never be served", encoding="utf-8")
    with ViewerServer(viewer, host="127.0.0.1", port=_free_port()) as server:
        status, _headers, body = _get(server, path)
        assert status == 404, f"{path} was not refused"
        assert b"this must never be served" not in body
        assert VIEWER_MARKER not in body


def test_only_get_and_head_are_allowed(tmp_path):
    viewer = _viewer(tmp_path)
    with ViewerServer(viewer, host="127.0.0.1", port=_free_port()) as server:
        status, headers, body = _get(server, "/", method="HEAD")
        assert status == 200 and body == b""
        for method in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS"):
            status, headers, _body = _get(server, "/", method=method)
            assert status == 405, method
            assert headers["Allow"] == "GET, HEAD"


# --------------------------------------------------------------------------
# Bounds
# --------------------------------------------------------------------------


def test_an_oversized_request_is_refused(tmp_path):
    viewer = _viewer(tmp_path)
    limits = ServerLimits(max_requests=10, request_timeout=5.0, max_request_bytes=256)
    with ViewerServer(viewer, host="127.0.0.1", port=_free_port(), limits=limits) as server:
        long_line = b"GET /" + b"a" * 4000 + b" HTTP/1.0\r\n\r\n"
        assert b" 414 " in _raw(server, long_line).split(b"\r\n")[0] + b" "
        # A body declared larger than the cap is refused before a byte is read.
        oversized_body = b"POST / HTTP/1.0\r\nContent-Length: 999999\r\n\r\n"
        assert b"413" in _raw(server, oversized_body).split(b"\r\n")[0]
        # A header block over the cap is refused too.
        fat_headers = b"GET / HTTP/1.0\r\nX-Pad: " + b"p" * 300 + b"\r\n\r\n"
        assert b"431" in _raw(server, fat_headers).split(b"\r\n")[0]
        # The cap does not break an ordinary request.
        assert _get(server, "/")[0] == 200


def test_the_request_budget_stops_the_server_on_its_own(tmp_path):
    viewer = _viewer(tmp_path)
    limits = ServerLimits(max_requests=2, request_timeout=5.0)
    server = ViewerServer(viewer, host="127.0.0.1", port=_free_port(), limits=limits).start()
    try:
        assert _get(server, "/")[0] == 200
        assert _get(server, "/")[0] == 200
        assert _wait_until(lambda: not server.running), "the server ignored its request budget"
        assert server.requests_served == 2
    finally:
        server.stop()


def test_limits_are_validated_rather_than_silently_accepted():
    for bad in (
        ServerLimits(max_requests=0),
        ServerLimits(request_timeout=0),
        ServerLimits(max_request_bytes=0),
        ServerLimits(poll_interval=0),
    ):
        with pytest.raises(ValueError):
            bad.validated()
    assert ServerLimits().validated() == ServerLimits()
    assert (DEFAULT_MAX_REQUESTS, DEFAULT_REQUEST_TIMEOUT, DEFAULT_MAX_REQUEST_BYTES) == (100, 30.0, 65536)


# --------------------------------------------------------------------------
# Clean shutdown
# --------------------------------------------------------------------------


def test_stop_leaves_no_thread_and_frees_the_port(tmp_path):
    viewer = _viewer(tmp_path)
    port = _free_port()
    server = ViewerServer(viewer, host="127.0.0.1", port=port).start()
    assert _get(server, "/")[0] == 200
    assert any(t.name == "dkg-viewer-server" for t in threading.enumerate())
    server.stop()
    assert not server.running
    assert not any(t.name == "dkg-viewer-server" for t in threading.enumerate()), "stop() left a thread behind"
    # The listening socket is closed, so nothing answers on that port.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1.0)
        with pytest.raises(OSError):
            probe.connect(("127.0.0.1", port))


def test_stop_is_idempotent_and_the_count_survives_it(tmp_path):
    viewer = _viewer(tmp_path)
    server = ViewerServer(viewer, host="127.0.0.1", port=_free_port()).start()
    assert _get(server, "/")[0] == 200
    server.stop()
    assert server.requests_served == 1
    server.stop()
    assert server.requests_served == 1


def test_a_server_cannot_be_started_twice(tmp_path):
    viewer = _viewer(tmp_path)
    server = ViewerServer(viewer, host="127.0.0.1", port=_free_port()).start()
    try:
        with pytest.raises(RuntimeError):
            server.start()
    finally:
        server.stop()


# --------------------------------------------------------------------------
# The CLI surface
# --------------------------------------------------------------------------


def test_the_subcommand_is_registered_with_an_explicit_required_port():
    import argparse

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    register(sub)
    assert COMMANDS == ("viz-serve",)
    with pytest.raises(SystemExit):
        parser.parse_args(["viz-serve"])  # --port is required
    args = parser.parse_args(["viz-serve", "--port", "9999"])
    assert args.viz_port == 9999
    assert args.viz_host == "127.0.0.1"
    assert args.viz_file is None
    assert args.viz_max_requests == DEFAULT_MAX_REQUESTS
    assert args.viz_request_timeout == DEFAULT_REQUEST_TIMEOUT
    assert args.viz_max_request_bytes == DEFAULT_MAX_REQUEST_BYTES


class _Args:
    cmd = "viz-serve"
    as_json = False
    viz_host = "127.0.0.1"
    viz_max_requests = 1
    viz_request_timeout = 5.0
    viz_max_request_bytes = DEFAULT_MAX_REQUEST_BYTES
    viz_max_nodes = None

    def __init__(self, port, file=None, host="127.0.0.1"):
        self.viz_port = port
        self.viz_file = file
        self.viz_host = host


def test_dispatch_ignores_commands_it_does_not_own(cfg):
    class Other:
        cmd = "export"

    assert dispatch(cfg, Other()) is None


def _dispatch_bounded(cfg, args, timeout=15.0):
    """Run the subcommand on a bounded daemon thread.

    A regression that let the command block instead of returning must fail this
    test, not hang the session, so nothing here ever joins without a deadline.
    """
    result: dict[str, object] = {}

    def run():
        result["code"] = dispatch(cfg, args)

    worker = threading.Thread(target=run, name="viz-cli-under-test", daemon=True)
    worker.start()
    worker.join(timeout)
    return worker, result


def test_the_cli_refuses_a_non_loopback_host(cfg, tmp_path, capsys):
    viewer = _viewer(tmp_path)
    worker, result = _dispatch_bounded(cfg, _Args(_free_port(), file=str(viewer), host="0.0.0.0"), timeout=10.0)
    assert not worker.is_alive(), "the command bound a non-loopback address instead of refusing"
    assert result["code"] == 2
    assert "loopback" in capsys.readouterr().err


def test_the_cli_serves_the_generated_viewer_and_stops(cfg, tmp_path, capsys):
    """End to end through the registered subcommand, with a budget of one request."""
    viewer = _viewer(tmp_path)
    port = _free_port()
    result: dict[str, object] = {}

    def run():
        result["code"] = dispatch(cfg, _Args(port, file=str(viewer)))

    # A daemon thread, so a regression that made the command never return would
    # fail this test rather than hang the whole session at interpreter exit.
    worker = threading.Thread(target=run, name="viz-cli-under-test", daemon=True)
    worker.start()
    try:
        assert _wait_until(lambda: _port_answers(port)), "the CLI never started serving"
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5.0)
        conn.request("GET", "/")
        body = conn.getresponse().read()
        conn.close()
        assert body == viewer.read_bytes()
    finally:
        worker.join(timeout=15.0)
    assert not worker.is_alive(), "the CLI did not return after its request budget"
    assert result["code"] == 0
    assert "stopped after 1 request" in capsys.readouterr().out


def test_the_cli_refuses_a_viewer_file_that_is_not_there(cfg, tmp_path, capsys):
    worker, result = _dispatch_bounded(cfg, _Args(_free_port(), file=str(tmp_path / "absent.html")), timeout=10.0)
    assert not worker.is_alive()
    assert result["code"] == 2
    assert "absent.html" in capsys.readouterr().err


def _port_answers(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.3)
        try:
            probe.connect(("127.0.0.1", port))
        except OSError:
            return False
    return True


@pytest.fixture
def tmp_viewer(tmp_path):
    """The generated viewer file, as a fixture for the tests added below."""
    return _viewer(tmp_path)

# -- bounds and shutdown, added after an adversarial review ------------------


def test_an_unknown_verb_consumes_the_request_budget(tmp_viewer):
    """http.server answers an unknown verb itself, before any of our handlers.

    Counting inside do_GET, do_HEAD, and the method refusal therefore missed
    them entirely: a review sent ten TRACE requests against a budget of three
    and the counter stayed at zero while the thread served every one, so the
    bound could be walked past indefinitely.
    """
    import http.client

    port = _free_port()
    server = ViewerServer(
        tmp_viewer, host="127.0.0.1", port=port,
        limits=ServerLimits(max_requests=3, request_timeout=2.0),
    ).start()
    try:
        for _ in range(6):
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2.0)
                conn.request("TRACE", "/")
                conn.getresponse().read()
                conn.close()
            except OSError:
                break
        assert _wait_until(lambda: not server.running, timeout=5.0), (
            "the budget never ran out, so unknown verbs are still uncounted"
        )
        assert server.requests_served >= 3
    finally:
        server.stop()


def test_a_bare_connection_that_sends_nothing_does_not_consume_the_budget(tmp_viewer):
    """Guard the other way: a pre-connect must not exhaust a small budget."""
    import socket as _socket

    port = _free_port()
    server = ViewerServer(
        tmp_viewer, host="127.0.0.1", port=port,
        limits=ServerLimits(max_requests=1, request_timeout=1.0),
    ).start()
    try:
        probe = _socket.create_connection(("127.0.0.1", port), timeout=2.0)
        probe.close()
        assert _wait_until(lambda: server.requests_served == 0, timeout=3.0)
        assert server.running, "an empty connection consumed the whole budget"
    finally:
        server.stop()


def test_a_foreign_host_header_is_refused_so_dns_rebinding_cannot_read_the_graph(tmp_viewer):
    """A loopback bind does not stop a name that re-resolves to loopback."""
    import http.client

    port = _free_port()
    server = ViewerServer(
        tmp_viewer, host="127.0.0.1", port=port,
        limits=ServerLimits(max_requests=20, request_timeout=2.0),
    ).start()
    try:
        def status_for(host: str) -> int:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2.0)
            conn.putrequest("GET", "/", skip_host=True)
            conn.putheader("Host", host)
            conn.endheaders()
            response = conn.getresponse()
            response.read()
            conn.close()
            return response.status

        assert status_for(f"127.0.0.1:{port}") == 200
        assert status_for("attacker.example") == 421
        assert status_for("evil.test:80") == 421
    finally:
        server.stop()


def test_the_response_forbids_framing(tmp_viewer):
    import http.client

    port = _free_port()
    server = ViewerServer(
        tmp_viewer, host="127.0.0.1", port=port,
        limits=ServerLimits(max_requests=5, request_timeout=2.0),
    ).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2.0)
        conn.request("GET", "/")
        response = conn.getresponse()
        policy = response.getheader("Content-Security-Policy") or ""
        response.read()
        conn.close()
        assert "frame-ancestors 'none'" in policy
    finally:
        server.stop()


def test_stop_closes_the_listening_socket_even_when_the_thread_outlives_the_join(tmp_viewer, monkeypatch):
    """The close used to sit AFTER the raise, so the one case the raise exists
    for was also the case that leaked the port.

    Forced rather than raced. An earlier version of this test connected an idle
    client and hoped the handler would outlast the shutdown budget; with a short
    request timeout it never did, so the test passed against the original broken
    ordering and proved nothing. Making the join appear to fail is deterministic
    and tests exactly the branch that was wrong.
    """
    import socket as _socket
    import threading as _threading

    port = _free_port()
    server = ViewerServer(
        tmp_viewer, host="127.0.0.1", port=port,
        limits=ServerLimits(max_requests=5, request_timeout=1.0, poll_interval=0.05),
    ).start()
    try:
        monkeypatch.setattr(_threading.Thread, "join", lambda self, timeout=None: None)
        with pytest.raises(RuntimeError):
            server.stop()
    finally:
        monkeypatch.undo()

    probe = _socket.socket()
    probe.settimeout(1.0)
    leaked = probe.connect_ex(("127.0.0.1", port)) == 0
    probe.close()
    assert not leaked, "stop() raised and left the listening socket bound"


def test_the_shutdown_budget_is_never_shorter_than_a_connection_may_live():
    """A single idle client must not be able to turn shutdown into a failure.

    The server is single threaded, so one connected client holds the handler for
    up to request_timeout. The original budget was poll_interval * 20 + 5, which
    at the shipped defaults is 9 seconds against a 30 second request timeout, so
    an ordinary browser pre-connect made shutdown raise. Asserted on the value
    rather than by waiting, so it is fast and cannot pass by luck.
    """
    shipped = ServerLimits().validated()
    assert shipped.request_timeout == DEFAULT_REQUEST_TIMEOUT
    assert shipped.shutdown_budget > shipped.request_timeout, (
        f"the shipped defaults give a {shipped.shutdown_budget}s budget for a "
        f"{shipped.request_timeout}s request timeout"
    )
    for timeout in (0.5, 5.0, 30.0, 120.0):
        limits = ServerLimits(request_timeout=timeout, poll_interval=0.05).validated()
        assert limits.shutdown_budget > timeout, timeout

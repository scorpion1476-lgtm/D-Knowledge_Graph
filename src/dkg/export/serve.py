"""Bounded loopback server for the generated offline viewer (R-23).

A viewer produced on a headless machine has to be opened somehow. Copying the
file off the machine is one answer; serving it over a port forward is the other,
and this module is the second answer written so that it cannot become a way to
expose the graph.

What it does, and what it deliberately refuses to do:

- Loopback only. The bind address is resolved by ``resolve_bind_host`` and every
  address that is not a loopback IP literal is refused with a clear error before
  a socket is created. ``0.0.0.0``, ``::``, a LAN address, and a hostname that
  might resolve anywhere are all refused. There is no flag that turns this off.
- One explicit port. The port is always passed in; nothing picks one silently.
- Exactly one file. The viewer's bytes are read once when the server starts and
  are held in memory. No request ever touches the filesystem, so there is no
  path to traverse: an unknown path is a 404 and nothing else can be served.
- Bounded. A per-connection socket timeout, a request-size cap, and a maximum
  number of served requests, after which the server stops on its own.
- Clean shutdown. ``stop`` closes the listening socket in a ``finally``, so the
  socket is released even in the one case the raise exists for, and raises if
  the serving thread outlives the join, so a leaked thread is a loud failure
  rather than a quiet one.
- One name. A request whose ``Host`` header is not the address this server was
  bound to is refused, because a loopback bind alone does not stop DNS
  rebinding: a page on another origin whose name re-resolves to loopback would
  otherwise be same-origin with this server and could read the whole graph.

This is not a general web server and must never grow into one. It makes no
outbound connection of its own, and the response carries a content-security
policy that forbids the page from loading anything and from being framed, which
is the air-gap default enforced at a second layer. One caveat worth stating
rather than hiding: the standard library's ``HTTPServer.server_bind`` performs a
reverse lookup on the bound address to compute its own server name, which on
some hosts touches a local resolver.
"""

from __future__ import annotations

import ipaddress
import socket
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit

# The only routes that return the viewer. Everything else is a 404.
VIEWER_ROUTES = ("/", "/index.html")

# Loopback names accepted as an alias for the loopback IP literal. A general
# hostname is never resolved, because a name can point at any address.
_LOOPBACK_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}

DEFAULT_MAX_REQUESTS = 100
DEFAULT_REQUEST_TIMEOUT = 30.0
DEFAULT_MAX_REQUEST_BYTES = 65536
DEFAULT_POLL_INTERVAL = 0.2

# Nothing may be loaded from anywhere; the page is self-contained by
# construction, and this says so to the browser as well.
_CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "form-action 'none'; base-uri 'none'; frame-ancestors 'none'"
)


class NonLoopbackBindError(ValueError):
    """Raised when a bind address is not a loopback address."""


@dataclass(frozen=True)
class ServerLimits:
    """Every bound the server enforces, in one place so none is implicit."""

    max_requests: int = DEFAULT_MAX_REQUESTS
    request_timeout: float = DEFAULT_REQUEST_TIMEOUT
    max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES
    poll_interval: float = DEFAULT_POLL_INTERVAL

    def validated(self) -> ServerLimits:
        if self.max_requests < 1:
            raise ValueError("max_requests must be at least 1")
        if self.request_timeout <= 0:
            raise ValueError("request_timeout must be positive")
        if self.max_request_bytes < 1:
            raise ValueError("max_request_bytes must be at least 1")
        if self.poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        return self

    @property
    def shutdown_budget(self) -> float:
        """How long ``stop`` waits for the serving thread before giving up.

        It has to exceed ``request_timeout``. This server is single threaded, so
        one connected client holds the handler for up to that long, and a budget
        derived from ``poll_interval`` alone was shorter than the default
        request timeout: an ordinary browser pre-connect was enough to turn a
        clean shutdown into a raised error.
        """
        return max(self.poll_interval * 20.0, self.request_timeout) + 5.0


def resolve_bind_host(host: str) -> str:
    """Return the loopback IP literal to bind, or refuse.

    Accepts a loopback IP literal (``127.0.0.1``, any ``127.0.0.0/8`` address,
    ``::1``) and the loopback names. Refuses everything else, including the
    wildcard addresses and any hostname, because a hostname can resolve to a
    routable address and a routable bind is exactly what this must never do.
    """
    text = (host or "").strip()
    if not text:
        raise NonLoopbackBindError("refusing to serve: no bind address was given; use a loopback address such as 127.0.0.1")
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    lowered = text.lower()
    if lowered in _LOOPBACK_NAMES:
        return "127.0.0.1"
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError as exc:
        raise NonLoopbackBindError(
            f"refusing to serve on {host!r}: only a loopback IP literal (for example 127.0.0.1 or ::1) "
            f"may be bound, and a hostname is never resolved because it can point at a routable address"
        ) from exc
    if not address.is_loopback:
        raise NonLoopbackBindError(
            f"refusing to serve on {host!r}: it is not a loopback address, and this server never binds a "
            f"routable interface"
        )
    return str(address)


def _address_family(host: str) -> int:
    return socket.AF_INET6 if ipaddress.ip_address(host).version == 6 else socket.AF_INET


def _display_url(host: str, port: int) -> str:
    shown = f"[{host}]" if ipaddress.ip_address(host).version == 6 else host
    return "http" + "://" + f"{shown}:{port}/"


class _ViewerHTTPServer(HTTPServer):
    """The listening socket, plus the single payload every response is built from."""

    allow_reuse_address = True

    def __init__(self, address, handler, *, payload: bytes, limits: ServerLimits, family: int):
        self.address_family = family
        self.dkg_payload = payload
        self.dkg_limits = limits
        self.dkg_served = 0
        # Every spelling of the address this server was actually bound to. A
        # request naming anything else is a rebinding attempt, not a local user.
        host, port = address[0], address[1]
        bracketed = f"[{host}]" if ipaddress.ip_address(host).version == 6 else host
        self.dkg_permitted_hosts = {
            f"{bracketed}:{port}".lower(),
            bracketed.lower(),
            f"localhost:{port}",
            "localhost",
        }
        super().__init__(address, handler)


class _ViewerHandler(BaseHTTPRequestHandler):
    """Serves one in-memory document at two routes and refuses everything else."""

    server_version = "dkg-viewer/0.1"
    sys_version = ""
    protocol_version = "HTTP/1.0"

    # BaseHTTPRequestHandler assigns this per request, but only once the request
    # line has actually been read: if the socket times out or the peer goes away
    # first, the base class returns without ever setting it. A class-level
    # default means the attribute always exists, so reading it after the fact
    # cannot raise. Without it, an idle connection that timed out crashed the
    # handler thread with AttributeError instead of closing quietly.
    raw_requestline: bytes = b""

    @property
    def _limits(self) -> ServerLimits:
        limits: ServerLimits = self.server.dkg_limits  # type: ignore[attr-defined]
        return limits

    def setup(self) -> None:
        # socketserver declares `timeout` as a class attribute but reads it off
        # the instance, and the timeout is per-server here rather than per-class,
        # so the per-instance assignment is deliberate. The ignore is scoped to
        # this line so it cannot mask anything else.
        self.timeout = self._limits.request_timeout  # type: ignore[misc]
        super().setup()

    def log_message(self, fmt, *args) -> None:
        # No access log: this is a local viewer, not a service, and a log line
        # per request is noise on the operator's terminal. Not telemetry either.
        return None

    def _send(self, code: int, body: bytes, content_type: str, *, length: int | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        # ``length`` lets HEAD report the document's real size while sending no
        # body, which is what a HEAD response is supposed to do.
        self.send_header("Content-Length", str(len(body) if length is None else length))
        self.send_header("Content-Security-Policy", _CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _text(self, code: int, message: str) -> None:
        self._send(code, message.encode("utf-8"), "text/plain; charset=utf-8")

    def _over_limit(self) -> bool:
        cap = self._limits.max_request_bytes
        if len(self.raw_requestline) > cap:
            self._text(414, "request line is longer than the configured cap")
            return True
        header_bytes = sum(len(str(k)) + len(str(v)) + 4 for k, v in self.headers.items())
        if len(self.raw_requestline) + header_bytes > cap:
            self._text(431, "request headers are larger than the configured cap")
            return True
        raw_length = self.headers.get("Content-Length")
        if raw_length is not None:
            try:
                length = int(raw_length)
            except ValueError:
                self._text(400, "Content-Length is not a number")
                return True
            if length > cap:
                self._text(413, "request body is larger than the configured cap")
                return True
        return False

    def handle_one_request(self) -> None:
        """Count every request, including the ones the base class answers itself.

        Counting inside do_GET, do_HEAD, and the method refusal missed anything
        http.server rejects before dispatch: an unknown verb gets a 501 straight
        from the base class, so an adversarial review sent ten of them against a
        budget of three and the counter stayed at zero while the thread served
        every one. The budget has to be charged where every request passes.

        Counted after the fact, and only when a request line actually arrived. A
        bare TCP connection that sends nothing is not a served request, and
        charging it would let a browser's pre-connect, or a caller's readiness
        probe, exhaust the budget before the real request was ever made.
        """
        super().handle_one_request()
        if self.raw_requestline:
            self.server.dkg_served += 1  # type: ignore[attr-defined]

    def _host_is_permitted(self) -> bool:
        """Reject a Host header that is not the address this server was given.

        Binding loopback stops a routed connection but not DNS rebinding: a page
        on another origin whose name re-resolves to 127.0.0.1 would otherwise be
        same-origin with this server and could read the whole exported graph.
        The bound host and port are the only names this server answers to.
        """
        sent = (self.headers.get("Host") or "").strip()
        if not sent:
            # HTTP/1.0 permits no Host header, and the browsers and tools that
            # open a local viewer always send one, so an absent header is only
            # ever a hand-made request. Allow it; there is no origin to confuse.
            return True
        permitted: set[str] = self.server.dkg_permitted_hosts  # type: ignore[attr-defined]
        return sent.lower() in permitted

    def _route(self) -> str:
        # Only the path is considered. The path is never joined to a directory
        # and never opened, so "/../../etc/passwd" is simply an unknown route.
        return urlsplit(self.path).path or "/"

    def do_GET(self) -> None:
        if self._over_limit():
            return
        if not self._host_is_permitted():
            self._text(421, "this server answers only to the address it was bound to")
            return
        if self._route() not in VIEWER_ROUTES:
            self._text(404, "not found: this server serves one viewer document at / and nothing else")
            return
        self._send(200, self.server.dkg_payload, "text/html; charset=utf-8")  # type: ignore[attr-defined]

    def do_HEAD(self) -> None:
        if self._over_limit():
            return
        if not self._host_is_permitted():
            self._text(421, "this server answers only to the address it was bound to")
            return
        if self._route() not in VIEWER_ROUTES:
            self._text(404, "not found")
            return
        payload: bytes = self.server.dkg_payload  # type: ignore[attr-defined]
        self._send(200, b"", "text/html; charset=utf-8", length=len(payload))

    def _refuse_method(self) -> None:
        if self._over_limit():
            return
        self.send_response(405)
        self.send_header("Allow", "GET, HEAD")
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()

    # Every other method is refused. http.server dispatches on the method name,
    # so a method with no do_* attribute already gets a 501; naming them here
    # makes the refusal explicit and returns the correct Allow header.
    do_POST = _refuse_method
    do_PUT = _refuse_method
    do_DELETE = _refuse_method
    do_PATCH = _refuse_method
    do_OPTIONS = _refuse_method


class ViewerServer:
    """A bounded, loopback-only server for exactly one generated viewer file."""

    def __init__(
        self,
        viewer_path: Path | str,
        *,
        port: int,
        host: str = "127.0.0.1",
        limits: ServerLimits | None = None,
    ) -> None:
        self.viewer_path = Path(viewer_path)
        if not self.viewer_path.is_file():
            raise FileNotFoundError(f"no viewer file to serve at {self.viewer_path}")
        # Refuse before anything else so a bad address never reaches a socket.
        self.host = resolve_bind_host(host)
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ValueError(f"an explicit port between 1 and 65535 is required, got {port!r}")
        self.port = port
        self.limits = (limits or ServerLimits()).validated()
        self._httpd: _ViewerHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._served_at_stop = 0

    @property
    def url(self) -> str:
        return _display_url(self.host, self.port)

    @property
    def requests_served(self) -> int:
        """How many requests were handled, still readable after stop()."""
        return self._httpd.dkg_served if self._httpd is not None else self._served_at_stop

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> ViewerServer:
        if self._httpd is not None:
            raise RuntimeError("this viewer server has already been started")
        payload = self.viewer_path.read_bytes()
        self._httpd = _ViewerHTTPServer(
            (self.host, self.port),
            _ViewerHandler,
            payload=payload,
            limits=self.limits,
            family=_address_family(self.host),
        )
        self._httpd.timeout = self.limits.poll_interval
        self._stop.clear()
        self._thread = threading.Thread(target=self._serve, name="dkg-viewer-server", daemon=True)
        self._thread.start()
        return self

    def _serve(self) -> None:
        httpd = self._httpd
        if httpd is None:
            return
        while not self._stop.is_set() and httpd.dkg_served < self.limits.max_requests:
            try:
                httpd.handle_request()
            except OSError:
                break

    def wait(self, timeout: float | None = None) -> None:
        """Block until the request budget runs out, the server is stopped, or the timeout."""
        if self._thread is not None:
            self._thread.join(timeout)

    def stop(self) -> None:
        """Stop serving and close the socket, failing loudly if the thread survives.

        The close happens in a ``finally`` so it cannot be skipped by the raise.
        It used to sit after it, which meant the one case the raise exists for,
        a handler that outlives the budget, was also the case that leaked the
        listening socket and left the port bound: an adversarial review
        connected to it after ``stop()`` had already raised. The thread handle
        was cleared first as well, so a second ``stop()`` returned cleanly while
        the thread was still alive, reporting success for a server that had not
        stopped.

        The budget also has to exceed the per-connection timeout. This server is
        single threaded, so one idle connected client (a browser pre-connecting
        is enough) holds the handler for ``request_timeout``. A budget shorter
        than that made a routine client into a shutdown failure.
        """
        self._stop.set()
        thread, self._thread = self._thread, None
        httpd, self._httpd = self._httpd, None
        try:
            if thread is not None:
                budget = self.limits.shutdown_budget
                thread.join(timeout=budget)
                if thread.is_alive():
                    raise RuntimeError(
                        "the viewer server thread did not stop within its shutdown budget of "
                        f"{budget:g}s; the listening socket has been closed regardless"
                    )
        finally:
            if httpd is not None:
                self._served_at_stop = httpd.dkg_served
                httpd.server_close()

    def __enter__(self) -> ViewerServer:
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

"""Inbound request guard for the served HTTP MCP surface.

The MCP surface is a trust boundary. Read-only with respect to the graph is not
sufficient on its own, because the threat here is not that a caller writes to
the database; it is that something the user did not intend gets to *ask*.

The specific attack this closes: a page in an ordinary browser can issue a
cross-origin POST to ``http://127.0.0.1:<port>/rpc``. From the server's point of
view that request arrives from a loopback peer, because the browser runs on the
same machine. Authorising on the peer address alone therefore hands the local
graph to any web page the user happens to visit. A loopback peer is not a
trusted peer.

Four independent checks run before any handler, in this order, and the first
failure is returned:

1. **Host** must match an expected authority. Without this, a DNS name that
   resolves to 127.0.0.1 (a rebind) reaches the server under an attacker's
   origin, which makes the *responses* readable to that origin.
2. **Origin** (and ``Referer`` as a fallback for clients that send only that)
   must be absent or explicitly allow-listed. A normal MCP client is not a
   browser and sends neither. A browser always attaches ``Origin`` to a
   cross-origin POST, so requiring absence-or-allow-listed is what actually
   separates the two. The allow-list is empty by default: nothing browser-driven
   is permitted unless an operator names it.
3. **Content type** on the JSON-RPC path must be JSON. ``text/plain``,
   ``application/x-www-form-urlencoded`` and ``multipart/form-data`` are exactly
   the three types a browser can send with no CORS preflight, so refusing them
   removes the no-preflight path. This is defence in depth behind the Origin
   check, not a substitute for it.
4. **Authorisation** must come from a credential, never from the peer address.
   A non-loopback bind always requires a token. A loopback bind with no token
   configured is refused unless the operator has explicitly opted in.

Every check is a pure function of the request metadata, so the whole policy is
testable without binding a socket.
"""

from __future__ import annotations

from dataclasses import dataclass

# The three content types a browser form or a `fetch` with a simple request can
# send without triggering a CORS preflight. Anything on this list must not reach
# the JSON-RPC handler.
_CORS_SAFELISTED_CONTENT_TYPES = (
    "text/plain",
    "application/x-www-form-urlencoded",
    "multipart/form-data",
)

_LOOPBACK_PEERS = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})

# Names that resolve to the loopback interface and are therefore legitimate
# values of the Host header for a loopback bind.
_LOOPBACK_HOST_NAMES = ("127.0.0.1", "localhost", "::1", "[::1]")


@dataclass(frozen=True)
class GuardDecision:
    """The outcome of the guard. ``allowed`` is the only field a caller acts on."""

    allowed: bool
    status: int = 200
    reason: str = ""

    @property
    def denied(self) -> bool:
        return not self.allowed


ALLOWED = GuardDecision(allowed=True)


def is_loopback_bind(bind: str) -> bool:
    """True when ``bind`` only accepts connections from this machine.

    ``0.0.0.0`` and ``::`` are explicitly NOT loopback: they accept from every
    interface, which is the case that must always carry a token.
    """
    host = (bind or "").strip().strip("[]").lower()
    if host in ("127.0.0.1", "::1", "localhost"):
        return True
    # Any address in 127.0.0.0/8 is loopback.
    return host.startswith("127.")


def is_loopback_peer(peer: str) -> bool:
    """True when the connecting peer is on this machine.

    Used only as an additional constraint on the opted-in no-token mode. It is
    never on its own a reason to authorise, which is the whole point of F-19.
    """
    return (peer or "").strip().lower() in _LOOPBACK_PEERS


def expected_authorities(bind: str, port: int) -> frozenset[str]:
    """The set of Host header values this server answers to.

    For a loopback bind that is every spelling of loopback at the served port.
    For any other bind it is that address at that port; an operator serving
    under a hostname adds it through ``http_allowed_hosts``.
    """
    names: list[str] = []
    if is_loopback_bind(bind):
        names.extend(_LOOPBACK_HOST_NAMES)
    else:
        host = (bind or "").strip()
        # noqa S104: this compares against the wildcard addresses in order to
        # REFUSE to derive an authority from them. It binds nothing.
        if host in ("0.0.0.0", "::", "[::]"):  # noqa: S104
            # A wildcard bind answers on every interface, so no single authority
            # can be derived from it. The operator must name the hosts.
            return frozenset()
        names.append(host)
    out: set[str] = set()
    for name in names:
        out.add(f"{name}:{port}")
        # Port 80 may be elided by the client; only then is a bare name valid.
        if int(port) == 80:
            out.add(name)
    return frozenset(out)


def _normalise_host(raw: str) -> str:
    return (raw or "").strip().lower()


def check_host(host_header: str, allowed: frozenset[str] | set[str]) -> GuardDecision:
    """Reject a missing or unexpected Host header.

    A rebound DNS name reaches the socket exactly like a direct request; the
    Host header is the only place the difference is visible.
    """
    got = _normalise_host(host_header)
    if not got:
        return GuardDecision(False, 403, "missing Host header")
    if got not in {_normalise_host(a) for a in allowed}:
        return GuardDecision(False, 403, "Host header not in the allow-list")
    return ALLOWED


def _origin_of(url: str) -> str:
    """Reduce a Referer URL to its origin so it can be compared to the list."""
    raw = (url or "").strip()
    if not raw:
        return ""
    scheme, sep, rest = raw.partition("://")
    if not sep:
        return raw.lower()
    authority = rest.split("/", 1)[0]
    return f"{scheme.lower()}://{authority.lower()}"


def check_origin(
    origin_header: str, referer_header: str, allowed_origins: frozenset[str] | set[str]
) -> GuardDecision:
    """Reject a browser-attached Origin that no operator named.

    Absent Origin is allowed: that is the ordinary non-browser MCP client. This
    is not a loophole a page can take, because a browser will not omit Origin on
    a cross-origin POST; ``fetch`` and ``XMLHttpRequest`` both attach it and a
    page cannot override the header.
    """
    permitted = {_normalise_host(o) for o in allowed_origins if o}
    origin = _normalise_host(origin_header)
    if origin:
        # "null" is what a sandboxed iframe or a file:// page sends. It is never
        # a meaningful identity, so it is never allow-listable.
        if origin == "null":
            return GuardDecision(False, 403, "Origin 'null' is never permitted")
        if origin not in permitted:
            return GuardDecision(False, 403, "Origin not in the allow-list")
        return ALLOWED
    referer_origin = _origin_of(referer_header)
    if referer_origin and referer_origin not in permitted:
        return GuardDecision(False, 403, "Referer origin not in the allow-list")
    return ALLOWED


def check_content_type(content_type: str) -> GuardDecision:
    """Require JSON on the JSON-RPC path.

    Removes the no-preflight path: the three CORS-safelisted types are refused,
    so a cross-origin POST must ask permission first, and the server never
    grants it because it emits no CORS headers at all.
    """
    raw = (content_type or "").split(";", 1)[0].strip().lower()
    if not raw:
        return GuardDecision(False, 415, "missing Content-Type; application/json required")
    if raw in _CORS_SAFELISTED_CONTENT_TYPES:
        return GuardDecision(
            False, 415, f"Content-Type {raw} is not permitted; application/json required"
        )
    if raw != "application/json" and not raw.endswith("+json"):
        return GuardDecision(False, 415, "Content-Type must be application/json")
    return ALLOWED


def check_authorisation(
    *,
    authorization_header: str,
    peer: str,
    configured_token: str,
    issued_tokens: frozenset[str] | set[str],
    client_secret_configured: bool,
    bind: str,
    allow_unauthenticated_loopback: bool,
) -> GuardDecision:
    """Authorise by credential. The peer address is never a credential.

    The no-token path is deliberately narrow: the bind must be loopback, the
    peer must be loopback, AND the operator must have opted in. Two of those
    three were previously true by default, which is what made a visited web page
    able to drive the server.
    """
    header = (authorization_header or "").strip()
    if header.startswith("Bearer "):
        supplied = header[len("Bearer ") :].strip()
        if not supplied:
            return GuardDecision(False, 401, "empty bearer token")
        if configured_token and _constant_time_equal(supplied, configured_token):
            return ALLOWED
        if any(_constant_time_equal(supplied, t) for t in issued_tokens):
            return ALLOWED
        return GuardDecision(False, 401, "bearer token not recognised")

    if configured_token or client_secret_configured:
        return GuardDecision(False, 401, "a bearer token is required")

    # No credential is configured at all. This is the case F-19 is about.
    if not is_loopback_bind(bind):
        return GuardDecision(
            False,
            401,
            "a bearer token is required for any non-loopback bind",
        )
    if not allow_unauthenticated_loopback:
        return GuardDecision(
            False,
            401,
            "unauthenticated access is off by default; set a token, or opt in "
            "explicitly with DKG_MCP_ALLOW_UNAUTHENTICATED_LOOPBACK=1 having "
            "understood that any page in a browser on this machine is also a "
            "loopback caller",
        )
    if not is_loopback_peer(peer):
        return GuardDecision(False, 401, "unauthenticated access is limited to loopback peers")
    return ALLOWED


def _constant_time_equal(a: str, b: str) -> bool:
    import hmac

    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def guard_request(
    *,
    path: str,
    host_header: str,
    origin_header: str,
    referer_header: str,
    content_type: str,
    authorization_header: str,
    peer: str,
    allowed_authorities: frozenset[str] | set[str],
    allowed_origins: frozenset[str] | set[str],
    configured_token: str,
    issued_tokens: frozenset[str] | set[str],
    client_secret_configured: bool,
    bind: str,
    allow_unauthenticated_loopback: bool,
    require_auth: bool = True,
    require_json: bool = True,
) -> GuardDecision:
    """Run every check in order and return the first failure.

    ``require_auth`` is false only for the unauthenticated liveness probe and
    the token-exchange endpoint, which cannot require the credential they exist
    to establish. Host and Origin still apply to both.
    """
    decision = check_host(host_header, allowed_authorities)
    if decision.denied:
        return decision
    decision = check_origin(origin_header, referer_header, allowed_origins)
    if decision.denied:
        return decision
    if require_json:
        decision = check_content_type(content_type)
        if decision.denied:
            return decision
    if require_auth:
        decision = check_authorisation(
            authorization_header=authorization_header,
            peer=peer,
            configured_token=configured_token,
            issued_tokens=issued_tokens,
            client_secret_configured=client_secret_configured,
            bind=bind,
            allow_unauthenticated_loopback=allow_unauthenticated_loopback,
        )
        if decision.denied:
            return decision
    return ALLOWED


def startup_refusal(
    *, bind: str, configured_token: str, client_secret_configured: bool
) -> str:
    """Return why the server must not start, or an empty string when it may.

    Refusing at startup is better than refusing per request: an operator who
    binds to every interface with no credential has made a mistake that a 401
    on each request would let them keep making.
    """
    if is_loopback_bind(bind):
        return ""
    if configured_token or client_secret_configured:
        return ""
    return (
        f"refusing to serve the HTTP MCP surface on {bind!r} without a credential: "
        "a non-loopback bind is reachable from other machines, so set the bearer "
        "token environment variable, or set DKG_MCP_CLIENT_SECRET, or bind to "
        "127.0.0.1"
    )

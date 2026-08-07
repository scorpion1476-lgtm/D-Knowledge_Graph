"""F-19: the served HTTP surface must not authorise a caller by peer address.

The defect this file pins down: in the no-token mode the server authorised on
``self.client_address[0] in ("127.0.0.1", "::1")``. A page in an ordinary
browser on the same machine connects from 127.0.0.1, so any site the user
visited could POST to the local JSON-RPC endpoint and read the graph back.

Two layers of test:

* Policy tests call ``http_guard`` directly. They need no socket, so the
  security policy is verified even in an environment that forbids binding.
* End-to-end tests bind a real loopback socket and drive the actual server,
  including the exact attack: a same-machine caller, with no token, sent the
  way a browser sends it. They skip only on a socket-bind refusal.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from dkg.core.config import MCPConfig
from dkg.core.errors import ConfigError
from dkg.mcp.http_guard import (
    check_authorisation,
    check_content_type,
    check_host,
    check_origin,
    expected_authorities,
    guard_request,
    is_loopback_bind,
    startup_refusal,
)
from dkg.mcp.server_http import serve_http

# ---------------------------------------------------------------------------
# Policy layer: no socket required.
# ---------------------------------------------------------------------------

_LOOPBACK_GUARD_ARGS = dict(
    path="/rpc",
    host_header="127.0.0.1:8765",
    origin_header="",
    referer_header="",
    content_type="application/json",
    authorization_header="",
    peer="127.0.0.1",
    allowed_authorities=expected_authorities("127.0.0.1", 8765),
    allowed_origins=frozenset(),
    configured_token="",
    issued_tokens=frozenset(),
    client_secret_configured=False,
    bind="127.0.0.1",
    allow_unauthenticated_loopback=False,
)


def test_loopback_peer_without_token_is_rejected_by_default():
    """The core F-19 claim, at the policy layer."""
    decision = guard_request(**_LOOPBACK_GUARD_ARGS)
    assert decision.denied
    assert decision.status == 401
    assert "opt in" in decision.reason


def test_loopback_peer_without_token_allowed_only_after_explicit_opt_in():
    args = dict(_LOOPBACK_GUARD_ARGS, allow_unauthenticated_loopback=True)
    assert guard_request(**args).allowed


def test_opt_in_still_does_not_admit_a_non_loopback_peer():
    """The opt-in relaxes the credential, never the reachability."""
    args = dict(
        _LOOPBACK_GUARD_ARGS,
        allow_unauthenticated_loopback=True,
        peer="10.0.0.7",
    )
    decision = guard_request(**args)
    assert decision.denied
    assert decision.status == 401


def test_browser_origin_is_rejected_even_with_the_opt_in_and_a_loopback_peer():
    """The attack in full: same machine, opted in, but driven from a page."""
    args = dict(
        _LOOPBACK_GUARD_ARGS,
        allow_unauthenticated_loopback=True,
        origin_header="https://evil.example",
    )
    decision = guard_request(**args)
    assert decision.denied
    assert decision.status == 403
    assert "Origin" in decision.reason


def test_browser_origin_is_rejected_even_when_the_caller_holds_a_valid_token():
    """A stolen-context request must not pass merely because a token exists."""
    args = dict(
        _LOOPBACK_GUARD_ARGS,
        configured_token="correct-token",
        authorization_header="Bearer correct-token",
        origin_header="https://evil.example",
    )
    assert guard_request(**args).denied


def test_named_origin_is_admitted_when_an_operator_allow_lists_it():
    args = dict(
        _LOOPBACK_GUARD_ARGS,
        configured_token="correct-token",
        authorization_header="Bearer correct-token",
        origin_header="https://console.internal",
        allowed_origins={"https://console.internal"},
    )
    assert guard_request(**args).allowed


def test_origin_null_is_never_allow_listable():
    """A sandboxed iframe or a file:// page sends null. It is not an identity."""
    decision = check_origin("null", "", {"null"})
    assert decision.denied


def test_referer_origin_is_checked_when_origin_is_absent():
    decision = check_origin("", "https://evil.example/page.html", frozenset())
    assert decision.denied
    assert "Referer" in decision.reason


def test_absent_origin_and_referer_pass_because_a_real_client_sends_neither():
    assert check_origin("", "", frozenset()).allowed


@pytest.mark.parametrize(
    "content_type",
    ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data"],
)
def test_cors_safelisted_content_types_are_refused(content_type):
    """These three are exactly what a page can POST with no preflight."""
    decision = check_content_type(content_type)
    assert decision.denied
    assert decision.status == 415


def test_json_content_type_is_accepted_with_and_without_a_charset():
    assert check_content_type("application/json").allowed
    assert check_content_type("application/json; charset=utf-8").allowed


def test_missing_content_type_is_refused():
    assert check_content_type("").denied


def test_rebound_host_is_rejected():
    """A name resolving to 127.0.0.1 reaches the socket identically."""
    allowed = expected_authorities("127.0.0.1", 8765)
    assert check_host("rebind.evil.example:8765", allowed).denied
    assert check_host("127.0.0.1:8765", allowed).allowed
    assert check_host("localhost:8765", allowed).allowed


def test_missing_host_is_rejected():
    assert check_host("", expected_authorities("127.0.0.1", 8765)).denied


def test_host_on_the_wrong_port_is_rejected():
    assert check_host("127.0.0.1:9999", expected_authorities("127.0.0.1", 8765)).denied


def test_wildcard_bind_derives_no_authority_of_its_own():
    """0.0.0.0 answers on every interface, so the operator must name the hosts."""
    assert expected_authorities("0.0.0.0", 8765) == frozenset()
    assert not is_loopback_bind("0.0.0.0")
    assert not is_loopback_bind("::")
    assert is_loopback_bind("127.0.0.1")
    assert is_loopback_bind("127.0.0.53")


def test_non_loopback_bind_always_requires_a_token():
    decision = check_authorisation(
        authorization_header="",
        peer="10.0.0.7",
        configured_token="",
        issued_tokens=frozenset(),
        client_secret_configured=False,
        bind="0.0.0.0",
        allow_unauthenticated_loopback=True,  # cannot rescue a wide bind
    )
    assert decision.denied
    assert "non-loopback" in decision.reason


def test_wrong_and_empty_bearer_tokens_are_rejected():
    common = dict(
        peer="127.0.0.1",
        configured_token="correct-token",
        issued_tokens=frozenset(),
        client_secret_configured=False,
        bind="127.0.0.1",
        allow_unauthenticated_loopback=True,
    )
    assert check_authorisation(authorization_header="Bearer wrong", **common).denied
    assert check_authorisation(authorization_header="Bearer ", **common).denied
    assert check_authorisation(authorization_header="Bearer correct-token", **common).allowed


def test_server_refuses_to_start_on_a_wide_bind_with_no_credential():
    refusal = startup_refusal(bind="0.0.0.0", configured_token="", client_secret_configured=False)
    assert refusal
    assert "127.0.0.1" in refusal
    assert not startup_refusal(bind="0.0.0.0", configured_token="t", client_secret_configured=False)
    assert not startup_refusal(bind="127.0.0.1", configured_token="", client_secret_configured=False)


# ---------------------------------------------------------------------------
# End-to-end layer: a real socket, the real server.
# ---------------------------------------------------------------------------


def _find_port() -> int:
    import socket

    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
    except (PermissionError, OSError) as e:
        s.close()
        pytest.skip(f"socket bind not permitted in this environment: {e!r}")
    port = s.getsockname()[1]
    s.close()
    return port


def _start(db, cfg, **mcp_kwargs) -> int:
    port = _find_port()
    cfg.mcp = MCPConfig(
        http_enabled=True,
        http_bind="127.0.0.1",
        http_port=port,
        http_bearer_token_env="DKG_MCP_TOKEN",
        **mcp_kwargs,
    )
    t = threading.Thread(
        target=serve_http,
        args=(db,),
        kwargs={"host": "127.0.0.1", "port": port, "cfg": cfg},
        daemon=True,
    )
    t.start()
    for _ in range(100):
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/healthz",
                headers={"Host": f"127.0.0.1:{port}"},
            )
            with urllib.request.urlopen(req, timeout=1) as r:
                if r.status == 200:
                    return port
        except urllib.error.HTTPError:
            return port  # bound and answering, which is all we waited for
        except Exception:
            time.sleep(0.05)
    raise RuntimeError("server did not start")


def _rpc(port: int, *, headers: dict) -> int:
    """POST tools/list and return the HTTP status the server chose."""
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/rpc",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def test_end_to_end_same_machine_caller_is_rejected_without_a_token(db, cfg):
    """The exact F-19 scenario, over a real loopback socket.

    No token is configured. The caller is on this machine, which is what a
    browser page would also be. It must be refused.
    """
    port = _start(db, cfg)
    status = _rpc(
        port,
        headers={"Content-Type": "application/json", "Host": f"127.0.0.1:{port}"},
    )
    assert status == 401, "a same-machine caller with no token must be refused"


def test_end_to_end_browser_style_request_is_rejected_even_when_opted_in(db, cfg):
    """Opted in to unauthenticated loopback, but driven from a page: refused."""
    port = _start(db, cfg, http_allow_unauthenticated_loopback=True)
    status = _rpc(
        port,
        headers={
            "Content-Type": "application/json",
            "Host": f"127.0.0.1:{port}",
            "Origin": "https://evil.example",
        },
    )
    assert status == 403


def test_end_to_end_form_post_needing_no_preflight_is_rejected(db, cfg):
    """The no-preflight path: a form-encoded POST from the same machine."""
    port = _start(db, cfg, http_allow_unauthenticated_loopback=True)
    status = _rpc(
        port,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Host": f"127.0.0.1:{port}",
        },
    )
    assert status == 415


def test_end_to_end_rebound_host_header_is_rejected(db, cfg):
    port = _start(db, cfg, http_allow_unauthenticated_loopback=True)
    status = _rpc(
        port,
        headers={"Content-Type": "application/json", "Host": "rebind.evil.example"},
    )
    assert status == 403


def test_end_to_end_opted_in_local_client_still_works(db, cfg):
    """The guard must not break the legitimate local client it protects."""
    port = _start(db, cfg, http_allow_unauthenticated_loopback=True)
    status = _rpc(
        port,
        headers={"Content-Type": "application/json", "Host": f"127.0.0.1:{port}"},
    )
    assert status == 200


def test_end_to_end_bearer_token_is_accepted_and_a_wrong_one_is_not(db, cfg, monkeypatch):
    monkeypatch.setenv("DKG_MCP_TOKEN", "correct-token-value")
    port = _start(db, cfg)
    ok = _rpc(
        port,
        headers={
            "Content-Type": "application/json",
            "Host": f"127.0.0.1:{port}",
            "Authorization": "Bearer correct-token-value",
        },
    )
    bad = _rpc(
        port,
        headers={
            "Content-Type": "application/json",
            "Host": f"127.0.0.1:{port}",
            "Authorization": "Bearer not-the-token",
        },
    )
    assert (ok, bad) == (200, 401)


def test_end_to_end_healthz_rejects_a_rebound_host(db, cfg):
    """Even the liveness probe must not answer under an attacker's name."""
    port = _start(db, cfg, http_allow_unauthenticated_loopback=True)
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/healthz", headers={"Host": "rebind.evil.example"}
    )
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(req, timeout=5)
    assert excinfo.value.code == 403


def test_serve_http_refuses_a_wide_bind_with_no_credential(db, cfg, monkeypatch):
    """Refused at startup, so no socket is ever opened on every interface."""
    monkeypatch.delenv("DKG_MCP_TOKEN", raising=False)
    monkeypatch.delenv("DKG_MCP_CLIENT_SECRET", raising=False)
    cfg.mcp = MCPConfig(http_enabled=True, http_bind="0.0.0.0", http_port=0)
    with pytest.raises(ConfigError, match="non-loopback bind|refusing to serve"):
        serve_http(db, host="0.0.0.0", port=0, cfg=cfg)

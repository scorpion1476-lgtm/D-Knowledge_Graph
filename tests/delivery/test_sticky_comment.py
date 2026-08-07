"""One sticky pull-request comment, updated in place (R-15).

The publication path takes its transport as an argument, so every case here is
driven by a fake that records the requests it receives and simulates the comment
store. NO TEST HERE CONTACTS THE NETWORK, and one of them proves it by making
``urllib.request.urlopen`` explode if anything reaches for it.

What is asserted: the marker is looked up BEFORE every write; a first run
creates and a second run updates the same comment; the store never grows past
one comment however many times the review is published; the lookup paginates;
a marker belonging to another key is not adopted; and a body that fails
validation is refused without any request at all.
"""

from __future__ import annotations

import json

import pytest

from dkg.code.pr_comment import marker_for, render_pr_comment
from dkg.code.pr_publish import (
    COMMENTS_PER_PAGE,
    Request,
    Response,
    find_marked_comment,
    publish_sticky_comment,
    validate_comment_body,
)
from dkg.core.errors import ValidationError

REPO = "example-org/example-repo"
PR = 7
MARKER = marker_for("dkg-code-review")
OTHER_MARKER = marker_for("some-other-review")


def _body(marker: str = MARKER, note: str = "first") -> str:
    return f"{marker}\n## review\n\nnote: {note}\n"


class FakeGitHub:
    """A comment store plus a transport that records every request.

    Deliberately small: it understands the three calls the publication path
    makes and rejects anything else, so a change that starts issuing a fourth
    kind of request fails here rather than silently passing.
    """

    def __init__(self, comments=None, *, per_page=COMMENTS_PER_PAGE):
        self.comments = list(comments or [])
        self.requests: list[Request] = []
        self.per_page = per_page
        self._next_id = max((c["id"] for c in self.comments), default=100) + 1

    def __call__(self, request: Request) -> Response:
        self.requests.append(request)
        assert request.headers["Authorization"].startswith("Bearer "), "the token must be sent"
        if request.method == "GET":
            return self._list(request)
        if request.method == "POST":
            return self._create(request)
        if request.method == "PATCH":
            return self._update(request)
        raise AssertionError(f"unexpected method {request.method}")

    # -- handlers ----------------------------------------------------------

    def _list(self, request: Request) -> Response:
        page = int(request.url.split("page=")[-1])
        start = (page - 1) * self.per_page
        return Response(200, json.dumps(self.comments[start : start + self.per_page]))

    def _create(self, request: Request) -> Response:
        body = json.loads(request.body.decode("utf-8"))["body"]
        comment = {"id": self._next_id, "body": body}
        self._next_id += 1
        self.comments.append(comment)
        return Response(201, json.dumps(comment))

    def _update(self, request: Request) -> Response:
        comment_id = int(request.url.rsplit("/", 1)[-1])
        body = json.loads(request.body.decode("utf-8"))["body"]
        for comment in self.comments:
            if comment["id"] == comment_id:
                comment["body"] = body
                return Response(200, json.dumps(comment))
        return Response(404, "{}")

    # -- helpers -----------------------------------------------------------

    @property
    def methods(self) -> list[str]:
        return [r.method for r in self.requests]

    def marked(self) -> list[dict]:
        return [c for c in self.comments if MARKER in c["body"]]


def _publish(fake, body, **kwargs):
    return publish_sticky_comment(
        transport=fake,
        repo=REPO,
        pr_number=PR,
        body=body,
        token="t0ken",
        marker=MARKER,
        **kwargs,
    )


# -- create, then update in place --------------------------------------------


def test_first_publication_creates_one_comment():
    fake = FakeGitHub()
    result = _publish(fake, _body(note="first"))
    assert result["action"] == "created"
    assert result["posted"] is True
    assert len(fake.marked()) == 1
    # The lookup happened first, then the write. Not the other way round.
    assert fake.methods == ["GET", "POST"]


def test_second_publication_updates_the_same_comment_in_place():
    fake = FakeGitHub()
    first = _publish(fake, _body(note="first"))
    fake.requests.clear()

    second = _publish(fake, _body(note="second"))
    assert second["action"] == "updated"
    assert second["comment_id"] == first["comment_id"]
    assert fake.methods == ["GET", "PATCH"]
    assert len(fake.marked()) == 1, "the thread must never be duplicated"
    assert "second" in fake.marked()[0]["body"]
    assert "first" not in fake.marked()[0]["body"]


def test_many_pushes_never_grow_the_thread():
    fake = FakeGitHub()
    for push in range(6):
        _publish(fake, _body(note=f"push-{push}"))
    assert len(fake.comments) == 1
    assert "push-5" in fake.comments[0]["body"]


def test_the_marker_is_looked_up_before_every_write():
    fake = FakeGitHub()
    for _ in range(3):
        _publish(fake, _body())
    # Every write is immediately preceded by a lookup, in every round.
    for index, request in enumerate(fake.requests):
        if request.method in ("POST", "PATCH"):
            assert index > 0
            assert fake.requests[index - 1].method == "GET"


# -- lookup behaviour --------------------------------------------------------


def test_lookup_paginates_past_a_full_first_page():
    filler = [{"id": i, "body": f"unrelated {i}"} for i in range(1, COMMENTS_PER_PAGE + 1)]
    target = {"id": 900, "body": f"{MARKER}\nold"}
    fake = FakeGitHub([*filler, target])
    found = find_marked_comment(
        fake, repo=REPO, pr_number=PR, marker=MARKER, token="t0ken"
    )
    assert found is not None
    assert found["id"] == 900
    assert fake.methods == ["GET", "GET"]


def test_lookup_stops_on_a_short_page_rather_than_asking_for_more():
    fake = FakeGitHub([{"id": 1, "body": "unrelated"}])
    assert find_marked_comment(fake, repo=REPO, pr_number=PR, marker=MARKER, token="t0ken") is None
    assert fake.methods == ["GET"]


def test_another_reviews_marker_is_not_adopted():
    fake = FakeGitHub([{"id": 5, "body": f"{OTHER_MARKER}\nsomebody else's review"}])
    result = _publish(fake, _body())
    assert result["action"] == "created"
    assert len(fake.comments) == 2
    assert fake.comments[0]["body"].startswith(OTHER_MARKER)


def test_the_oldest_marked_comment_is_the_one_updated():
    fake = FakeGitHub(
        [
            {"id": 11, "body": f"{MARKER}\nolder"},
            {"id": 22, "body": f"{MARKER}\nnewer duplicate from an earlier failure"},
        ]
    )
    result = _publish(fake, _body(note="fresh"))
    assert result["action"] == "updated"
    assert result["comment_id"] == 11


# -- validation gates the write ----------------------------------------------


def test_a_body_without_the_marker_is_refused_without_any_request():
    fake = FakeGitHub()
    result = _publish(fake, "## review with no marker\n")
    assert result["action"] == "rejected"
    assert result["posted"] is False
    assert fake.requests == []
    assert any("no marker" in r for r in result["validation"]["reasons"])


def test_a_body_with_two_markers_is_refused():
    fake = FakeGitHub()
    result = _publish(fake, f"{MARKER}\ntext\n{MARKER}\n")
    assert result["action"] == "rejected"
    assert fake.requests == []


def test_an_oversized_body_is_refused():
    fake = FakeGitHub()
    result = _publish(fake, _body() + ("x" * 70000))
    assert result["action"] == "rejected"
    assert fake.requests == []


def test_injected_markup_in_the_artifact_is_refused():
    fake = FakeGitHub()
    tampered = f"{MARKER}\n## review\n<script>alert(1)</script>\n"
    result = _publish(fake, tampered)
    assert result["action"] == "rejected"
    assert fake.requests == []
    assert any("angle bracket" in r for r in result["validation"]["reasons"])


def test_a_rendered_comment_passes_validation():
    report = {
        "review": {
            "scope": {"base_ref": "abc", "changed_files": ["a.py"]},
            "risk": {"level": "low", "score": 0.0, "levels": {"names": ["low"], "cuts": {"low": 0.0}}},
            "changed_symbols": [],
            "flows": [],
            "test_gaps": {},
            "token_saving": {},
            "why": {"advisory": "ADVISORY."},
        },
        "gate": {},
    }
    body = render_pr_comment(report)
    verdict = validate_comment_body(body, marker=MARKER)
    assert verdict["valid"], verdict["reasons"]


# -- dry run and safety ------------------------------------------------------


def test_dry_run_looks_up_but_never_writes():
    fake = FakeGitHub([{"id": 3, "body": f"{MARKER}\nold"}])
    result = _publish(fake, _body(), dry_run=True)
    assert result["action"] == "would-update"
    assert result["posted"] is False
    assert fake.methods == ["GET"]
    assert fake.comments[0]["body"].endswith("old")


def test_a_hostile_repository_name_is_refused():
    fake = FakeGitHub()
    with pytest.raises(ValidationError):
        publish_sticky_comment(
            transport=fake,
            repo="evil.example/../../x",
            pr_number=PR,
            body=_body(),
            token="t0ken",
            marker=MARKER,
        )
    assert fake.requests == []


def test_a_non_https_api_base_is_refused():
    fake = FakeGitHub()
    with pytest.raises(ValidationError):
        publish_sticky_comment(
            transport=fake,
            repo=REPO,
            pr_number=PR,
            body=_body(),
            token="t0ken",
            marker=MARKER,
            api_base="http://api.example",
        )
    assert fake.requests == []


def test_no_test_here_touches_the_network(monkeypatch):
    def explode(*_a, **_k):
        raise AssertionError("a test tried to open a real connection")

    monkeypatch.setattr("urllib.request.urlopen", explode)
    fake = FakeGitHub()
    _publish(fake, _body())
    _publish(fake, _body(note="again"))
    assert len(fake.comments) == 1


def test_the_token_is_never_exposed_by_the_loggable_form():
    request = Request("POST", "https://api.example/x", {"Authorization": "Bearer secret-token"}, b"{}")
    assert "secret-token" not in json.dumps(request.redacted())
    assert request.redacted()["headers"]["Authorization"] == "<redacted>"


def test_an_error_status_from_the_api_is_raised_not_swallowed():
    def failing(_request: Request) -> Response:
        return Response(403, '{"message": "forbidden"}')

    with pytest.raises(ValidationError):
        publish_sticky_comment(
            transport=failing,
            repo=REPO,
            pr_number=PR,
            body=_body(),
            token="t0ken",
            marker=MARKER,
        )


# -- token confinement, added after an adversarial review -------------------
#
# Two ways a bearer token could leave for a host nobody chose. Both were
# demonstrated against the shipped code, so both are pinned here.


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example",
        # The host here is evil.example. Comparing netloc, or reading it quickly,
        # both get this wrong; only comparing the parsed hostname catches it.
        "https://api.github.com@evil.example",
        "https://169.254.169.254",          # cloud instance metadata
        "https://127.0.0.1:8080",           # anything local that will take a token
        "https://api.github.com.evil.example",
    ],
)
def test_the_api_base_refuses_a_host_nobody_authorised(url):
    from dkg.code.pr_publish import _check_api_base

    with pytest.raises(ValidationError):
        _check_api_base(url)


def test_the_public_api_host_is_accepted_so_the_check_is_not_vacuous():
    from dkg.code.pr_publish import DEFAULT_API_BASE, _check_api_base

    assert _check_api_base(DEFAULT_API_BASE) == DEFAULT_API_BASE


def test_a_self_hosted_forge_works_only_when_the_operator_names_it(monkeypatch):
    from dkg.code.pr_publish import _API_HOST_ENV, _check_api_base

    monkeypatch.delenv(_API_HOST_ENV, raising=False)
    with pytest.raises(ValidationError):
        _check_api_base("https://ghe.internal.example/api/v3")
    monkeypatch.setenv(_API_HOST_ENV, "ghe.internal.example")
    assert _check_api_base("https://ghe.internal.example/api/v3")


def test_a_redirect_is_refused_rather_than_followed_with_the_token():
    """The standard library keeps Authorization across a redirect.

    urllib's HTTPRedirectHandler strips only content-length and content-type
    when it rebuilds the request, so a 302 would carry the bearer token to
    wherever Location points, possibly over plain http. Validating the API base
    once constrains the first hop and not where the credential lands.
    """
    import http.server
    import socket
    import threading

    from dkg.code.pr_publish import urllib_transport

    hits: list[str] = []

    class Redirector(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def do_GET(self):  # noqa: N802
            hits.append(self.headers.get("Authorization") or "")
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/stolen")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            return None

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    httpd = http.server.HTTPServer(("127.0.0.1", port), Redirector)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        send = urllib_transport(timeout=5.0)
        request = Request(
            method="GET",
            url=f"http://127.0.0.1:{port}/repos/o/n/issues/1/comments",
            headers={"Authorization": "Bearer sentinel-token"},
            body=None,
        )
        # It is refused before the redirect is followed. The refusal may come
        # from the host check or from the redirect handler; either way the
        # token never reaches the second host.
        with pytest.raises(ValidationError):
            send(request)
        assert all("stolen" not in h for h in hits)
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)

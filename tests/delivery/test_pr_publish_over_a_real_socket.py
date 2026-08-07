"""R-15 and R-19 over a REAL transport, a REAL socket, and a REAL stage boundary.

WHAT THIS IS AND IS NOT. This is not a hosted GitHub Actions run. Nothing here
contacts GitHub, and the rows that need a hosted run stay honestly short of
verified for that reason. What it does close is narrower and worth stating
exactly: the sticky-comment path had been driven only by an injected fake
transport, so the code that actually opens a socket, `urllib_transport`, was
never executed by any test, and neither was the artifact hand-off between the
two workflow stages. Both are now executed here.

Concretely, against a loopback TLS server impersonating the three API calls the
publication path makes:

* the real `urllib_transport` runs, over a real socket, speaking real HTTP,
  carrying a real Authorization header that the server asserts it received;
* the marker lookup paginates for real, over real JSON responses;
* a first publication creates and a second UPDATES THE SAME COMMENT, with the
  server, not a fake, holding the comment store;
* the two-stage boundary is real: the unprivileged stage renders the review to
  a file on disk and the privileged stage reads that file back and posts it,
  which is what the artifact upload and download do between the two workflows;
* the privileged stage validates the file as untrusted input before posting,
  because in the real design that file was produced by a run over code the
  pull request controls.

TLS is used rather than plain HTTP because the product refuses to send a token
over anything else, and a test that reached for plain HTTP would be testing a
weakened build. The certificate is self-signed, generated into a temporary
directory, and trusted only for the duration of one test through SSL_CERT_FILE.
The host allowlist is opened to localhost through the documented environment
variable rather than by patching the check, so the check itself still runs.
"""

from __future__ import annotations

import json
import shutil
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from dkg.code.pr_comment import marker_for, render_pr_comment
from dkg.code.pr_publish import publish_sticky_comment, urllib_transport
from dkg.core.errors import ValidationError

REPO = "example-org/example-repo"
PR = 7
MARKER_KEY = "dkg-code-review"
MARKER = marker_for(MARKER_KEY)
TOKEN = "test-token-not-a-real-credential"

requires_openssl = pytest.mark.skipif(
    shutil.which("openssl") is None,
    reason="no openssl binary to generate a self-signed certificate for the loopback server",
)


# -- a loopback server that behaves like the three calls we make ---------------


class _CommentStore:
    """The server's own state, so the store is not a test-side fiction."""

    def __init__(self) -> None:
        self.comments: list[dict] = []
        self.requests: list[tuple[str, str]] = []
        self.auth_headers: list[str | None] = []
        self._next_id = 1000

    def create(self, body: str) -> dict:
        self._next_id += 1
        comment = {"id": self._next_id, "body": body}
        self.comments.append(comment)
        return comment

    def update(self, comment_id: int, body: str) -> dict | None:
        for comment in self.comments:
            if comment["id"] == comment_id:
                comment["body"] = body
                return comment
        return None


def _handler_for(store: _CommentStore):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args) -> None:  # keep the test output readable
            return

        def _respond(self, status: int, payload) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _record(self) -> None:
            store.requests.append((self.command, urlsplit(self.path).path))
            store.auth_headers.append(self.headers.get("Authorization"))

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's contract
            self._record()
            parts = urlsplit(self.path)
            query = parse_qs(parts.query)
            page = int(query.get("page", ["1"])[0])
            per_page = int(query.get("per_page", ["30"])[0])
            start = (page - 1) * per_page
            self._respond(200, store.comments[start : start + per_page])

        def do_POST(self) -> None:  # noqa: N802
            self._record()
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
            self._respond(201, store.create(payload.get("body", "")))

        def do_PATCH(self) -> None:  # noqa: N802
            self._record()
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
            comment_id = int(urlsplit(self.path).path.rsplit("/", 1)[-1])
            updated = store.update(comment_id, payload.get("body", ""))
            if updated is None:
                self._respond(404, {"message": "Not Found"})
                return
            self._respond(200, updated)

    return Handler


def _self_signed(directory: Path) -> tuple[Path, Path]:
    """A certificate for localhost, generated here and trusted nowhere else."""
    key = directory / "server.key"
    cert = directory / "server.crt"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256",
            "-days", "1", "-nodes",
            "-keyout", str(key), "-out", str(cert),
            "-subj", "/CN=localhost",
            "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
    return cert, key


@pytest.fixture
def api(tmp_path, monkeypatch):
    """A loopback TLS server, and the environment that lets the product reach it.

    The host allowlist is opened through DKG_PR_API_HOSTS, which is the
    documented way an operator points this at a self-hosted endpoint, so the
    allowlist check itself still executes rather than being patched out.
    """
    cert, key = _self_signed(tmp_path)
    store = _CommentStore()

    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(store))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(cert), keyfile=str(key))
    server.socket = context.wrap_socket(server.socket, server_side=True)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    port = server.server_address[1]
    monkeypatch.setenv("DKG_PR_API_HOSTS", "localhost")
    monkeypatch.setenv("SSL_CERT_FILE", str(cert))
    try:
        yield {"base": f"https://localhost:{port}", "store": store}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# -- the real transport, over a real socket ------------------------------------


@requires_openssl
def test_the_real_transport_creates_then_updates_one_comment(api):
    """The whole point of a sticky comment, with nothing faked below the socket."""
    transport = urllib_transport(timeout=10)
    store = api["store"]

    first = publish_sticky_comment(
        transport=transport,
        repo=REPO,
        pr_number=PR,
        body=f"{MARKER}\n## review\n\nrun one\n",
        token=TOKEN,
        marker_key=MARKER_KEY,
        api_base=api["base"],
    )
    second = publish_sticky_comment(
        transport=transport,
        repo=REPO,
        pr_number=PR,
        body=f"{MARKER}\n## review\n\nrun two\n",
        token=TOKEN,
        marker_key=MARKER_KEY,
        api_base=api["base"],
    )

    assert first["action"] == "created"
    assert second["action"] == "updated"
    assert len(store.comments) == 1, "a second push must not open a second thread"
    assert "run two" in store.comments[0]["body"]
    assert first["comment_id"] == second["comment_id"]


@requires_openssl
def test_the_marker_is_looked_up_before_every_write(api):
    """Not an optimisation: the lookup is what makes duplication impossible."""
    transport = urllib_transport(timeout=10)
    for note in ("one", "two"):
        publish_sticky_comment(
            transport=transport,
            repo=REPO,
            pr_number=PR,
            body=f"{MARKER}\n## review\n\n{note}\n",
            token=TOKEN,
            marker_key=MARKER_KEY,
            api_base=api["base"],
        )

    methods = [method for method, _path in api["store"].requests]

    assert methods[0] == "GET", "the first request of the first run must be the lookup"
    assert methods.index("POST") > 0
    patch_at = methods.index("PATCH")
    assert "GET" in methods[:patch_at], "the update was not preceded by a lookup"


@requires_openssl
def test_the_token_actually_reaches_the_server_as_a_bearer_header(api):
    """A fake transport cannot establish that the header survives urllib."""
    publish_sticky_comment(
        transport=urllib_transport(timeout=10),
        repo=REPO,
        pr_number=PR,
        body=f"{MARKER}\n## review\n\nheader check\n",
        token=TOKEN,
        marker_key=MARKER_KEY,
        api_base=api["base"],
    )

    assert api["store"].auth_headers
    assert all(h == f"Bearer {TOKEN}" for h in api["store"].auth_headers)


@requires_openssl
def test_the_lookup_paginates_over_real_responses(api):
    """Fill a page and then some, so page two is genuinely fetched."""
    store = api["store"]
    for i in range(120):
        store.create(f"unrelated comment {i}\n")
    store.create(f"{MARKER}\n## review\n\nburied\n")

    result = publish_sticky_comment(
        transport=urllib_transport(timeout=10),
        repo=REPO,
        pr_number=PR,
        body=f"{MARKER}\n## review\n\nfound it\n",
        token=TOKEN,
        marker_key=MARKER_KEY,
        api_base=api["base"],
    )

    assert result["action"] == "updated", "the marker past page one was not found"
    assert len([c for c in store.comments if MARKER in c["body"]]) == 1
    pages = [p for m, p in store.requests if m == "GET"]
    assert len(pages) >= 2, "pagination did not happen"


@requires_openssl
def test_a_host_outside_the_allowlist_is_refused_before_any_socket_opens(api, monkeypatch):
    """The allowlist is real here, not patched away: narrow it and it refuses."""
    monkeypatch.setenv("DKG_PR_API_HOSTS", "somewhere-else.invalid")

    with pytest.raises(ValidationError, match="refusing to send a token"):
        publish_sticky_comment(
            transport=urllib_transport(timeout=10),
            repo=REPO,
            pr_number=PR,
            body=f"{MARKER}\n## review\n\nnope\n",
            token=TOKEN,
            marker_key=MARKER_KEY,
            api_base=api["base"],
        )

    assert api["store"].requests == [], "a request was sent to a host outside the allowlist"


# -- the two-stage boundary, for real -------------------------------------------


@requires_openssl
def test_the_two_stage_artifact_handoff_works_end_to_end(api, tmp_path):
    """Stage one writes a file. Stage two reads that file and posts it.

    This is what upload-artifact and download-artifact do between
    pr-review.yml and pr-review-publish.yml. Doing it through a real file on
    disk is what makes this more than passing a string between two functions.
    """
    # STAGE ONE, unprivileged: render the review and write it out. No token
    # exists in this stage and nothing is posted.
    rendered = render_pr_comment(
        {
            "repository": REPO,
            "risk": {"level": "moderate", "score": 0.42},
            "impacted": [],
        },
        marker_key=MARKER_KEY,
    )
    artifact_dir = tmp_path / "artifact"
    artifact_dir.mkdir()
    artifact = artifact_dir / "comment.md"
    artifact.write_text(rendered, encoding="utf-8")
    (artifact_dir / "pr-number.txt").write_text(f"{PR}\n", encoding="utf-8")

    # STAGE TWO, privileged: read the artifact back off disk and publish it.
    body = artifact.read_text(encoding="utf-8")
    pr_number = int((artifact_dir / "pr-number.txt").read_text(encoding="utf-8").strip())

    result = publish_sticky_comment(
        transport=urllib_transport(timeout=10),
        repo=REPO,
        pr_number=pr_number,
        body=body,
        token=TOKEN,
        marker_key=MARKER_KEY,
        api_base=api["base"],
    )

    assert result["action"] == "created"
    assert len(api["store"].comments) == 1
    posted = api["store"].comments[0]["body"]
    assert posted == body, "what stage one wrote is not what stage two posted"
    assert posted.startswith(MARKER)


@requires_openssl
def test_stage_two_refuses_an_artifact_that_was_tampered_with(api, tmp_path):
    """The artifact came from a run over code the pull request controls.

    Stage two therefore treats it as untrusted input. A body carrying injected
    HTML must be refused without any write happening.
    """
    artifact = tmp_path / "comment.md"
    artifact.write_text(
        f"{MARKER}\n## review\n\n<script>alert(1)</script>\n", encoding="utf-8"
    )

    result = publish_sticky_comment(
        transport=urllib_transport(timeout=10),
        repo=REPO,
        pr_number=PR,
        body=artifact.read_text(encoding="utf-8"),
        token=TOKEN,
        marker_key=MARKER_KEY,
        api_base=api["base"],
    )

    assert result["action"] == "rejected"
    assert not any(m in ("POST", "PATCH") for m, _ in api["store"].requests)
    assert api["store"].comments == []


@requires_openssl
def test_a_dry_run_looks_up_but_never_writes(api):
    result = publish_sticky_comment(
        transport=urllib_transport(timeout=10),
        repo=REPO,
        pr_number=PR,
        body=f"{MARKER}\n## review\n\ndry\n",
        token=TOKEN,
        marker_key=MARKER_KEY,
        api_base=api["base"],
        dry_run=True,
    )

    methods = [m for m, _ in api["store"].requests]
    assert result["action"].startswith("would-")
    assert methods and set(methods) == {"GET"}
    assert api["store"].comments == []


def test_this_file_does_not_claim_to_be_a_hosted_run():
    """A guard on the honesty of the claim, not on the code.

    The rows this file supports stay short of fully verified because a hosted
    workflow run is a thing this environment cannot produce. If someone later
    edits the docstring to imply otherwise, this fails.
    """
    text = Path(__file__).read_text(encoding="utf-8")
    assert "This is not a hosted GitHub Actions run" in text

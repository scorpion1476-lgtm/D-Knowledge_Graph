"""Selectable embedding backends, including the opt-in remote endpoint backend.

Air-gap note. One test in this file stands up a real HTTP server, and that is
consistent with the air-gap rule for a specific reason: the server is bound to
127.0.0.1 on an ephemeral port, so every byte stays inside this machine's
loopback interface. No name is resolved, no route is taken, and no external host
is contacted. It exists so the real ``urllib`` request path is exercised rather
than mocked away, which is the only way to prove the loopback branch actually
works. Every test that involves a NON-loopback endpoint uses an injected
transport and an unroutable ``.invalid`` host, so nothing can leave even if the
refusal or the warning were broken.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dkg.adapters.embedding import (
    BACKEND_ENV,
    EGRESS_ENV,
    REMOTE_DIMENSION_ENV,
    REMOTE_ENDPOINT_ENV,
    REMOTE_MODEL_ENV,
    EgressNotPermittedError,
    HashingEmbeddingAdapter,
    Model2VecEmbeddingAdapter,
    RemoteEndpointEmbeddingAdapter,
    default_embedding_adapter,
    is_loopback_endpoint,
    select_embedding_adapter,
)
from dkg.ingest.base import ingest_text
from dkg.search.vector_index import model_tag, reindex, stored_count

DIM = 8
_ENV_KEYS = (
    BACKEND_ENV,
    EGRESS_ENV,
    REMOTE_ENDPOINT_ENV,
    REMOTE_MODEL_ENV,
    REMOTE_DIMENSION_ENV,
    "DKG_EMBEDDING_REMOTE_TIMEOUT",
    "DKG_EMBEDDING_REMOTE_API_KEY",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Start every test from the shipped default: nothing configured at all."""
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _fake_vector(text: str) -> list[float]:
    """A deterministic vector so a response can be checked, not just counted."""
    return [float((sum(ord(c) for c in text) + i) % 17) for i in range(DIM)]


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        self.server.received.append(payload)  # type: ignore[attr-defined]
        body = json.dumps(
            {"data": [{"embedding": _fake_vector(t)} for t in payload["input"]]}
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep the test output quiet
        return


@pytest.fixture
def loopback_endpoint():
    """A real HTTP server on 127.0.0.1 only. Nothing leaves this machine."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.received = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_address[1]}/v1/embeddings"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class _RecordingTransport:
    """A transport that records calls and never opens a socket."""

    def __init__(self, dimension: int = DIM):
        self.calls: list[tuple[str, bytes]] = []
        self.dimension = dimension

    def __call__(self, url, body, *, headers, timeout):
        self.calls.append((url, body))
        payload = json.loads(body.decode("utf-8"))
        return json.dumps(
            {"data": [{"embedding": [0.0] * self.dimension} for _ in payload["input"]]}
        ).encode("utf-8")


# -- selection ---------------------------------------------------------------


def test_every_backend_is_selectable_through_the_adapter_interface():
    assert select_embedding_adapter("hashing").name == "hashing"
    assert isinstance(select_embedding_adapter("hashing"), HashingEmbeddingAdapter)
    assert isinstance(select_embedding_adapter("model2vec"), Model2VecEmbeddingAdapter)
    remote = select_embedding_adapter("remote")
    assert isinstance(remote, RemoteEndpointEmbeddingAdapter)
    # All three satisfy the one interface the retrieval path uses.
    for adapter in (select_embedding_adapter("hashing"), remote):
        assert hasattr(adapter, "embed")
        assert isinstance(adapter.available(), tuple)


def test_backend_is_selectable_by_environment_variable(monkeypatch):
    monkeypatch.setenv(BACKEND_ENV, "hashing")
    assert default_embedding_adapter().name == "hashing"
    monkeypatch.setenv(BACKEND_ENV, "remote")
    monkeypatch.setenv(REMOTE_ENDPOINT_ENV, "https://embeddings.invalid/v1/embeddings")
    assert default_embedding_adapter().name.startswith("remote-")


def test_unknown_backend_fails_loud():
    with pytest.raises(ValueError, match="unknown embedding backend"):
        select_embedding_adapter("does-not-exist")


def test_remote_backend_is_off_by_default():
    """With no configuration at all, selection never reaches the remote backend."""
    adapter = default_embedding_adapter()
    assert not adapter.name.startswith("remote-")
    assert adapter.name in ("model2vec", "hashing")
    assert not isinstance(adapter, RemoteEndpointEmbeddingAdapter)


def test_remote_is_unavailable_with_no_endpoint():
    ok, why = RemoteEndpointEmbeddingAdapter().available()
    assert not ok
    assert REMOTE_ENDPOINT_ENV in why


def test_remote_is_unavailable_with_no_declared_dimension():
    ok, why = RemoteEndpointEmbeddingAdapter(
        "https://embeddings.invalid/v1/embeddings", allow_egress=True
    ).available()
    assert not ok
    assert REMOTE_DIMENSION_ENV in why


# -- egress refusal ----------------------------------------------------------


def test_remote_refuses_without_egress_optin_and_sends_nothing():
    transport = _RecordingTransport()
    adapter = RemoteEndpointEmbeddingAdapter(
        "https://embeddings.invalid/v1/embeddings",
        dimension=DIM,
        transport=transport,
    )
    ok, why = adapter.available()
    assert not ok
    assert EGRESS_ENV in why
    with pytest.raises(EgressNotPermittedError) as excinfo:
        adapter.embed(["a secret sentence"])
    assert EGRESS_ENV in str(excinfo.value)
    assert "Nothing was sent" in str(excinfo.value)
    # The decisive assertion: the transport was never reached.
    assert transport.calls == []


def test_remote_refuses_over_loopback_too_without_optin(loopback_endpoint):
    _server, url = loopback_endpoint
    adapter = RemoteEndpointEmbeddingAdapter(url, dimension=DIM)
    with pytest.raises(EgressNotPermittedError):
        adapter.embed(["hello"])
    assert _server.received == []


def test_refusal_does_not_quietly_fall_back(monkeypatch):
    """A named remote backend stays named even when it cannot run."""
    monkeypatch.setenv(BACKEND_ENV, "remote")
    monkeypatch.setenv(REMOTE_ENDPOINT_ENV, "https://embeddings.invalid/v1/embeddings")
    monkeypatch.setenv(REMOTE_DIMENSION_ENV, str(DIM))
    adapter = default_embedding_adapter()
    assert adapter.name.startswith("remote-")
    assert not adapter.available()[0]
    with pytest.raises(EgressNotPermittedError):
        adapter.embed(["x"])


def test_egress_optin_is_read_from_the_environment(monkeypatch):
    adapter = RemoteEndpointEmbeddingAdapter(
        "https://embeddings.invalid/v1/embeddings", dimension=DIM
    )
    assert not adapter.egress_permitted()
    monkeypatch.setenv(EGRESS_ENV, "1")
    assert adapter.egress_permitted()
    monkeypatch.setenv(EGRESS_ENV, "no")
    assert not adapter.egress_permitted()


# -- the egress warning ------------------------------------------------------


def test_non_loopback_run_warns_and_names_exactly_what_would_leave(capsys):
    transport = _RecordingTransport()
    adapter = RemoteEndpointEmbeddingAdapter(
        "https://embeddings.invalid/v1/embeddings",
        model="some-model",
        dimension=DIM,
        allow_egress=True,
        transport=transport,
    )
    texts = ["the first sentence to leave", "the second sentence to leave"]
    vectors = adapter.embed(texts)
    assert len(vectors) == 2
    err = capsys.readouterr().err
    assert "DKG EGRESS WARNING" in err
    assert "embeddings.invalid" in err
    for text in texts:
        assert text in err, "the warning must name exactly what text would leave"
    assert "some-model" in err
    assert len(transport.calls) == 1


def test_loopback_run_is_not_warned_about(loopback_endpoint, capsys):
    server, url = loopback_endpoint
    adapter = RemoteEndpointEmbeddingAdapter(url, dimension=DIM, allow_egress=True)
    vectors = adapter.embed(["loopback sentence"])
    assert vectors == [_fake_vector("loopback sentence")]
    assert server.received == [{"input": ["loopback sentence"]}]
    err = capsys.readouterr().err
    assert "EGRESS WARNING" not in err, "a loopback endpoint must not be warned about"


def test_warning_is_skipped_only_for_loopback(capsys):
    """Same adapter class, same call, two hosts: exactly one of them warns."""
    loopback = RemoteEndpointEmbeddingAdapter(
        "http://127.0.0.1:9/v1/embeddings",
        dimension=DIM,
        allow_egress=True,
        transport=_RecordingTransport(),
    )
    remote = RemoteEndpointEmbeddingAdapter(
        "https://embeddings.invalid/v1/embeddings",
        dimension=DIM,
        allow_egress=True,
        transport=_RecordingTransport(),
    )
    loopback.embed(["quiet"])
    loopback_err = capsys.readouterr().err
    remote.embed(["loud"])
    remote_err = capsys.readouterr().err
    assert "EGRESS WARNING" not in loopback_err
    assert "EGRESS WARNING" in remote_err


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://127.0.0.1:8080/e", True),
        ("http://127.5.5.5/e", True),
        ("http://localhost:8080/e", True),
        ("http://[::1]:8080/e", True),
        ("https://embeddings.invalid/e", False),
        ("http://10.0.0.4/e", False),
        ("http://0.0.0.0/e", False),
        ("", False),
    ],
)
def test_loopback_detection(url, expected):
    assert is_loopback_endpoint(url) is expected


# -- response handling and the store key -------------------------------------


def test_wrong_dimension_in_response_fails_loud():
    adapter = RemoteEndpointEmbeddingAdapter(
        "https://embeddings.invalid/v1/embeddings",
        dimension=DIM,
        allow_egress=True,
        transport=_RecordingTransport(dimension=DIM + 3),
    )
    with pytest.raises(RuntimeError, match="dimension"):
        adapter.embed(["x"])


def test_remote_vectors_never_mix_with_another_backend(db, loopback_endpoint):
    _server, url = loopback_endpoint
    ingest_text(db, "alpha beta gamma delta epsilon", display_name="d1")
    stub = HashingEmbeddingAdapter(dimension=256)
    remote = RemoteEndpointEmbeddingAdapter(url, dimension=DIM, allow_egress=True)

    stub_summary = reindex(db, adapter=stub)
    remote_summary = reindex(db, adapter=remote)

    assert stub_summary["model"] != remote_summary["model"]
    assert stored_count(db, tag=model_tag(stub)) == stub_summary["vectors"]
    assert stored_count(db, tag=model_tag(remote)) == remote_summary["vectors"]
    # Each backend's rows carry only its own dimension, so no query can read the
    # other population by accident.
    rows = db.fetchall(
        "SELECT model, dim FROM chunk_embeddings WHERE tenant_id='local' ORDER BY model, dim;"
    )
    by_model = {r["model"]: r["dim"] for r in rows}
    assert by_model[model_tag(stub)] == 256
    assert by_model[model_tag(remote)] == DIM


def test_two_remote_endpoints_get_distinct_store_keys():
    first = RemoteEndpointEmbeddingAdapter("https://one.invalid/e", dimension=DIM, allow_egress=True)
    second = RemoteEndpointEmbeddingAdapter("https://two.invalid/e", dimension=DIM, allow_egress=True)
    same_host_other_model = RemoteEndpointEmbeddingAdapter(
        "https://one.invalid/e", model="other", dimension=DIM, allow_egress=True
    )
    tags = {model_tag(first), model_tag(second), model_tag(same_host_other_model)}
    assert len(tags) == 3


def test_hybrid_search_degrades_and_says_why_when_remote_is_refused(db, monkeypatch):
    """A read path with a refused remote backend degrades and records the reason."""
    from dkg.search.hybrid import hybrid_search

    ingest_text(db, "alpha beta gamma delta epsilon", display_name="d1")
    monkeypatch.setenv(BACKEND_ENV, "remote")
    monkeypatch.setenv(REMOTE_ENDPOINT_ENV, "https://embeddings.invalid/v1/embeddings")
    monkeypatch.setenv(REMOTE_DIMENSION_ENV, str(DIM))
    results = hybrid_search(db, "alpha beta", limit=5, use_reranker=False, auto_index=False)
    assert results
    assert results[0]["why"]["vector"] is False
    assert EGRESS_ENV in results[0]["why"]["vector_unavailable"]
    assert stored_count(db, tag="ignored") == 0

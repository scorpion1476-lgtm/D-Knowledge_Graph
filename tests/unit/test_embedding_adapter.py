import pytest

from dkg.adapters.embedding import (
    BACKEND_ENV,
    EGRESS_ENV,
    EgressNotPermittedError,
    HashingEmbeddingAdapter,
    RemoteEndpointEmbeddingAdapter,
    cosine,
    default_embedding_adapter,
    select_embedding_adapter,
)


def test_hashing_adapter_returns_normalised_vectors():
    a = HashingEmbeddingAdapter(dimension=64)
    vecs = a.embed(["alpha beta gamma", "alpha alpha"])
    assert len(vecs) == 2
    for v in vecs:
        norm = sum(x * x for x in v) ** 0.5
        assert abs(norm - 1.0) < 1e-6 or norm == 0.0


def test_cosine_matches_similar_texts():
    a = HashingEmbeddingAdapter(dimension=256)
    v1, v2 = a.embed(["hello world hello", "hello world"])
    v3, v4 = a.embed(["hello world", "completely unrelated tokens"])
    assert cosine(v1, v2) > cosine(v3, v4)


def test_dimension_bounds_enforced():
    with pytest.raises(ValueError):
        HashingEmbeddingAdapter(dimension=8)


def test_available_reports_true_for_default():
    a = HashingEmbeddingAdapter()
    ok, _ = a.available()
    assert ok


def test_backend_selection_is_explicit_and_defaults_to_local(monkeypatch):
    monkeypatch.delenv(BACKEND_ENV, raising=False)
    monkeypatch.delenv(EGRESS_ENV, raising=False)
    monkeypatch.delenv("DKG_EMBEDDING_REMOTE_ENDPOINT", raising=False)
    # With no configuration the selection is local: never the remote backend.
    assert default_embedding_adapter().name in ("model2vec", "hashing")
    assert select_embedding_adapter("hashing").name == "hashing"
    with pytest.raises(ValueError):
        select_embedding_adapter("nonsense")


def test_remote_backend_refuses_without_the_egress_optin(monkeypatch):
    """The refusal is an exception, so the caller cannot mistake it for a result."""
    monkeypatch.delenv(EGRESS_ENV, raising=False)
    sent = []

    def transport(url, body, *, headers, timeout):  # pragma: no cover - must not run
        sent.append(url)
        raise AssertionError("the refused backend must not send anything")

    adapter = RemoteEndpointEmbeddingAdapter(
        "https://embeddings.invalid/v1/embeddings", dimension=4, transport=transport
    )
    with pytest.raises(EgressNotPermittedError):
        adapter.embed(["private text"])
    assert sent == []

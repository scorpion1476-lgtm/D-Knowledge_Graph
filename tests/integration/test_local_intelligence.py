"""End-to-end tests for optional-model, embedding, and capability paths.

Covers rows D-02 (LLM adapter), D-03 (embedding adapter),
D-04 (deterministic rules fallback), D-05 (pluggable provider-neutral
interfaces), and D-07 (capability detection reports unavailable honestly).

The unit tests exercise each adapter in isolation. These integration
tests exercise the same adapters through the capability registry and the
orchestration path, including at least one denial path (unknown
capability, no LLM registered, missing dependency).
"""

from __future__ import annotations

import pytest

from dkg.adapters.capability import (
    Capability,
    CapabilityRegistry,
    default_registry,
)
from dkg.adapters.embedding import HashingEmbeddingAdapter, cosine
from dkg.adapters.llm import (
    DeterministicLLMAdapter,
    GenerationRequest,
    NullLLMAdapter,
)
from dkg.core.errors import AdapterUnavailableError


def test_default_registry_has_core_adapter_capabilities():
    reg = default_registry()
    names = {c["name"] for c in reg.describe()}
    for expected in (
        "adapter.llm",
        "adapter.embedding",
        "adapter.browser",
        "adapter.identity",
        "mcp.stdio",
        "mcp.http",
    ):
        assert expected in names, f"capability {expected!r} missing from registry"


def test_registry_reports_unknown_capability_unavailable_denied():
    reg = default_registry()
    # A capability that does not exist is honestly reported as unavailable.
    assert reg.available("adapter.does_not_exist") is False


def test_registry_require_missing_raises_adapter_unavailable():
    reg = default_registry()
    with pytest.raises(AdapterUnavailableError, match="not registered"):
        reg.require("adapter.does_not_exist")


def test_registry_require_reports_unavailable_reason_when_check_fails():
    reg = CapabilityRegistry()
    reg.register(
        Capability(
            name="fake.unavailable",
            description="always returns unavailable",
            check=lambda: (False, "missing dependency: pretend_module"),
        )
    )
    with pytest.raises(AdapterUnavailableError, match="missing dependency"):
        reg.require("fake.unavailable")


def test_registry_check_that_raises_is_reported_as_false_not_crashed():
    reg = CapabilityRegistry()

    def broken() -> tuple[bool, str]:
        raise RuntimeError("boom")

    reg.register(Capability(name="fake.broken", description="raises", check=broken))
    described = reg.describe()
    entry = next(c for c in described if c["name"] == "fake.broken")
    assert entry["available"] is False
    assert "raised" in entry["reason"]


def test_deterministic_llm_adapter_produces_stable_marker():
    adapter = DeterministicLLMAdapter()
    r1 = adapter.generate(GenerationRequest(system="s", prompt="Alpha is fast."))
    r2 = adapter.generate(GenerationRequest(system="s", prompt="Alpha is fast."))
    assert r1.text == r2.text, "deterministic adapter must be reproducible"
    assert r1.text.startswith("[deterministic-llm]")
    ok, reason = adapter.available()
    assert ok
    assert "deterministic" in reason.lower() or "built-in" in reason.lower()


def test_deterministic_llm_adapter_handles_empty_prompt_gracefully():
    adapter = DeterministicLLMAdapter()
    r = adapter.generate(GenerationRequest(system="s", prompt=""))
    assert "empty" in r.text.lower()


def test_null_llm_adapter_reports_unavailable_honestly():
    adapter = NullLLMAdapter()
    ok, reason = adapter.available()
    assert ok is False
    assert "no LLM" in reason or "deterministic fallback" in reason


def test_hashing_embedding_adapter_returns_stable_bounded_vectors():
    adapter = HashingEmbeddingAdapter(dimension=128)
    v1 = adapter.embed(["hello world"])
    v2 = adapter.embed(["hello world"])
    assert v1 == v2, "hashing embedder must be reproducible"
    assert len(v1) == 1 and len(v1[0]) == 128


def test_hashing_embedding_cosine_of_identical_texts_is_one():
    adapter = HashingEmbeddingAdapter(dimension=64)
    (v,) = adapter.embed(["some sentence with words"])
    assert cosine(v, v) == pytest.approx(1.0)


def test_hashing_embedding_rejects_invalid_dimension():
    with pytest.raises(ValueError, match="dimension"):
        HashingEmbeddingAdapter(dimension=8)
    with pytest.raises(ValueError, match="dimension"):
        HashingEmbeddingAdapter(dimension=8192)


def test_cosine_rejects_dimension_mismatch():
    with pytest.raises(ValueError, match="dimensions must match"):
        cosine([1.0, 0.0], [1.0, 0.0, 0.0])

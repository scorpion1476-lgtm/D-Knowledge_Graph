from dkg.adapters.capability import default_registry


def test_default_registry_has_expected_names():
    reg = default_registry()
    names = {c["name"] for c in reg.describe()}
    for expected in (
        "ingest.html",
        "ingest.pdf",
        "ingest.rss",
        "net.http",
        "adapter.llm",
        "adapter.embedding",
        "mcp.stdio",
        "mcp.http",
    ):
        assert expected in names


def test_availability_is_boolean():
    reg = default_registry()
    for c in reg.describe():
        assert isinstance(c["available"], bool)
        assert isinstance(c["reason"], str)


def test_deterministic_llm_default_is_available():
    """The bundled DeterministicLLMAdapter provides a real, offline default."""
    reg = default_registry()
    assert reg.available("adapter.llm") is True


def test_bundled_defaults_all_available():
    reg = default_registry()
    for name in (
        "adapter.embedding",
        "adapter.browser",
        "adapter.identity",
        "ingest.rss",
        "ingest.docx",
        "net.http",
        "mcp.stdio",
        "mcp.http",
    ):
        assert reg.available(name), f"expected {name} to be available"

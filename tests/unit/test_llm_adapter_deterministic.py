from dkg.adapters.llm import DeterministicLLMAdapter, GenerationRequest


def test_deterministic_returns_prefix():
    a = DeterministicLLMAdapter()
    r = a.generate(GenerationRequest(system="s", prompt="Hello world. Some more text."))
    assert r.text.startswith("[deterministic-llm] summary:")
    assert "Hello world" in r.text


def test_deterministic_stop_sequence():
    a = DeterministicLLMAdapter()
    r = a.generate(GenerationRequest(system="s", prompt="Cut here CUT after", stop=["CUT"]))
    assert "CUT" not in r.text
    assert r.finish_reason == "stop_sequence"


def test_deterministic_available():
    ok, _ = DeterministicLLMAdapter().available()
    assert ok

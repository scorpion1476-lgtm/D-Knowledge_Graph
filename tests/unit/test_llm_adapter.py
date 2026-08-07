from dkg.adapters.llm import GenerationRequest, NullLLMAdapter


def test_null_llm_is_unavailable_but_callable():
    a = NullLLMAdapter()
    ok, _ = a.available()
    assert not ok
    resp = a.generate(GenerationRequest(system="x", prompt="y"))
    assert resp.text == ""
    assert resp.metadata["adapter"] == "null"

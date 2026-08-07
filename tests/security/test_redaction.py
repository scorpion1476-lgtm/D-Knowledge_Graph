from dkg.security.redact import redact, redact_dict


def test_bearer_header_redacted():
    text = "Authorization: Bearer abcdef1234567890abcdef"
    out, report = redact(text)
    assert "abcdef1234567890abcdef" not in out
    assert report.matched.get("bearer_header", 0) == 1


def test_url_basic_auth_redacted():
    text = "https://alice:secret_password@example.com/path"
    out, _ = redact(text)
    assert "secret_password" not in out
    assert "[REDACTED]" in out


def test_openai_key_redacted():
    text = "sk-thisistotallynotarealkey12345678"
    out, report = redact(text)
    assert "[REDACTED:openai_key]" in out
    assert report.matched.get("openai_key", 0) >= 1


def test_dict_walk_redacts_string_values():
    d = {"secret": "sk-abcdefghijklmnopqrstuv1234567", "nested": {"a": "safe"}}
    r = redact_dict(d)
    assert "[REDACTED" in r["secret"]
    assert r["nested"]["a"] == "safe"


def test_non_string_input_passed_through():
    out, report = redact(12345)  # type: ignore[arg-type]
    assert out == 12345
    assert report.matched == {}


def test_clean_text_reports_no_matches_denied_false_positive():
    # Confirm the redactor does not fire on ordinary prose (deny false
    # positives).
    text = "A knowledge graph stores facts and their citations."
    out, report = redact(text)
    assert out == text
    assert report.matched == {}


def test_redact_dict_rejects_invalid_shape_returns_input():
    # A non-dict, non-list scalar just round-trips.
    r = redact_dict(42)  # type: ignore[arg-type]
    assert r == 42

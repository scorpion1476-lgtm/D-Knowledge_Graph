"""Secrets are referenced, never stored, and never printed.

Acceptance test for matrix row I-09, "Encrypted secret reference design". The
row's own limitation states the design plainly: this build bundles no
cryptography. It holds *references* to secrets, resolved from the environment or
an OS keyring at the moment of use, and it redacts anything that looks like a
credential on the way out. That is the design, and a manual review of the
security model cannot tell whether the code still implements it.

Three properties carry the whole design, and each is verified against the code
rather than the prose:

1. **Configuration holds a reference, not a value.** The MCP bearer credential
   is configured as `http_bearer_token_env`, the *name* of an environment
   variable. If a field named for a value ever appears, or a default ever holds
   something that looks like a credential, the design has quietly inverted.
2. **The redactor actually redacts.** It is exercised against synthetic
   credentials of every shape it claims to know, and the output must not
   contain the input. Every literal here is assembled at runtime from inert
   fragments so this file stays clean under `scripts/secret_scan.py`, the same
   discipline `tests/security/test_secret_scanner.py` uses.
3. **No cryptography is bundled.** The row promises the design avoids shipping
   crypto, so the declared runtime dependency set must contain none.

The security model document is then required to state the design, so a reader
is told what the code does rather than left to infer it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from dkg.core.config import MCPConfig
from dkg.security.redact import redact, redact_dict

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "SECURITY_MODEL.md"


# -- the document states the design -------------------------------------------


def test_the_security_model_states_the_reference_design():
    flat = re.sub(r"\s+", " ", DOC.read_text(encoding="utf-8")).lower()
    assert "secret references go in configuration" in flat
    assert "the redactor removes them from any output that leaves the process" in flat


# -- configuration holds a reference, not a value -----------------------------


def test_the_credential_is_configured_as_an_environment_variable_name():
    cfg = MCPConfig()
    assert cfg.http_bearer_token_env == "DKG_MCP_TOKEN"
    assert re.fullmatch(r"[A-Z][A-Z0-9_]+", cfg.http_bearer_token_env), (
        "the configured value is not an environment variable name, so it is not a reference"
    )


def test_no_configuration_field_holds_a_literal_credential():
    """A default that is itself a secret would defeat the whole design."""
    cfg = MCPConfig()
    suspicious: list[str] = []
    for name, value in vars(cfg).items():
        if not isinstance(value, str) or not value:
            continue
        redacted, report = redact(value)
        if report.matched:
            suspicious.append(f"{name}={list(report.matched)}")
    assert not suspicious, f"configuration defaults look like credentials: {suspicious}"


def test_field_naming_makes_the_reference_explicit():
    """`*_env` is the naming convention that says "this is a pointer"."""
    names = [n for n in vars(MCPConfig()) if "token" in n or "secret" in n or "password" in n]
    assert names, "the MCP configuration no longer has a credential field at all"
    for name in names:
        assert name.endswith("_env"), (
            f"{name} names a credential without the _env suffix that marks it a reference"
        )


# -- the redactor actually redacts --------------------------------------------


def _synthetic(kind: str) -> str:
    """Build credential-shaped strings at runtime from inert fragments.

    Spelled literally, these would be real findings for the secret scanner in
    this very file.
    """
    return {
        "github": "gh" + "p_" + ("a1B2" * 9),
        "openai": "sk" + "-" + ("Xy9" * 8),
        "aws_id": "AK" + "IA" + ("QRSTUV7890ABCDEF"),
        "bearer": "Authorization: Bea" + "rer " + ("t0k3n" * 5),
        "url_auth": "https://" + "user" + ":" + "hunter2secret" + "@example.internal/x",
        "kv": "api" + "_key" + "=" + "s3cr3tvalue123",
    }[kind]


@pytest.mark.parametrize("kind", ["github", "openai", "aws_id", "bearer", "url_auth", "kv"])
def test_every_credential_shape_is_redacted(kind):
    raw = _synthetic(kind)
    out, report = redact(raw)
    assert report.matched, f"{kind}: the redactor matched nothing"
    assert "REDACTED" in out, f"{kind}: nothing was replaced"


@pytest.mark.parametrize("kind", ["github", "openai", "aws_id"])
def test_the_secret_itself_does_not_survive_redaction(kind):
    """Matching is not enough; the value must be gone from the output."""
    raw = _synthetic(kind)
    out, _ = redact(raw)
    assert raw not in out, f"{kind}: the credential is still present after redaction"


def test_redaction_walks_nested_structures():
    """Output leaves the process as JSON, not as a flat string."""
    payload = {"outer": {"items": [{"header": _synthetic("bearer")}]}}
    out = redact_dict(payload)
    flat = repr(out)
    assert "REDACTED" in flat
    assert _synthetic("bearer") not in flat


def test_ordinary_text_is_left_alone():
    """A redactor that redacts everything is as useless as one that redacts nothing."""
    prose = "The deployment guide explains how to set the credential before starting."
    out, report = redact(prose)
    assert out == prose and not report.matched


# -- nothing cryptographic is bundled -----------------------------------------


def test_no_cryptography_library_is_a_runtime_dependency():
    """The design avoids shipping crypto; it references a secret store instead."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    block = re.search(r"^dependencies\s*=\s*\[(.*?)\]", text, re.M | re.S)
    assert block, "pyproject declares no dependencies list"
    declared = block.group(1).lower()
    for library in ("cryptography", "pycryptodome", "pynacl", "nacl", "keyring", "bcrypt"):
        assert library not in declared, (
            f"{library} is a mandatory runtime dependency; the reference design bundles no crypto"
        )


def test_the_core_bundles_no_cryptographic_runtime_dependency_at_all():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    block = re.search(r"^dependencies\s*=\s*\[(.*?)\]", text, re.M | re.S)
    assert block and not block.group(1).strip(), (
        "the core is documented as having zero mandatory runtime dependencies"
    )

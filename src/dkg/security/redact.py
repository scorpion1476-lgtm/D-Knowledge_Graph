"""Credential redaction.

Applies conservative regex-based redaction to any string that will be logged,
printed, or returned through an external interface. The goal is not perfect
DLP; it is defence-in-depth so that a slip of a secret does not immediately
surface in stdout or an audit line.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_REDACT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("aws_secret_key", re.compile(r"(?<![A-Za-z0-9/+=])[A-Za-z0-9/+=]{40}(?![A-Za-z0-9/+=])")),
    ("github_pat", re.compile(r"\bghp_[A-Za-z0-9]{36,}\b")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9-_]{20,}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9-_]{20,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z-_]{35}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("private_key_block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP |)PRIVATE KEY-----[\s\S]+?-----END [^-]+-----")),
    ("bearer_header", re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)([A-Za-z0-9._-]{16,})")),
    ("basic_auth_url", re.compile(r"([a-z]+://)([^\s:@/]+):([^\s@/]+)@")),
    ("password_kv", re.compile(r"(?i)(password|passwd|pwd|secret|api_key|apikey)\s*[:=]\s*['\"]?([^\s'\";]{4,})['\"]?")),
]


@dataclass
class RedactionReport:
    matched: dict[str, int]


def redact(text: str) -> tuple[str, RedactionReport]:
    if not isinstance(text, str):
        return text, RedactionReport(matched={})
    counts: dict[str, int] = {}
    out = text
    for name, pattern in _REDACT_PATTERNS:
        def _repl(m: re.Match, _name=name) -> str:
            counts[_name] = counts.get(_name, 0) + 1
            if _name == "bearer_header":
                return f"{m.group(1)}[REDACTED:{_name}]"
            if _name == "basic_auth_url":
                return f"{m.group(1)}[REDACTED]:[REDACTED]@"
            if _name == "password_kv":
                return f"{m.group(1)}=[REDACTED]"
            return f"[REDACTED:{_name}]"

        out = pattern.sub(_repl, out)
    return out, RedactionReport(matched=counts)


def redact_dict(data: dict) -> dict:
    """Redact string values recursively."""
    return _redact_walk(data)


def _redact_walk(obj):
    if isinstance(obj, str):
        return redact(obj)[0]
    if isinstance(obj, dict):
        return {k: _redact_walk(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact_walk(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_redact_walk(v) for v in obj)
    return obj

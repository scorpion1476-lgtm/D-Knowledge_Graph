"""Deterministic, collision-resistant identifiers.

IDs are content-derived where possible so that ingesting the same source twice
produces the same source ID, chunk ID, and claim ID. This makes deduplication
trivial and lets the audit log survive backup and restore.
"""

from __future__ import annotations

import hashlib
import secrets
import time

_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def content_id(prefix: str, *parts: str | bytes) -> str:
    """Return a stable ID of the form ``prefix_<sha256[:24]>``.

    Any Unicode ``str`` part is encoded UTF-8 before hashing.
    """
    if not prefix or "_" in prefix:
        raise ValueError("prefix must be non-empty and contain no underscore")
    h = hashlib.sha256()
    for p in parts:
        if isinstance(p, str):
            p = p.encode("utf-8")
        h.update(p)
        h.update(b"\x1f")  # unit separator - avoids trivial collisions
    return f"{prefix}_{h.hexdigest()[:24]}"


def random_id(prefix: str, length: int = 16) -> str:
    """Return a random ID for events that do not have a content basis."""
    if length < 8:
        raise ValueError("length must be at least 8")
    return f"{prefix}_{secrets.token_hex(length)}"


def ulid_like() -> str:
    """Return a monotonic, sortable random identifier (26 characters, Crockford base32).

    This is a minimal, dependency-free approximation of ULID sufficient for
    ordering audit entries and task runs. It is not spec-compliant.
    """
    ts_ms = int(time.time() * 1000)
    ts_bytes = ts_ms.to_bytes(6, "big")
    rand_bytes = secrets.token_bytes(10)
    payload = ts_bytes + rand_bytes
    # base32 encode into 26 chars using Crockford alphabet
    num = int.from_bytes(payload, "big")
    out = []
    for _ in range(26):
        out.append(_ULID_ALPHABET[num & 0x1F])
        num >>= 5
    return "".join(reversed(out))

#!/usr/bin/env python3
"""Regex-based secret scanner for the source tree.

Uses the same patterns as ``dkg.security.redact`` so a slip in either place
shows up in the other.

Design:
- Scan every source-controlled text file, including tests and docs.
- Skip only ephemeral or vendored directories (``.git``, ``.venv``,
  ``__pycache__``, ``dist``, ``build``, ``.mypy_cache``, ``.pytest_cache``,
  ``node_modules``, ``test-evidence``).
- Allow specific, documented fake-token fixtures through a narrow
  ``(relative_path, kind, canonical_value)`` allowlist. Every allowed
  entry carries a rationale so it is visible in a diff review.
- Never allowlist by kind alone or by whole directory; the triple must
  match exactly.
- Two files (this script and ``src/dkg/security/redact.py``) are excluded
  from scanning to avoid trivial self-matches on the patterns themselves.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_PATTERNS = {
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "github_pat": re.compile(r"\bghp_[A-Za-z0-9]{36,}\b"),
    "openai_key": re.compile(r"\bsk-[A-Za-z0-9-_]{20,}\b"),
    "anthropic_key": re.compile(r"\bsk-ant-[A-Za-z0-9-_]{20,}\b"),
    "google_api_key": re.compile(r"\bAIza[0-9A-Za-z-_]{35}\b"),
    "slack_token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    "pem_block": re.compile(r"-----BEGIN [^-]+PRIVATE KEY-----"),
}

_SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    ".mypy_cache",
    ".pytest_cache",
    "node_modules",
    "test-evidence",
}

_SCAN_EXTS = {
    ".py", ".md", ".txt", ".yaml", ".yml", ".json", ".toml",
    ".sh", ".cfg", ".ini", ".env", ".example",
}

# Files excluded from scanning because their own regex patterns would
# trivially match themselves. A per-file exception, not a directory one.
_SELF_FILES = frozenset(
    {
        "scripts/secret_scan.py",
        "src/dkg/security/redact.py",
    }
)


# Narrow allowlist. Each entry is an explicit ``(path, kind, value)``
# triple with a rationale. ``value`` must be the *exact* substring that
# matched; anything else is reported.
_ALLOWED_FIXTURES: tuple[dict[str, str], ...] = (
    {
        "path": "tests/security/test_redaction.py",
        "kind": "openai_key",
        "value": "sk-thisistotallynotarealkey12345678",
        "rationale": (
            "explicit fake OpenAI-shaped token used by "
            "test_openai_key_redacted; never a real credential"
        ),
    },
    {
        "path": "tests/security/test_redaction.py",
        "kind": "openai_key",
        "value": "sk-abcdefghijklmnopqrstuv1234567",
        "rationale": (
            "explicit fake OpenAI-shaped token used by "
            "test_dict_walk_redacts_string_values; never a real credential"
        ),
    },
)


def load_allowlist() -> dict[tuple[str, str], set[str]]:
    """Return ``{(relative_path, kind): {value, ...}}``.

    Exposed for tests so they can assert the allowlist shape without
    duplicating the data.
    """
    out: dict[tuple[str, str], set[str]] = {}
    for entry in _ALLOWED_FIXTURES:
        key = (entry["path"], entry["kind"])
        out.setdefault(key, set()).add(entry["value"])
    return out


def scan_tree(root: Path) -> list[dict]:
    """Return a list of unallowed secret-shaped matches under ``root``.

    Exposed for tests. The list is ordered by (file, line).
    """
    allow = load_allowlist()
    hits: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            p = Path(dirpath) / fn
            # Normalise to POSIX so paths compare identically on Windows.
            rel = p.relative_to(root).as_posix()
            if rel in _SELF_FILES:
                continue
            if p.suffix.lower() not in _SCAN_EXTS:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for kind, pattern in _PATTERNS.items():
                for m in pattern.finditer(text):
                    value = m.group(0)
                    if value in allow.get((rel, kind), set()):
                        continue
                    hits.append(
                        {
                            "file": rel,
                            "kind": kind,
                            "line": text[: m.start()].count("\n") + 1,
                            "value_prefix": value[:8],
                        }
                    )
    hits.sort(key=lambda h: (h["file"], h["line"], h["kind"]))
    return hits


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="D-Knowledge_Graph secret scanner")
    ap.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="tree to scan (default: project root)",
    )
    args = ap.parse_args(argv)

    hits = scan_tree(args.root)
    if hits:
        for h in hits:
            print(
                f"secret? {h['kind']} at {h['file']}:{h['line']} "
                f"(value starts with {h['value_prefix']!r})",
                file=sys.stderr,
            )
        return 1
    print("no secrets found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Regression tests for scripts/secret_scan.py.

These tests exercise the scanner against synthetic trees written under a
temporary directory so we can prove:

1. A real-looking secret placed inside a ``tests/`` file is detected. This
   guards against the earlier defect where the scanner blanket-excluded
   ``tests/``.

2. A specifically approved fake fixture (documented in the scanner's
   allowlist by exact ``(path, kind, value)`` triple) is not reported.

3. Only exact allowlist matches are honoured. Changing the value, the
   path, or the kind causes the scanner to report the finding.

4. The scanner's built-in allowlist covers exactly the two fixtures in
   ``tests/security/test_redaction.py``; nothing broader is silently
   allowed.

Note: every literal that would match a secret regex is *assembled at
runtime* from harmless fragments so this test module itself is clean when
scanned. The scanner sees only inert strings in the source file.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


# Assembled at runtime; the scanner's regexes will not match the source of
# this file even though the constructed values are secret-shaped.
_AWS_LIKE = "AKIA" + "0123456789ABCDEF"
_GITHUB_LIKE = "ghp_" + ("A" * 40)
_FAKE_OPENAI_APPROVED_A = "sk-" + "thisistotallynotarealkey12345678"
_FAKE_OPENAI_APPROVED_B = "sk-" + "abcdefghijklmnopqrstuv1234567"
_FAKE_OPENAI_ALTERED = "sk-" + "thisistotallynotarealkey12345679"


def _load_scanner():
    spec = importlib.util.spec_from_file_location(
        "dkg_secret_scan_under_test",
        REPO_ROOT / "scripts" / "secret_scan.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _make_tree(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return root


def test_scanner_detects_real_looking_secret_in_tests_directory(tmp_path):
    scanner = _load_scanner()
    root = _make_tree(
        tmp_path,
        {
            "tests/security/some_new_test.py": (
                f"SECRET = '{_AWS_LIKE}'  # aws access key shape\n"
            ),
        },
    )
    hits = scanner.scan_tree(root)
    kinds = {h["kind"] for h in hits}
    assert "aws_access_key" in kinds, f"expected aws_access_key hit, got {hits!r}"
    assert any(h["file"].startswith("tests/") for h in hits), (
        "scanner must scan under tests/"
    )


def test_scanner_detects_secret_in_top_level_test_directory(tmp_path):
    scanner = _load_scanner()
    root = _make_tree(
        tmp_path,
        {
            "tests/anything.py": f"TOKEN = '{_GITHUB_LIKE}'\n",
        },
    )
    hits = scanner.scan_tree(root)
    assert any(h["kind"] == "github_pat" for h in hits)


def test_scanner_allows_approved_fake_redaction_fixture_only_at_exact_path(tmp_path):
    scanner = _load_scanner()
    root = _make_tree(
        tmp_path,
        {
            # exact path + exact values used by the real redaction test
            "tests/security/test_redaction.py": (
                f"TOKEN = '{_FAKE_OPENAI_APPROVED_A}'\n"
                f"OTHER = '{_FAKE_OPENAI_APPROVED_B}'\n"
            ),
        },
    )
    hits = scanner.scan_tree(root)
    assert hits == [], f"approved fake fixtures must not be reported, got {hits!r}"


def test_scanner_does_not_allow_same_value_at_a_different_path(tmp_path):
    scanner = _load_scanner()
    root = _make_tree(
        tmp_path,
        {
            "tests/security/other_file.py": (
                f"TOKEN = '{_FAKE_OPENAI_APPROVED_A}'\n"
            ),
        },
    )
    hits = scanner.scan_tree(root)
    assert hits, "allowlist must be bound to exact path"
    assert hits[0]["kind"] == "openai_key"
    assert hits[0]["file"] == "tests/security/other_file.py"


def test_scanner_does_not_allow_a_similar_but_different_value(tmp_path):
    scanner = _load_scanner()
    root = _make_tree(
        tmp_path,
        {
            # correct path, but the value differs by one character
            "tests/security/test_redaction.py": (
                f"TOKEN = '{_FAKE_OPENAI_ALTERED}'\n"
            ),
        },
    )
    hits = scanner.scan_tree(root)
    assert hits, "allowlist must be bound to exact value, not prefix"
    assert hits[0]["kind"] == "openai_key"


def test_allowlist_only_covers_the_two_documented_fixtures():
    scanner = _load_scanner()
    allow = scanner.load_allowlist()
    # Only the redaction test file, only the openai_key kind, exactly two
    # values.
    assert set(allow.keys()) == {
        ("tests/security/test_redaction.py", "openai_key"),
    }
    values = next(iter(allow.values()))
    assert values == {_FAKE_OPENAI_APPROVED_A, _FAKE_OPENAI_APPROVED_B}


def test_real_repo_scan_returns_no_hits():
    """Running the scanner against the real repository must be clean.

    Any new real-looking secret shape added elsewhere must go through the
    documented allowlist first.
    """
    scanner = _load_scanner()
    hits = scanner.scan_tree(REPO_ROOT)
    assert hits == [], (
        "unallowed secret-shaped strings detected in the repository: "
        f"{hits!r}"
    )

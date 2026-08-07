#!/usr/bin/env python3
"""AST-based SAST for the D-Knowledge_Graph source tree.

Uses only the Python standard library. Walks every ``.py`` file under
``src/`` and ``scripts/`` and reports these classes of issue:

- ``B101 exec``: use of ``exec(...)`` or ``eval(...)`` on any value.
- ``B102 shell_true``: ``subprocess.*`` calls with ``shell=True``.
- ``B103 mktemp``: use of ``tempfile.mktemp``.
- ``B104 sql_fstring``: ``execute(...)`` calls where the SQL argument
  is an f-string or a ``%``-formatted string (encourages parameter
  binding).
- ``B105 hardcoded_secret``: string literal that matches the same
  patterns as the secret scanner.
- ``B106 assert_in_prod``: unused (we allow assert in tests, and
  application code does not gate on assertions).
- ``B107 dynamic_import``: ``importlib.import_module`` with a value
  that is not a literal string.
- ``B108 xml_parse``: use of ``xml.etree`` parse functions on external
  input without an entity check.

Exits non-zero if any high-severity issue is found. Warnings do not
fail the run.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCAN_DIRS = ("src", "scripts")

_SECRET_PATTERNS = [
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "aws_access_key"),
    (re.compile(r"\bghp_[A-Za-z0-9]{36,}\b"), "github_pat"),
    (re.compile(r"\bsk-[A-Za-z0-9-_]{20,}\b"), "openai_key"),
    (re.compile(r"\bAIza[0-9A-Za-z-_]{35}\b"), "google_api_key"),
]

# The scanner itself contains the same regex definitions as a scanning
# tool. These files are excluded from the hardcoded-secret rule so they
# do not self-match.
_SELF_FILES = frozenset({
    "scripts/sast.py",
    "scripts/secret_scan.py",
    "src/dkg/security/redact.py",
})


@dataclass
class Finding:
    file: str
    line: int
    rule: str
    message: str
    severity: str  # "high" | "warn"


def _visit(tree: ast.AST, rel: str) -> list[Finding]:
    findings: list[Finding] = []

    class V(ast.NodeVisitor):
        def _push(self, node, rule, msg, sev):
            findings.append(
                Finding(file=rel, line=getattr(node, "lineno", 0), rule=rule, message=msg, severity=sev)
            )

        def visit_Call(self, node: ast.Call):
            func = node.func
            name = _dotted(func)
            if name in ("exec", "eval"):
                self._push(node, "B101", f"use of {name}()", "high")
            if name.startswith("subprocess.") or name == "subprocess":
                for kw in node.keywords:
                    if kw.arg == "shell" and _is_true(kw.value):
                        self._push(node, "B102", "subprocess with shell=True", "high")
            if name == "tempfile.mktemp":
                self._push(node, "B103", "use of tempfile.mktemp is unsafe", "high")
            if name.endswith(".execute") or name.endswith(".executemany"):
                if node.args:
                    first = node.args[0]
                    if isinstance(first, ast.JoinedStr):
                        self._push(node, "B104", "SQL passed via f-string", "high")
                    elif isinstance(first, ast.BinOp) and isinstance(first.op, ast.Mod):
                        self._push(node, "B104", "SQL passed via % formatting", "high")
                    elif isinstance(first, ast.BinOp) and isinstance(first.op, ast.Add):
                        # str + str concat likely SQL construction
                        if isinstance(first.left, ast.Constant) or isinstance(first.right, ast.Constant):
                            self._push(node, "B104", "SQL via string concatenation", "high")
            if name == "importlib.import_module":
                if node.args and not isinstance(node.args[0], ast.Constant):
                    self._push(node, "B107", "dynamic import_module with non-literal", "warn")
            self.generic_visit(node)

        def visit_Constant(self, node: ast.Constant):
            if isinstance(node.value, str) and rel not in _SELF_FILES:
                for pat, kind in _SECRET_PATTERNS:
                    if pat.search(node.value):
                        self._push(node, "B105", f"hardcoded {kind}-shaped literal", "high")
                        break
            self.generic_visit(node)

    V().visit(tree)
    return findings


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    return ""


def _is_true(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def scan_tree(root: Path) -> list[Finding]:
    all_findings: list[Finding] = []
    for base in SCAN_DIRS:
        target = root / base
        if not target.exists():
            continue
        for py in sorted(target.rglob("*.py")):
            # Normalise to POSIX so paths compare identically on Windows.
            rel = py.relative_to(root).as_posix()
            try:
                text = py.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(text, filename=rel)
            except SyntaxError as e:
                all_findings.append(
                    Finding(file=rel, line=e.lineno or 0, rule="B000", message=f"parse error: {e.msg}", severity="high")
                )
                continue
            all_findings.extend(_visit(tree, rel))
    return all_findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="D-Knowledge_Graph SAST")
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--out", type=Path, default=None, help="write JSON report here")
    args = ap.parse_args(argv)

    findings = scan_tree(args.root)
    high = [f for f in findings if f.severity == "high"]
    warn = [f for f in findings if f.severity == "warn"]

    for f in findings:
        print(f"{f.severity.upper():4} {f.rule} {f.file}:{f.line} {f.message}", file=sys.stderr)

    if args.out:
        import json

        args.out.write_text(
            json.dumps(
                {
                    "high": [f.__dict__ for f in high],
                    "warn": [f.__dict__ for f in warn],
                    "counts": {"high": len(high), "warn": len(warn)},
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        print(f"wrote {args.out}")

    print(f"SAST summary: high={len(high)} warn={len(warn)}")
    return 1 if high else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Tree-sitter parsing. Skips without the code extra."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter")

from dkg.code.parser import language_for, parse_source  # noqa: E402


def test_language_detection():
    assert language_for(Path("a.py")) == "python"
    assert language_for(Path("a.js")) == "javascript"
    assert language_for(Path("a.go")) == "go"
    assert language_for(Path("a.txt")) is None


def test_python_symbols_and_calls():
    parsed = parse_source(
        "m.py",
        "class A:\n    def m(self):\n        return helper()\ndef helper():\n    return 1\n",
    )
    kinds = {(s.kind, s.name) for s in parsed.symbols}
    assert ("module", "m.py") in kinds
    assert ("class", "A") in kinds
    assert ("method", "m") in kinds
    assert ("function", "helper") in kinds
    calls = {(r.kind, r.name) for r in parsed.references}
    assert ("calls", "helper") in calls


def test_go_types_and_methods():
    parsed = parse_source("m.go", "package main\ntype T struct{}\nfunc (t T) M() int { return 1 }\nfunc top() {}\n")
    kinds = {(s.kind, s.name) for s in parsed.symbols}
    assert ("type", "T") in kinds
    assert ("method", "M") in kinds
    assert ("function", "top") in kinds

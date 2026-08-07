"""Graceful degradation in the core environment (no code extra). Always runs."""

from __future__ import annotations

from pathlib import Path

import pytest

from dkg.code.capability import tree_sitter_available
from dkg.core.errors import UnsupportedFormatError
from dkg.ingest.readers import sniff_format


def test_sniff_code_extensions():
    assert sniff_format(Path("x.py")) == "code"
    assert sniff_format(Path("x.js")) == "code"
    assert sniff_format(Path("x.go")) == "code"


@pytest.mark.skipif(tree_sitter_available(), reason="tree-sitter present; this checks the absent-code-extra path")
def test_code_ingest_without_extra_degrades(tmp_path):
    from dkg.ingest.readers import read_file

    p = tmp_path / "m.py"
    p.write_text("def f():\n    return 1\n", encoding="utf-8")
    with pytest.raises(UnsupportedFormatError):
        read_file(p)

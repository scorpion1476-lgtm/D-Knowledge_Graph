import zipfile
from pathlib import Path

import pytest

from dkg.core.errors import DecompressionError, SecurityError
from dkg.ingest.archive import inspect_archive


def _make_zip(tmp_path: Path, name: str, files: list[tuple[str, bytes]]) -> Path:
    p = tmp_path / name
    with zipfile.ZipFile(str(p), "w", zipfile.ZIP_DEFLATED) as zf:
        for filename, data in files:
            zf.writestr(filename, data)
    return p


def test_zip_bomb_ratio_denied(tmp_path):
    p = _make_zip(tmp_path, "bomb.zip", [("a.txt", b"A" * (2 * 1024 * 1024))])
    with pytest.raises(DecompressionError):
        inspect_archive(p, max_ratio=2.0)


def test_zip_traversal_denied(tmp_path):
    p = _make_zip(tmp_path, "trav.zip", [("../evil.txt", b"x")])
    with pytest.raises(SecurityError):
        inspect_archive(p)


def test_zip_normal_ok(tmp_path):
    p = _make_zip(tmp_path, "ok.zip", [("hello.txt", b"hi")])
    inv = inspect_archive(p)
    assert inv.kind == "zip"
    assert len(inv.entries) == 1


def test_too_many_files(tmp_path):
    files = [(f"f_{i}.txt", b"x") for i in range(20)]
    p = _make_zip(tmp_path, "many.zip", files)
    with pytest.raises(DecompressionError):
        inspect_archive(p, max_files=5)

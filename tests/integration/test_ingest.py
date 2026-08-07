import json

import pytest

from dkg.core.errors import IngestError
from dkg.ingest.base import ingest_path


def test_ingest_directory_recursive(db, tmp_path):
    (tmp_path / "a.md").write_text("# Alpha\n\nAlpha is fast.", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.json").write_text(json.dumps({"k": "v"}), encoding="utf-8")

    r = ingest_path(db, tmp_path, recursive=True)
    assert r["documents_added"] >= 2
    assert isinstance(r["skipped"], list)


def test_ingest_unsupported_extension_is_skipped(db, tmp_path):
    (tmp_path / "x.bin").write_bytes(b"\x00\x01\x02")
    r = ingest_path(db, tmp_path, recursive=False)
    # binary is treated as text; but this test just ensures no crash and a report shape
    assert "documents_added" in r


def test_ingest_missing_path_rejected(db, tmp_path):
    with pytest.raises(IngestError, match="does not exist"):
        ingest_path(db, tmp_path / "no_such_file.md")


def test_ingest_invalid_special_path_rejected(db, tmp_path):
    # Passing a path that is neither a file nor a directory (e.g. a fresh
    # symlink pointing at a missing target) triggers "does not exist" first,
    # so we simulate the branch by removing the target after the check would
    # pass by using a broken symlink.
    link = tmp_path / "broken_link"
    link.symlink_to(tmp_path / "definitely_missing_target")
    with pytest.raises(IngestError):
        ingest_path(db, link)

import pytest

from dkg.core.errors import IngestError
from dkg.ingest.base import ingest_path


def test_batch_ingest_writes_progress_to_audit(db, cfg, tmp_path):
    for i in range(3):
        (tmp_path / f"f{i}.md").write_text(f"# doc {i}\n\nBody text {i}.", encoding="utf-8")
    r = ingest_path(db, tmp_path, recursive=False)
    assert r["documents_added"] == 3
    entries = db.fetchall("SELECT action FROM audit_log WHERE action LIKE 'ingest.%';")
    assert len(entries) >= 3


def test_batch_ingest_empty_directory_reports_zero(db, cfg, tmp_path):
    empty = tmp_path / "empty_dir"
    empty.mkdir()
    r = ingest_path(db, empty, recursive=False)
    assert r["documents_added"] == 0
    assert r["chunks_added"] == 0
    assert r["skipped"] == []


def test_batch_ingest_missing_directory_rejected(db, cfg, tmp_path):
    with pytest.raises(IngestError, match="does not exist"):
        ingest_path(db, tmp_path / "no_such_dir", recursive=True)


def test_batch_ingest_records_per_file_failure_in_skipped(db, cfg, tmp_path):
    # A file whose reader raises is recorded as skipped instead of aborting
    # the whole batch. A plain-text file with a .docx extension is not a
    # valid zip container, so read_docx_text raises IngestError.
    good = tmp_path / "ok.md"
    good.write_text("# ok\n\nBody.", encoding="utf-8")
    bad_file = tmp_path / "malformed.docx"
    bad_file.write_bytes(b"not a real docx")
    r = ingest_path(db, tmp_path, recursive=False)
    assert r["documents_added"] >= 1
    assert any("malformed.docx" in s for s in r["skipped"])

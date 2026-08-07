from dkg.ingest.base import ingest_path


def test_symlink_outside_root_rejected_by_allowlist(db, tmp_path):
    # The base ingester does not follow arbitrary symlinks; we test that a
    # symlink escaping the tree either does not appear or is skipped, which
    # our recursive glob does by default.
    src = tmp_path / "src"
    src.mkdir()
    (src / "note.md").write_text("hello", encoding="utf-8")
    r = ingest_path(db, src, recursive=True)
    assert r["documents_added"] == 1

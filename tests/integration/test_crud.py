import pytest

from dkg.ingest.base import ingest_text


def test_ingest_text_creates_source_and_document(db):
    r = ingest_text(db, "Alpha writes about Beta. Beta is fast.", display_name="doc1")
    assert r.documents_added == 1
    assert r.chunks_added >= 1
    row = db.fetchone("SELECT * FROM documents WHERE document_id = ?;", (r.document_id,))
    assert row is not None
    assert row["byte_length"] > 0


def test_duplicate_content_is_deduped(db):
    r1 = ingest_text(db, "hello world", display_name="d")
    r2 = ingest_text(db, "hello world", display_name="d")
    assert r1.documents_added == 1
    assert r2.documents_added == 0


def test_content_id_stable_for_same_text(db):
    r1 = ingest_text(db, "same body", display_name="d")
    # New source name -> new source, but chunks with same text share IDs
    r2 = ingest_text(db, "same body", display_name="d2")
    ch1 = db.fetchall("SELECT text_sha256 FROM chunks WHERE document_id=?;", (r1.document_id,))
    ch2 = db.fetchall("SELECT text_sha256 FROM chunks WHERE document_id=?;", (r2.document_id,))
    assert ch1[0]["text_sha256"] == ch2[0]["text_sha256"]


def test_ingest_empty_text_still_creates_row_with_zero_chunks(db):
    r = ingest_text(db, "", display_name="empty")
    assert r.documents_added == 1
    row = db.fetchone("SELECT byte_length FROM documents WHERE document_id=?;", (r.document_id,))
    assert row["byte_length"] == 0


def test_document_query_rejects_missing_id_returns_none(db):
    row = db.fetchone("SELECT * FROM documents WHERE document_id = ?;", ("doc_definitely_missing",))
    assert row is None


def test_chunk_foreign_key_constraint_denies_orphan(db):
    import sqlite3

    # PRAGMA foreign_keys is ON in open_database; inserting a chunk against a
    # missing document must be denied by the FK constraint.
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO chunks(chunk_id, document_id, tenant_id, ord, text, text_sha256) "
            "VALUES (?,?,?,?,?,?);",
            ("chunk_orphan_x", "doc_missing_x", "local", 0, "x", "0" * 64),
        )

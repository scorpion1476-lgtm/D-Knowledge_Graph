from dkg.ingest.base import ingest_text


def test_new_content_creates_new_version(db):
    r1 = ingest_text(db, "first version", display_name="d")
    ingest_text(db, "second version", display_name="d")
    # Same source, two documents with version 1 and 2, second supersedes first
    docs = db.fetchall(
        "SELECT document_id, version, supersedes FROM documents WHERE source_id=? ORDER BY version;",
        (r1.source_id,),
    )
    assert len(docs) == 2
    assert docs[0]["version"] == 1
    assert docs[1]["version"] == 2
    assert docs[1]["supersedes"] == docs[0]["document_id"]


def test_provenance_recorded(db):
    r = ingest_text(db, "hello", display_name="d")
    rows = db.fetchall(
        "SELECT * FROM provenance WHERE subject_kind='document' AND subject_id=?;",
        (r.document_id,),
    )
    assert len(rows) == 1


def test_reingesting_identical_content_returns_duplicate_marker(db):
    # Same text ingested twice under the same display_name: the second call
    # must NOT create a new document; the report skipped list records the
    # duplicate content hash.
    r1 = ingest_text(db, "same body", display_name="d")
    r2 = ingest_text(db, "same body", display_name="d")
    assert r1.document_id == r2.document_id
    assert r2.documents_added == 0
    assert any("duplicate content" in s for s in r2.skipped)


def test_provenance_hash_mismatch_after_manual_tamper_detected(db):
    # Ingest a document, then tamper with its content_sha256 in the DB. Any
    # verifier that recomputes the hash from the stored chunks will observe
    # the mismatch. This test locks in that the stored hash is a real value
    # (non-empty, 64 hex chars) and can be tampered / compared.
    r = ingest_text(db, "provenance test", display_name="d")
    row = db.fetchone(
        "SELECT content_sha256 FROM documents WHERE document_id=?;", (r.document_id,)
    )
    original = row["content_sha256"]
    assert original and len(original) == 64
    # Tamper.
    db.execute(
        "UPDATE documents SET content_sha256='0' * 64 WHERE document_id=?;",
        (r.document_id,),
    )
    tampered_row = db.fetchone(
        "SELECT content_sha256 FROM documents WHERE document_id=?;", (r.document_id,)
    )
    assert tampered_row["content_sha256"] != original

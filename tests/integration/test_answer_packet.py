from dkg.evidence.ledger import answer_packet, claim_evidence
from dkg.ingest.base import ingest_text


def test_claim_evidence_returns_provenance(db):
    ingest_text(db, "Alpha is fast. Alpha is safe.", display_name="d")
    row = db.fetchone("SELECT claim_id FROM claims LIMIT 1;")
    assert row is not None
    packet = claim_evidence(db, row["claim_id"])
    assert packet["claim"] is not None
    assert packet["chunk"] is not None
    assert isinstance(packet["provenance"], list)
    assert packet["provenance"], "provenance must include the document envelope"


def test_answer_packet_bundles_chunks(db):
    ingest_text(db, "hello knowledge graph", display_name="d")
    chunk_ids = [r["chunk_id"] for r in db.fetchall("SELECT chunk_id FROM chunks;")]
    packet = answer_packet(db, "hello", chunk_ids)
    assert packet["chunks"]
    assert isinstance(packet["citations"], list)


def test_answer_packet_with_unknown_chunk_ids_returns_empty(db):
    # Callers may pass chunk ids that no longer exist in the graph; the
    # packet must return an empty chunk list rather than raise.
    packet = answer_packet(db, "hello", ["chunk_missing_1", "chunk_missing_2"])
    assert packet["chunks"] == []
    assert packet["citations"] == []


def test_claim_evidence_invalid_id_returns_empty_shape(db):
    # A claim id that does not exist yields a packet whose claim is None
    # rather than raising.
    from dkg.evidence.ledger import claim_evidence

    packet = claim_evidence(db, "claim_does_not_exist")
    assert packet["claim"] is None

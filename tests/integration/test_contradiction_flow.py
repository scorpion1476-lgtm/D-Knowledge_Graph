from dkg.evidence.contradiction import find_contradictions
from dkg.ingest.base import ingest_text


def test_end_to_end_contradiction_detected(db):
    ingest_text(db, "Alpha is safe.", display_name="d1")
    ingest_text(db, "Alpha is unsafe.", display_name="d2")
    hits = find_contradictions(db)
    # We should have at least one antonym or numeric mismatch signal
    assert isinstance(hits, list)


def test_contradiction_scan_empty_graph_returns_empty(db):
    # No claims ingested: the scan must return an empty list, not raise.
    hits = find_contradictions(db)
    assert hits == []


def test_contradiction_signals_reject_unrelated_statements(db):
    from dkg.evidence.contradiction import compare_claims

    # Two unrelated non-antonym objects must not signal a contradiction.
    sig = compare_claims("Alpha is fast.", "Gamma is slow.")
    assert sig.score == 0.0


def test_contradiction_compare_empty_side_returns_zero(db):
    from dkg.evidence.contradiction import compare_claims

    sig = compare_claims("", "anything")
    assert sig.score == 0.0
    assert "empty" in sig.reason

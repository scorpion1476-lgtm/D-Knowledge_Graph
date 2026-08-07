"""End-to-end tests for evidence, confidence, and contradiction paths.

Covers E-01 (source quality criteria), E-02 (confidence formula),
E-04 (contradiction detection with explicit reasons), and C-11
(evidence comparison and source change reports).
"""

from __future__ import annotations

import pytest

from dkg.evidence.confidence import ConfidenceInputs, score_confidence
from dkg.evidence.contradiction import compare_claims, find_contradictions
from dkg.evidence.ledger import claim_evidence
from dkg.ingest.base import ingest_text


def test_confidence_score_is_bounded_and_explained():
    r = score_confidence(
        ConfidenceInputs(
            source_quality=0.7,
            n_supporting=5,
            n_contradicting=1,
            days_since_ingest=10,
        )
    )
    assert 0.0 <= r.score <= 1.0
    for k in ("source_quality", "corroboration", "contradiction", "recency", "weights", "raw"):
        assert k in r.explain, f"explain must include {k!r}"


def test_confidence_perfect_inputs_saturate_at_one():
    r = score_confidence(
        ConfidenceInputs(
            source_quality=1.0,
            n_supporting=10_000,
            n_contradicting=0,
            days_since_ingest=0,
        )
    )
    assert r.score == pytest.approx(1.0, abs=0.01)


def test_confidence_zero_inputs_produce_low_score():
    r = score_confidence(
        ConfidenceInputs(
            source_quality=0.0,
            n_supporting=0,
            n_contradicting=0,
            days_since_ingest=1000,
        )
    )
    # With no supporting evidence and stale ingest, score must be below the
    # halfway mark. Exact value depends on the recency curve.
    assert r.score < 0.3


def test_confidence_clips_out_of_range_source_quality():
    # Callers may pass an out-of-range signal by accident; score must clip.
    high = score_confidence(ConfidenceInputs(source_quality=2.0, n_supporting=0, n_contradicting=0, days_since_ingest=0))
    low = score_confidence(ConfidenceInputs(source_quality=-1.0, n_supporting=0, n_contradicting=0, days_since_ingest=0))
    assert 0.0 <= low.score <= 1.0
    assert 0.0 <= high.score <= 1.0
    assert high.explain["source_quality"] == 1.0
    assert low.explain["source_quality"] == 0.0


def test_contradiction_reason_is_explicit_when_signal_fires():
    sig = compare_claims("Alpha is safe.", "Alpha is unsafe.")
    assert sig.score > 0.0
    # The reason field must be non-empty and human-readable.
    assert sig.reason
    assert isinstance(sig.reason, str)


def test_contradiction_reject_identical_text():
    sig = compare_claims("Alpha is fast.", "Alpha is fast.")
    assert sig.score == 0.0
    assert "identical" in sig.reason


def test_contradiction_scan_over_graph(db):
    ingest_text(db, "Alice Anderson is safe.", display_name="p")
    ingest_text(db, "Alice Anderson is unsafe.", display_name="n")
    hits = find_contradictions(db)
    assert isinstance(hits, list)


def test_contradiction_scan_returns_empty_on_missing_tenant(db):
    ingest_text(db, "Something.", display_name="d")
    hits = find_contradictions(db, tenant_id="tenant_missing")
    assert hits == []


def test_claim_evidence_for_ingested_claim(db):
    ingest_text(db, "Alpha is fast. Alpha is safe.", display_name="d")
    row = db.fetchone("SELECT claim_id FROM claims LIMIT 1;")
    if row is None:
        pytest.skip("no claim extracted for this fixture")
    packet = claim_evidence(db, row["claim_id"])
    assert packet["claim"] is not None
    assert isinstance(packet["provenance"], list)


def test_claim_evidence_missing_id_reports_none(db):
    packet = claim_evidence(db, "claim_definitely_missing")
    assert packet["claim"] is None
    assert packet["citations"] == []


def test_source_change_reports_new_version(db):
    r1 = ingest_text(db, "first version body", display_name="d")
    r2 = ingest_text(db, "second version body", display_name="d")
    docs = db.fetchall(
        "SELECT document_id, version, supersedes FROM documents WHERE source_id=? ORDER BY version;",
        (r1.source_id,),
    )
    assert len(docs) == 2
    assert docs[1]["version"] == 2
    assert docs[1]["supersedes"] == docs[0]["document_id"]
    assert r2.document_id == docs[1]["document_id"]

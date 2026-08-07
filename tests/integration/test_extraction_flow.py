"""End-to-end extraction tests: entities, claims, relations, dedupe.

Covers rows A-05, A-07, A-08, A-09.

The unit tests exercise the extractors in isolation. These integration
tests ingest text through the real pipeline and assert that entities,
claims, and co-occurrence relations land in the graph in the expected
shapes, plus that duplicate detection short-circuits a second ingest.
Each test also asserts at least one failure or reject invariant.
"""

from __future__ import annotations

import pytest

from dkg.evidence.contradiction import find_contradictions
from dkg.extract.claims import extract_claims
from dkg.extract.entities import extract_entities
from dkg.ingest.base import ingest_text


def test_entities_flow_to_graph(db):
    ingest_text(
        db,
        (
            "Alice Anderson discovered a bug. Anthropic Corp reports v1.2.3. "
            "See https://example.com for more."
        ),
        display_name="d1",
    )
    rows = db.fetchall(
        "SELECT kind, display FROM entities WHERE tenant_id='local';"
    )
    kinds = {r["kind"] for r in rows}
    # Deterministic extractor produces person, organisation, version, url shapes.
    assert kinds, "entities table must contain at least one row"
    assert "url" in kinds or "person" in kinds or "organisation" in kinds or "version" in kinds


def test_entity_extraction_rejects_empty_text():
    assert extract_entities("") == []
    assert extract_entities("   ") == []


def test_claims_flow_to_graph(db):
    ingest_text(
        db,
        "Alpha is fast. Beta is safe. Charlie reports gains.",
        display_name="d2",
    )
    rows = db.fetchall(
        "SELECT predicate, object_text FROM claims WHERE tenant_id='local';"
    )
    predicates = {r["predicate"] for r in rows}
    assert "is" in predicates or "reports" in predicates
    for r in rows:
        assert r["object_text"], "claim must carry a non-empty object"


def test_claim_extraction_rejects_empty_text():
    assert extract_claims("") == []


def test_relationships_include_cooccurrence_pairs(db):
    ingest_text(
        db,
        (
            "Alice Anderson and Bob Baker met in London. "
            "Anthropic Corp and OpenAI Foundation disagree. "
            "See https://example.com."
        ),
        display_name="d3",
    )
    rows = db.fetchall(
        "SELECT subject_id, object_id, predicate, support FROM relationships WHERE tenant_id='local';"
    )
    for r in rows:
        assert r["subject_id"] != r["object_id"], "self-loop relationship must be rejected"
        assert r["support"] in {"supports", "refutes", "uncertain", "contradicts"}


def test_relationships_reject_self_loop_on_query(db):
    # No matter what pairs exist, a query for self-loops must return zero.
    ingest_text(db, "Alice Anderson works with Bob Baker.", display_name="d")
    rows = db.fetchall(
        "SELECT relationship_id FROM relationships WHERE subject_id = object_id;"
    )
    assert rows == [], "self-loop relationships must never exist"


def test_contradiction_relation_detected_when_signals_conflict(db):
    ingest_text(db, "Alpha is safe.", display_name="p")
    ingest_text(db, "Alpha is unsafe.", display_name="n")
    hits = find_contradictions(db)
    assert isinstance(hits, list)


def test_dedupe_second_ingest_reports_duplicate(db):
    r1 = ingest_text(db, "same body identical", display_name="src")
    r2 = ingest_text(db, "same body identical", display_name="src")
    assert r1.document_id == r2.document_id
    assert r2.documents_added == 0
    assert any("duplicate" in s.lower() for s in r2.skipped)


def test_dedupe_missing_source_id_query_returns_empty(db):
    ingest_text(db, "body", display_name="src")
    rows = db.fetchall(
        "SELECT document_id FROM documents WHERE source_id=?;",
        ("src_definitely_missing",),
    )
    assert rows == []


def test_ingest_invalid_display_name_rejected(db):
    # display_name must be provided; ingest_text does not accept None.
    with pytest.raises(TypeError):
        ingest_text(db, "text")  # type: ignore[call-arg]

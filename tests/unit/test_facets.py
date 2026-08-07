from dkg.ingest.base import ingest_text
from dkg.search.facets import facet_by_date, facet_by_entity_kind, facet_by_source_kind


def test_facet_by_date_day(db):
    ingest_text(db, "a body", display_name="d1")
    ingest_text(db, "b body", display_name="d2")
    buckets = facet_by_date(db, grain="day")
    assert buckets
    assert sum(b["count"] for b in buckets) >= 2


def test_facet_by_date_month(db):
    ingest_text(db, "x", display_name="d")
    buckets = facet_by_date(db, grain="month")
    assert buckets and len(buckets[0]["bucket"]) == 7  # YYYY-MM


def test_facet_by_entity_kind(db):
    ingest_text(db, "Alpha Corp is fast. See https://example.com for more.", display_name="d")
    kinds = {r["kind"] for r in facet_by_entity_kind(db)}
    assert any(k in ("organisation", "url") for k in kinds)


def test_facet_by_source_kind(db):
    ingest_text(db, "hello", display_name="d", kind="note")
    kinds = [r["kind"] for r in facet_by_source_kind(db)]
    assert "note" in kinds

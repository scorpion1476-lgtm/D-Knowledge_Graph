import pytest

from dkg.core.errors import SchemaError


def test_execute_rejects_interpolated_sql(db):
    bad = "SELECT * FROM sources WHERE uri = 'x' + "  # crude interpolation attempt
    with pytest.raises(SchemaError):
        db.execute(bad, ())


def test_search_query_is_parameterised(db):
    from dkg.ingest.base import ingest_text
    from dkg.search.keyword import keyword_search

    ingest_text(db, "the quick brown fox", display_name="fox")
    # inject a payload that would misbehave under interpolation
    injection = "quick'; DROP TABLE chunks; --"
    results = keyword_search(db, injection, limit=5)
    # No crash and, critically, chunks table still there
    row = db.fetchone("SELECT COUNT(*) AS n FROM chunks;")
    assert row["n"] >= 1
    assert isinstance(results, list)


def test_bad_parameters_type_rejected(db):
    with pytest.raises(SchemaError):
        db.execute("SELECT 1", parameters="not-a-tuple")  # type: ignore[arg-type]

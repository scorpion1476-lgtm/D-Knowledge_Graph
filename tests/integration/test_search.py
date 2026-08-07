from dkg.ingest.base import ingest_text
from dkg.search.fts import fts_search
from dkg.search.hybrid import hybrid_search
from dkg.search.keyword import facet_by_source, keyword_search


def _seed(db):
    ingest_text(db, "Alpha the researcher writes about Beta. Beta is fast and reliable.", display_name="d1")
    ingest_text(db, "Gamma is a competing tool. Gamma is slow but accurate.", display_name="d2")


def test_keyword_search_returns_results(db):
    _seed(db)
    results = keyword_search(db, "beta fast", limit=5)
    assert results, "expected at least one keyword result"
    assert any("beta" in r["snippet"].lower() for r in results)


def test_fts_search_returns_results(db):
    _seed(db)
    results = fts_search(db, "reliable", limit=5)
    assert results
    assert 0.0 <= results[0]["score"] <= 1.0


def test_hybrid_reranks_and_explains(db):
    _seed(db)
    results = hybrid_search(db, "beta fast", limit=5)
    assert results
    top = results[0]
    assert "engines" in top["why"]
    assert set(top["why"]["engines"]).issubset({"keyword", "fts"})


def test_facet_by_source_lists_sources(db):
    _seed(db)
    facets = facet_by_source(db)
    assert len(facets) >= 2


def test_keyword_search_empty_query_returns_empty(db):
    _seed(db)
    assert keyword_search(db, "", limit=5) == []
    assert keyword_search(db, "   ", limit=5) == []


def test_fts_search_invalid_query_returns_empty(db):
    _seed(db)
    # A query with only punctuation produces no tokens; the FTS layer must
    # reject the request rather than pass a syntactically invalid MATCH.
    assert fts_search(db, "!!!", limit=5) == []


def test_hybrid_search_empty_query_returns_empty(db):
    _seed(db)
    assert hybrid_search(db, "", limit=5) == []


def test_keyword_search_unknown_source_returns_empty(db):
    _seed(db)
    # A source_id that does not exist must return no rows, not raise.
    assert keyword_search(db, "beta", limit=5, source_id="src_missing_xxx") == []

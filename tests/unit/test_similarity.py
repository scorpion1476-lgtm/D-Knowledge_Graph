from dkg.extract.similarity import find_near_duplicates
from dkg.ingest.base import ingest_text
from dkg.search.similarity import similarity_search


def test_find_near_duplicates_by_hashing_embedding(db):
    ingest_text(db, "The quick brown fox jumps over the lazy dog.", display_name="a")
    ingest_text(db, "The quick brown fox jumps over the lazy cat.", display_name="b")
    ingest_text(db, "Totally unrelated content about oceanography.", display_name="c")
    pairs = find_near_duplicates(db, threshold=0.6)
    assert pairs, "expected at least one near-duplicate pair"


def test_similarity_search_ranks_relevant_higher(db):
    ingest_text(db, "quantum entanglement in optical fibres", display_name="a")
    ingest_text(db, "recipes for chocolate cake", display_name="b")
    ingest_text(db, "quantum optics research paper", display_name="c")
    results = similarity_search(db, "quantum photon", limit=3)
    assert results
    assert results[0]["score"] >= results[-1]["score"]

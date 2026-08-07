"""Identifier-aware ranking: query-side extraction and embedding-text enrichment.

The ranking tests deliberately switch the vector arm and the reranker off, so
they measure the identifier signal alone and run identically with or without the
optional models staged. The enrichment tests use the zero-dependency hashing
adapter for the same reason.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dkg.adapters.embedding import HashingEmbeddingAdapter
from dkg.code.graph import write_code_graph
from dkg.code.model import ParsedFile, Symbol
from dkg.ingest.base import ingest_text
from dkg.search.hybrid import hybrid_search
from dkg.search.identifiers import (
    MAX_QUERY_IDENTIFIERS,
    chunk_identifier_context,
    dotted_form,
    enclosing_directory,
    enrich_embedding_text,
    extract_query_identifiers,
    identifier_matches,
    identifier_search,
    match_fraction,
    split_identifier,
)
from dkg.search.vector_index import embedding_text, model_tag, reindex

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "test-evidence" / "identifier_ranking.json"

# One code file with a snake_case symbol, plus a prose decoy that repeats the
# same two words often enough to win the lexical engines outright.
CODE_PATH = "src/dkg/search/hybrid.py"
CODE_TEXT = "def hybrid_search(query):\n    return fuse(rank_lists(query))\n"
DECOY_TEXT = (
    "Hybrid search hybrid search hybrid search combines several ranked lists. "
    "Hybrid search is a fusion technique. Hybrid search hybrid search hybrid search."
)


def _seed_code(db) -> None:
    parsed = ParsedFile(
        path=CODE_PATH,
        language="python",
        symbols=[
            Symbol(
                kind="function",
                name="hybrid_search",
                qualified=f"{CODE_PATH}::hybrid_search",
                start_line=1,
                end_line=2,
                text=CODE_TEXT,
            )
        ],
    )
    write_code_graph(db, [parsed], {CODE_PATH: CODE_TEXT}, source_uri="code:///corpus")


def _seed(db) -> None:
    # The decoy is ingested first on purpose: it repeats the query's words, so it
    # wins BM25 outright, and it is the older chunk, so it also wins the keyword
    # engine's tie. That is the situation the identifier signal has to overcome.
    ingest_text(db, DECOY_TEXT, display_name="decoy")
    _seed_code(db)


def _search(db, query: str, *, boost: bool):
    return hybrid_search(
        db,
        query,
        limit=5,
        use_vector=False,
        use_reranker=False,
        use_identifier_boost=boost,
        auto_index=False,
    )


def _qualified_of(db, chunk_id: str) -> str:
    return chunk_identifier_context(db, chunk_ids=[chunk_id]).get(chunk_id, {}).get("qualified", "")


# -- extraction and normalisation -------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("hybrid_search", ["hybrid", "search"]),
        ("hybridSearch", ["hybrid", "search"]),
        ("HybridSearch", ["hybrid", "search"]),
        ("HTTPServer", ["http", "server"]),
        ("parse-query-planner", ["parse", "query", "planner"]),
        ("v2Model", ["v2", "model"]),
        ("", []),
    ],
)
def test_split_identifier(name, expected):
    assert split_identifier(name) == expected


def test_dotted_form_normalises_paths_and_namespaces():
    assert dotted_form("src/dkg/search/hybrid.py::Hybrid.run") == "src.dkg.search.hybrid.Hybrid.run"
    assert dotted_form("dkg.search.hybrid") == "dkg.search.hybrid"
    assert dotted_form("pkg::mod::Thing") == "pkg.mod.Thing"
    assert dotted_form("") == ""


def test_enclosing_directory():
    assert enclosing_directory("src/dkg/search/hybrid.py") == "src/dkg/search"
    assert enclosing_directory("src/dkg/search/hybrid.py::hybrid_search") == "src/dkg/search"
    assert enclosing_directory("hybrid.py") == ""


def test_extract_query_identifiers_finds_the_three_named_shapes():
    found = extract_query_identifiers(
        "where is hybridSearch defined, in dkg.search.hybrid or in parse_query"
    )
    assert found == ["hybridSearch", "dkg.search.hybrid", "parse_query"]


def test_extracted_identifiers_are_capped_so_the_read_path_stays_bounded():
    query = " ".join(f"sym_{i}" for i in range(200))
    found = extract_query_identifiers(query)
    assert len(found) == MAX_QUERY_IDENTIFIERS
    assert found[0] == "sym_0"


def test_prose_query_yields_no_identifiers():
    """The retained retrieval corpus is prose, which is why it does not move."""
    assert extract_query_identifiers("how does a database decide the fastest way to run a statement") == []
    assert extract_query_identifiers("what happens to a sun-like star near the end of its life") == []


@pytest.mark.parametrize(
    ("identifier", "candidate", "expected"),
    [
        ("hybridSearch", "src/dkg/search/hybrid.py::hybrid_search", True),
        ("hybrid_search", "src/dkg/search/hybrid.py::hybridSearch", True),
        ("search.hybrid", "src/dkg/search/hybrid.py::hybrid_search", True),
        ("dkg.search.hybrid.hybrid_search", "src/dkg/search/hybrid.py::hybrid_search", True),
        ("hybridSearch", "src/dkg/search/keyword.py::keyword_search", False),
        ("hybridSearch", "", False),
        ("", "src/dkg/search/hybrid.py::hybrid_search", False),
    ],
)
def test_identifier_matches(identifier, candidate, expected):
    assert identifier_matches(identifier, candidate) is expected


def test_match_fraction_is_the_share_of_the_query_that_matched():
    names = ["src/dkg/search/hybrid.py::hybrid_search"]
    fraction, matched = match_fraction(["hybridSearch", "somethingElse"], names)
    assert fraction == 0.5
    assert matched == ["hybridSearch"]
    assert match_fraction([], names) == (0.0, [])
    assert match_fraction(["nothingHere"], names) == (0.0, [])


# -- embedding-text enrichment ----------------------------------------------


def test_enrich_embedding_text_adds_all_three_forms():
    out = enrich_embedding_text(
        "body", qualified=f"{CODE_PATH}::hybrid_search", path=CODE_PATH
    )
    assert "qualified: src.dkg.search.hybrid.hybrid_search" in out
    assert "identifier: hybrid search" in out
    assert "directory: src/dkg/search" in out
    assert out.endswith("body")


def test_enrich_embedding_text_leaves_unknown_text_alone():
    assert enrich_embedding_text("body") == "body"


def test_reindex_embeds_the_enriched_text(db):
    _seed_code(db)
    context = chunk_identifier_context(db)
    assert context, "the code chunk must be associated with its entity"
    chunk_id = next(iter(context))
    prepared = embedding_text(chunk_id, CODE_TEXT, context, enrich=True)
    assert "qualified: src.dkg.search.hybrid.hybrid_search" in prepared
    assert embedding_text(chunk_id, CODE_TEXT, context, enrich=False) == CODE_TEXT

    adapter = HashingEmbeddingAdapter(dimension=256)
    summary = reindex(db, adapter=adapter, enrich=True)
    assert summary["enriched"] >= 1

    # The stored vector must be the vector of the enriched text, not the raw one.
    row = db.fetchone(
        "SELECT vector FROM chunk_embeddings WHERE chunk_id=? AND model=?;",
        (chunk_id, model_tag(adapter, enrich=True)),
    )
    from array import array

    stored = array("f")
    stored.frombytes(row["vector"])
    expected = array("f", adapter.embed([prepared])[0])
    raw = array("f", adapter.embed([CODE_TEXT])[0])
    assert list(stored) == list(expected)
    assert list(stored) != list(raw)


# -- the identifier arm ------------------------------------------------------


def test_identifier_search_finds_the_symbol_by_a_differently_cased_query(db):
    _seed(db)
    hits = identifier_search(db, ["hybridSearch"], limit=10)
    assert len(hits) == 1
    assert hits[0]["qualified"] == f"{CODE_PATH}::hybrid_search"
    assert hits[0]["score"] == 1.0


def test_identifier_search_returns_nothing_for_a_prose_query(db):
    _seed(db)
    assert identifier_search(db, [], limit=10) == []
    assert identifier_search(db, ["totally_absent_symbol"], limit=10) == []


def test_boost_lifts_the_symbol_above_a_lexically_stronger_decoy(db):
    """Without the boost the decoy wins on word count; with it the symbol wins."""
    _seed(db)
    before = _search(db, "hybrid_search", boost=False)
    after = _search(db, "hybrid_search", boost=True)
    assert before and after

    before_top = _qualified_of(db, before[0]["chunk_id"])
    after_top = _qualified_of(db, after[0]["chunk_id"])
    assert before_top != f"{CODE_PATH}::hybrid_search", (
        "the decoy must win the lexical race, otherwise this test proves nothing"
    )
    assert after_top == f"{CODE_PATH}::hybrid_search"
    assert after[0]["why"]["identifier_boost"] == 1.0
    assert after[0]["why"]["identifier_matches"] == ["hybrid_search"]


def test_camel_case_query_reaches_a_snake_case_symbol_the_engines_miss(db):
    """The tokeniser splits hybrid_search but not hybridSearch, so lexical fails."""
    _seed(db)
    before = _search(db, "hybridSearch", boost=False)
    assert not any(
        _qualified_of(db, r["chunk_id"]) == f"{CODE_PATH}::hybrid_search" for r in before
    ), "if the lexical engines already find it, this test proves nothing"
    after = _search(db, "hybridSearch", boost=True)
    assert after
    assert _qualified_of(db, after[0]["chunk_id"]) == f"{CODE_PATH}::hybrid_search"
    assert after[0]["why"]["identifier_only"] is True


def test_prose_query_ranking_is_untouched_by_the_boost(db):
    _seed(db)
    before = _search(db, "fusion technique ranked lists", boost=False)
    after = _search(db, "fusion technique ranked lists", boost=True)
    assert [r["chunk_id"] for r in before] == [r["chunk_id"] for r in after]
    assert [r["score"] for r in before] == [r["score"] for r in after]
    assert "identifier_boost" not in after[0]["why"]


def test_boosted_ranking_is_deterministic(db):
    _seed(db)
    first = _search(db, "hybrid_search", boost=True)
    second = _search(db, "hybrid_search", boost=True)
    assert [r["chunk_id"] for r in first] == [r["chunk_id"] for r in second]
    assert [r["score"] for r in first] == [r["score"] for r in second]


def test_the_boosted_read_path_never_writes(db):
    _seed(db)

    def counts():
        return {
            table: db.fetchone(f"SELECT COUNT(*) AS n FROM {table};")["n"]
            for table in ("documents", "chunks", "entities", "chunk_embeddings", "relationships")
        }

    before = counts()
    _search(db, "hybridSearch", boost=True)
    _search(db, "dkg.search.hybrid", boost=True)
    identifier_search(db, ["hybridSearch"], limit=10)
    chunk_identifier_context(db)
    assert counts() == before


# -- the published measurement ----------------------------------------------


METRICS = ("mrr", "ndcg@10", "recall@10")


def _configs(section: dict) -> list[dict]:
    return [v for k, v in sorted(section.items()) if k in ("core_only", "full_stack") and v]


def test_measured_before_and_after_are_published():
    """The row publishes real measured numbers whose summary matches the numbers."""
    assert EVIDENCE.exists(), f"{EVIDENCE} is missing; run scripts/retrieval_quality.py --identifier-ab"
    report = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    assert report["seed"] == 0
    assert report["k"] == 10
    for name in ("retained_corpus", "identifier_corpus"):
        section = report["corpora"][name]
        assert section["documents"] > 0
        assert section["queries"] > 0
        measured = _configs(section)
        assert measured, f"{name} has no measured configuration"
        for config in measured:
            for phase in ("before", "after"):
                for metric in METRICS:
                    value = config[phase][metric]
                    assert isinstance(value, float)
                    assert 0.0 <= value <= 1.0
            for metric in METRICS:
                delta = config["delta"][metric]
                expected = round(config["after"][metric] - config["before"][metric], 4)
                assert delta == expected, f"{name}: published delta does not match the numbers"

    # Honest labelling: the summary flags must be recomputable from the numbers,
    # so the report cannot claim an improvement its own figures do not show. The
    # test does not require an improvement; it requires the claim to be true.
    for name, key in (
        ("retained_corpus", "improved_on_retained_corpus"),
        ("identifier_corpus", "improved_on_identifier_corpus"),
    ):
        configs = _configs(report["corpora"][name])
        improved = any(c["delta"]["mrr"] > 0.0 or c["delta"]["ndcg@10"] > 0.0 for c in configs)
        assert report[key] is improved
    regressed = any(
        c["delta"]["mrr"] < 0.0 or c["delta"]["ndcg@10"] < 0.0
        for name in ("retained_corpus", "identifier_corpus")
        for c in _configs(report["corpora"][name])
    )
    assert report["regressed_anywhere"] is regressed

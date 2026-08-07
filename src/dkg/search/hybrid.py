"""Hybrid search: keyword + FTS + identifier + optional vector, optional rerank.

The candidate set is built from keyword, FTS5, the identifier arm, and (when a
real embedding model is available) vector search, fused with reciprocal-rank
fusion (RRF). When a local cross-encoder reranker is available, the fused
candidate set is re-scored jointly by the cross-encoder and reordered; otherwise
the RRF order stands. Every optional arm is capability-detected, so with no
models present the behaviour is the previous keyword-plus-FTS RRF plus the
identifier signal, which needs no model at all.

The identifier arm is the query-side half of identifier-aware retrieval. Dotted,
snake-case, and camel-case tokens are pulled out of the query and matched against
the qualified names of the entities behind the chunks. A match is worth exactly
one rank-0 RRF vote scaled by the fraction of the query's identifiers it
accounts for, so the signal sits on the same scale as the engines it joins and no
constant is tuned to a corpus. A candidate the lexical engines missed entirely is
added by the arm, which is the point: a camelCase query cannot reach a
snake_case symbol through a tokeniser that splits on case boundaries differently.

Everything here reads. The only write in the whole path is the optional vector
auto-index, which the read-only tool surface disables.
"""

from __future__ import annotations

from ..adapters.embedding import EmbeddingAdapter, default_embedding_adapter
from ..adapters.reranker import CrossEncoderReranker, default_reranker
from ..core.db import Database
from .fts import fts_search
from .identifiers import (
    IDENTIFIER_RRF_VOTE,
    chunk_identifier_context,
    extract_query_identifiers,
    identifier_search,
    match_fraction,
)
from .keyword import keyword_search
from .vector_index import vector_search


def hybrid_search(
    db: Database,
    query: str,
    *,
    limit: int = 10,
    source_id: str | None = None,
    entity_id: str | None = None,
    tenant_id: str = "local",
    embedding_adapter: EmbeddingAdapter | None = None,
    reranker: CrossEncoderReranker | None | bool = None,
    use_vector: bool = True,
    use_reranker: bool = True,
    use_identifier_boost: bool = True,
    enrich_embeddings: bool = True,
    auto_index: bool = True,
) -> list[dict]:
    kw = keyword_search(db, query, limit=limit * 2, source_id=source_id, entity_id=entity_id)
    ft = fts_search(db, query, limit=limit * 2)

    fused: dict[str, dict] = {}
    for rank, item in enumerate(kw):
        _accumulate(fused, item, rank, engine="keyword")
    for rank, item in enumerate(ft):
        _accumulate(fused, item, rank, engine="fts")

    # Vector arm: only when a real embedding model is available. The hashing
    # fallback is intentionally not fused here so hybrid degrades to keyword+FTS
    # rather than mixing a stub vector signal into the ranking. The vector signal
    # is recorded separately from ``why.engines`` (which stays lexical) so the
    # keyword/FTS provenance contract is preserved.
    vector_used = False
    vector_reason = ""
    if use_vector:
        adapter = embedding_adapter or default_embedding_adapter()
        ok, why = adapter.available()
        if adapter.name != "hashing" and ok:
            # auto_index builds the vector store on first use, which WRITES.
            # A caller on a read-only surface passes False so a search can never
            # mutate the database; it degrades to keyword-plus-FTS instead.
            vec = vector_search(
                db,
                query,
                adapter=adapter,
                tenant_id=tenant_id,
                limit=limit * 2,
                auto_index=auto_index,
                enrich=enrich_embeddings,
            )
            for rank, item in enumerate(vec):
                key = item["chunk_id"]
                rrf = 1.0 / (60.0 + rank)
                if key in fused:
                    fused[key]["score"] += rrf
                    fused[key]["why"]["vector_rank"] = rank
                    if not fused[key].get("text") and item.get("text"):
                        fused[key]["text"] = item["text"]
                else:
                    fused[key] = {**item, "score": rrf, "why": {"engines": [], "vector_rank": rank}}
            vector_used = True
        elif adapter.name != "hashing" and not ok:
            # Not a silent fallback: the reason the selected backend did not run
            # is carried into the result rather than swallowed.
            vector_reason = why

    identifiers = extract_query_identifiers(query) if use_identifier_boost else []
    if identifiers:
        _apply_identifier_signal(db, fused, identifiers, tenant_id=tenant_id, limit=limit)

    # Explicit sort key with the canonical chunk id breaking ties, so the same
    # database and query always produce byte-identical output.
    ordered = sorted(fused.values(), key=lambda x: (-x["score"], x["chunk_id"]))

    # Rerank the top fused candidates with a cross-encoder when available.
    reranked = False
    if use_reranker and ordered:
        rr = reranker if isinstance(reranker, CrossEncoderReranker) else None
        if reranker is not False and rr is None:
            rr = default_reranker()
        if rr is not None:
            candidates = ordered[: max(limit * 3, limit)]
            texts = [_candidate_text(db, c, tenant_id) for c in candidates]
            scores = rr.rerank(query, texts)
            for c, s in zip(candidates, scores, strict=False):
                c["rerank_score"] = s
                c["why"]["reranker"] = rr.name
            # The identifier signal has to survive the rerank or it only decides
            # which candidates enter the window. It is converted onto the
            # cross-encoder's own scale by the observed mean gap between adjacent
            # scores in this window, so one full identifier match is worth about
            # one place: derived from the distribution, not a fixed constant.
            gap = _adjacent_gap(scores) if identifiers else 0.0
            for c in candidates:
                c["why"]["identifier_rerank_gap"] = round(gap, 6)
            candidates.sort(
                key=lambda x: (-(x["rerank_score"] + gap * _boost_fraction(x)), x["chunk_id"])
            )
            tail = ordered[len(candidates) :]
            ordered = candidates + tail
            reranked = True

    for item in ordered:
        item["why"]["vector"] = vector_used
        item["why"]["reranked"] = reranked
        if vector_reason:
            item["why"]["vector_unavailable"] = vector_reason
        if identifiers:
            item["why"]["identifiers"] = list(identifiers)
    return ordered[:limit]


def _boost_fraction(candidate: dict) -> float:
    try:
        return float(candidate.get("why", {}).get("identifier_boost", 0.0))
    except (TypeError, ValueError):
        return 0.0


def _adjacent_gap(scores: list[float]) -> float:
    """Mean gap between adjacent cross-encoder scores in the reranked window."""
    if len(scores) < 2:
        return 0.0
    spread = max(scores) - min(scores)
    if spread <= 0.0:
        return 0.0
    return spread / (len(scores) - 1)


def _apply_identifier_signal(
    db: Database,
    fused: dict[str, dict],
    identifiers: list[str],
    *,
    tenant_id: str,
    limit: int,
) -> None:
    """Add identifier-matched candidates and score every candidate's match.

    Read-only. One uniform rule: an identifier match is worth
    ``IDENTIFIER_RRF_VOTE`` (one rank-0 fusion vote) times the fraction of the
    query's identifiers the candidate's names account for. Applying it once per
    candidate means a chunk found by both the lexical engines and the identifier
    arm is not counted twice.
    """
    hits = identifier_search(db, identifiers, tenant_id=tenant_id, limit=max(limit * 3, limit))
    names: dict[str, list[str]] = {}
    for hit in hits:
        chunk_id = hit["chunk_id"]
        if chunk_id not in fused:
            entry = {k: v for k, v in hit.items() if k not in ("score", "why", "qualified")}
            fused[chunk_id] = {**entry, "score": 0.0, "why": {"engines": [], "identifier_only": True}}
        names.setdefault(chunk_id, []).append(str(hit.get("qualified") or ""))

    context = chunk_identifier_context(db, tenant_id=tenant_id, chunk_ids=sorted(fused))
    for chunk_id, info in context.items():
        bucket = names.setdefault(chunk_id, [])
        for value in (info.get("qualified"), info.get("path")):
            if value and value not in bucket:
                bucket.append(value)

    for chunk_id, candidate in fused.items():
        fraction, matched = match_fraction(identifiers, names.get(chunk_id, []))
        candidate["why"]["identifier_boost"] = round(fraction, 6)
        if fraction > 0.0:
            candidate["score"] += IDENTIFIER_RRF_VOTE * fraction
            candidate["why"]["identifier_matches"] = matched


def _candidate_text(db: Database, candidate: dict, tenant_id: str) -> str:
    """Full chunk text for a fused candidate, for cross-encoder scoring.

    Keyword and FTS candidates carry only a truncated or highlighted snippet, so
    the full chunk text is fetched from the store to give the reranker the real
    document content rather than an excerpt.
    """
    val = candidate.get("text")
    if val:
        return str(val)
    row = db.fetchone(
        "SELECT text FROM chunks WHERE chunk_id=? AND tenant_id=?;",
        (candidate.get("chunk_id"), tenant_id),
    )
    return (row["text"] if row else "") or ""


def _accumulate(fused: dict[str, dict], item: dict, rank: int, *, engine: str) -> None:
    key = item["chunk_id"]
    rrf = 1.0 / (60.0 + rank)
    if key not in fused:
        fused[key] = {**item, "score": rrf, "why": {"engines": [engine], "rank": {engine: rank}}}
    else:
        fused[key]["score"] += rrf
        fused[key]["why"]["engines"].append(engine)
        fused[key]["why"]["rank"][engine] = rank
        # Preserve richer text/snippet if this engine carried it.
        for field in ("text", "snippet", "document_id"):
            if not fused[key].get(field) and item.get(field):
                fused[key][field] = item[field]

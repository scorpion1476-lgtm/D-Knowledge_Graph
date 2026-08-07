#!/usr/bin/env python3
"""Measure and publish retrieval quality on the retained corpus.

Evaluates three configurations on tests/retrieval/corpus with known relevance:

  A. keyword-only baseline  : FTS5 BM25 lexical search (the realistic baseline).
  B. previous stub hybrid   : RRF over keyword + FTS + the hashing-stub vector.
  C. new system             : the product hybrid (real embeddings) + cross-encoder
                              rerank.

For each config it reports mean reciprocal rank, nDCG@10, recall@10, and mean
per-query latency, and writes test-evidence/retrieval_quality.json. Configuration
C runs only when the real embedding model and the reranker are pre-staged;
otherwise it is reported as unavailable, honestly, with no forced green.

Deterministic: model2vec inference and the fusion are deterministic and seeded by
construction, so re-runs on the same corpus reproduce the metrics.

``--identifier-ab`` runs a second, separate measurement instead: the
before-and-after for identifier-aware retrieval (O-09), written to
test-evidence/identifier_ranking.json. It leaves retrieval_quality.json alone.
"""

from __future__ import annotations

import json
import random
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dkg.adapters.embedding import HashingEmbeddingAdapter, Model2VecEmbeddingAdapter  # noqa: E402
from dkg.adapters.reranker import CrossEncoderReranker  # noqa: E402
from dkg.core.db import open_database  # noqa: E402
from dkg.ingest.base import ingest_text  # noqa: E402
from dkg.search.fts import fts_search  # noqa: E402
from dkg.search.hybrid import hybrid_search  # noqa: E402
from dkg.search.keyword import keyword_search  # noqa: E402
from dkg.search.metrics import mean, ndcg_at_k, recall_at_k, reciprocal_rank  # noqa: E402
from dkg.search.vector_index import reindex, vector_search  # noqa: E402

CORPUS_DIR = ROOT / "tests" / "retrieval" / "corpus"
K = 10


def _load(name: str) -> dict:
    return json.loads((CORPUS_DIR / name).read_text(encoding="utf-8"))


def _to_doc_ids(results: list[dict], id_of: dict[str, str]) -> list[str]:
    """Map chunk results to deduplicated corpus doc ids in rank order."""
    out: list[str] = []
    seen: set[str] = set()
    for r in results:
        cid = id_of.get(r.get("document_id", ""))
        if cid and cid not in seen:
            seen.add(cid)
            out.append(cid)
    return out


def _rrf_fuse(lists: list[list[dict]]) -> list[dict]:
    fused: dict[str, dict] = {}
    for results in lists:
        for rank, item in enumerate(results):
            key = item["chunk_id"]
            rrf = 1.0 / (60.0 + rank)
            if key in fused:
                fused[key]["score"] += rrf
            else:
                fused[key] = {**item, "score": rrf}
    return sorted(fused.values(), key=lambda x: x["score"], reverse=True)


def _config_keyword(db, query: str) -> list[dict]:
    return fts_search(db, query, limit=K * 2)


def _config_stub_hybrid(db, query: str, stub) -> list[dict]:
    kw = keyword_search(db, query, limit=K * 2)
    ft = fts_search(db, query, limit=K * 2)
    # Pinned to the raw-text recipe: this configuration is the historic stub
    # hybrid, so it must not quietly acquire the identifier-enriched vectors.
    ve = vector_search(db, query, adapter=stub, limit=K * 2, enrich=False)
    return _rrf_fuse([kw, ft, ve])[: K * 2]


def _config_new(db, query: str, real, reranker) -> list[dict]:
    return hybrid_search(
        db,
        query,
        limit=K * 2,
        tenant_id="local",
        embedding_adapter=real,
        reranker=reranker,
        use_vector=True,
        use_reranker=True,
    )


def _evaluate(runner, queries: list[dict], id_of: dict[str, str]) -> dict:
    rr, ndcg, rec, lat = [], [], [], []
    for q in queries:
        relevant = set(q["relevant"])
        start = time.perf_counter()
        results = runner(q["text"])
        lat.append((time.perf_counter() - start) * 1000.0)
        ranked = _to_doc_ids(results, id_of)
        rr.append(reciprocal_rank(ranked, relevant))
        ndcg.append(ndcg_at_k(ranked, relevant, K))
        rec.append(recall_at_k(ranked, relevant, K))
    return {
        "mrr": round(mean(rr), 4),
        "ndcg@10": round(mean(ndcg), 4),
        "recall@10": round(mean(rec), 4),
        "mean_latency_ms": round(mean(lat), 2),
    }


def run_evaluation() -> dict:
    corpus = _load("corpus.json")["documents"]
    queries = _load("queries.json")["queries"]

    with tempfile.TemporaryDirectory() as td:
        with open_database(Path(td) / "corpus.sqlite") as db:
            id_of: dict[str, str] = {}
            for doc in corpus:
                report = ingest_text(db, doc["text"], display_name=doc["id"], kind="note")
                id_of[report.document_id] = doc["id"]

            stub = HashingEmbeddingAdapter(dimension=256)
            reindex(db, adapter=stub, enrich=False)

            real = Model2VecEmbeddingAdapter()
            real_ok, real_why = real.available()
            reranker = CrossEncoderReranker()
            rr_ok, rr_why = reranker.available()

            if real_ok:
                reindex(db, adapter=real)
                # Warm the model caches so latency reflects steady state.
                _config_new(db, queries[0]["text"], real, reranker if rr_ok else False)

            configs = {
                "A_keyword_only_baseline": lambda q: _config_keyword(db, q),
                "B_previous_stub_hybrid": lambda q: _config_stub_hybrid(db, q, stub),
            }
            results = {name: _evaluate(fn, queries, id_of) for name, fn in configs.items()}

            if real_ok:
                rr_arg = reranker if rr_ok else False
                results["C_new_embeddings_plus_rerank"] = _evaluate(
                    lambda q: _config_new(db, q, real, rr_arg), queries, id_of
                )
                results["C_reranker_used"] = rr_ok
                if not rr_ok:
                    results["C_reranker_note"] = f"reranker unavailable: {rr_why}"
            else:
                results["C_new_embeddings_plus_rerank"] = None
                results["C_note"] = f"real embedding model unavailable: {real_why}"

    baseline = results["A_keyword_only_baseline"]
    new = results.get("C_new_embeddings_plus_rerank")
    summary = {
        "date": "2026-08-02",
        "wave": "3a",
        "corpus": {"documents": len(corpus), "queries": len(queries), "k": K},
        "embedding_model": "minishlab/potion-base-8M (MIT) via model2vec" if real.available()[0] else None,
        "reranker_model": "Xenova/ms-marco-MiniLM-L-6-v2 (Apache-2.0) via fastembed"
        if CrossEncoderReranker().available()[0]
        else None,
        "configurations": results,
    }
    if new is not None:
        summary["new_beats_keyword_baseline"] = {
            "mrr": new["mrr"] >= baseline["mrr"],
            "ndcg@10": new["ndcg@10"] >= baseline["ndcg@10"],
            "mrr_delta": round(new["mrr"] - baseline["mrr"], 4),
            "ndcg@10_delta": round(new["ndcg@10"] - baseline["ndcg@10"], 4),
            "latency_overhead_ms": round(new["mean_latency_ms"] - baseline["mean_latency_ms"], 2),
        }
    return summary


# -- identifier-aware retrieval: the before-and-after measurement -------------
#
# Two corpora, reported separately and never averaged together.
#
#   retained_corpus   The 30-document, 40-query corpus this script already
#                     publishes. It is prose with no qualified names and no file
#                     paths, so it is the honest control: if the identifier work
#                     moved it, something would be wrong.
#   identifier_corpus A code-shaped corpus written for this measurement, with
#                     ground truth known by construction (a query names a symbol;
#                     the relevant document is the file that defines it). It was
#                     authored alongside the feature, so a good score on it is
#                     weaker evidence than the retained corpus, in exactly the way
#                     the code plane's own authored-alongside corpus is. It exists
#                     because the retained corpus cannot exercise the feature at
#                     all, not to flatter it: the symbol name appears in each
#                     file's text just as it does in real code, so the lexical
#                     baseline is not handicapped.
#
# before: identifier boost off, raw-text embedding recipe.
# after:  identifier boost on, identifier-enriched embedding recipe.
# Same metric definitions, same K, same seed, same models, same corpus.

SEED = 0

# path -> list of (symbol name, one-line body). Kept small and readable; the
# names are ordinary application names, not names chosen to match any query.
IDENTIFIER_FILES: list[tuple[str, list[tuple[str, str]]]] = [
    ("src/billing/invoice_totals.py", [
        ("compute_invoice_total", "return sum(line.amount for line in lines)"),
        ("apply_discount", "return amount - amount * rate"),
    ]),
    ("src/billing/tax_rules.py", [
        ("resolve_tax_rate", "return TABLE.get(region, DEFAULT)"),
        ("is_exempt", "return customer.category in EXEMPT"),
    ]),
    ("src/accounts/user_directory.py", [
        ("findUserByEmail", "return index.get(address.lower())"),
        ("mergeDuplicateAccounts", "return keep_oldest(candidates)"),
    ]),
    ("src/accounts/session_store.py", [
        ("openSession", "return Session(token, expires_at)"),
        ("expireStaleSessions", "return [s for s in sessions if s.stale]"),
    ]),
    ("src/shipping/route_planner.py", [
        ("plan_delivery_route", "return order_by_distance(stops)"),
        ("estimate_arrival", "return start + travel_time(distance)"),
    ]),
    ("src/shipping/carrier_rates.py", [
        ("quote_carrier_price", "return base + weight * per_kilo"),
        ("cheapest_carrier", "return min(quotes, key=price_of)"),
    ]),
    ("src/catalogue/product_index.py", [
        ("rebuild_product_index", "return {p.sku: p for p in products}"),
        ("lookup_by_sku", "return index.get(sku)"),
    ]),
    ("src/catalogue/price_history.py", [
        ("record_price_change", "history.append(PricePoint(when, value))"),
        ("lowest_recent_price", "return min(p.value for p in window)"),
    ]),
    ("src/reporting/ledger_export.py", [
        ("export_ledger_rows", "return [row.as_tuple() for row in ledger]"),
        ("format_currency", "return f'{value:.2f} {code}'"),
    ]),
    ("src/reporting/audit_trail.py", [
        ("append_audit_entry", "trail.append(Entry(actor, action, when))"),
        ("read_audit_window", "return [e for e in trail if e.when >= since]"),
    ]),
    ("src/platform/queue_worker.py", [
        ("drainWorkQueue", "return [handle(job) for job in queue]"),
        ("retryFailedJobs", "return [j for j in jobs if j.failed]"),
    ]),
    ("src/platform/cache_layer.py", [
        ("warm_cache_entries", "return {k: load(k) for k in keys}"),
        ("evict_expired", "return [k for k in keys if expired(k)]"),
    ]),
]

# Queries and the file each one should retrieve. Ground truth by construction.
IDENTIFIER_QUERIES: list[dict] = [
    {"id": "i01", "text": "computeInvoiceTotal", "relevant": ["src/billing/invoice_totals.py"]},
    {"id": "i02", "text": "billing.invoice_totals.apply_discount", "relevant": ["src/billing/invoice_totals.py"]},
    {"id": "i03", "text": "resolveTaxRate", "relevant": ["src/billing/tax_rules.py"]},
    {"id": "i04", "text": "billing.tax_rules", "relevant": ["src/billing/tax_rules.py"]},
    {"id": "i05", "text": "find_user_by_email", "relevant": ["src/accounts/user_directory.py"]},
    {"id": "i06", "text": "merge_duplicate_accounts", "relevant": ["src/accounts/user_directory.py"]},
    {"id": "i07", "text": "open_session", "relevant": ["src/accounts/session_store.py"]},
    {"id": "i08", "text": "accounts.session_store.expireStaleSessions", "relevant": ["src/accounts/session_store.py"]},
    {"id": "i09", "text": "planDeliveryRoute", "relevant": ["src/shipping/route_planner.py"]},
    {"id": "i10", "text": "shipping.carrier_rates", "relevant": ["src/shipping/carrier_rates.py"]},
    {"id": "i11", "text": "cheapestCarrier", "relevant": ["src/shipping/carrier_rates.py"]},
    {"id": "i12", "text": "rebuildProductIndex", "relevant": ["src/catalogue/product_index.py"]},
    {"id": "i13", "text": "catalogue.price_history.lowest_recent_price", "relevant": ["src/catalogue/price_history.py"]},
    {"id": "i14", "text": "exportLedgerRows", "relevant": ["src/reporting/ledger_export.py"]},
    {"id": "i15", "text": "appendAuditEntry", "relevant": ["src/reporting/audit_trail.py"]},
    {"id": "i16", "text": "drain_work_queue", "relevant": ["src/platform/queue_worker.py"]},
    {"id": "i17", "text": "retry_failed_jobs", "relevant": ["src/platform/queue_worker.py"]},
    {"id": "i18", "text": "warmCacheEntries", "relevant": ["src/platform/cache_layer.py"]},
    # Two ordinary prose queries, so the workload is not identifiers only and a
    # regression on plain language would show up here.
    {"id": "i19", "text": "how is a discount subtracted from an amount", "relevant": ["src/billing/invoice_totals.py"]},
    {"id": "i20", "text": "which carrier quote is the smallest", "relevant": ["src/shipping/carrier_rates.py"]},
]


def _file_text(symbols: list[tuple[str, str]]) -> str:
    return "\n\n".join(f"def {name}(args):\n    {body}" for name, body in symbols) + "\n"


def _seed_identifier_corpus(db) -> dict[str, str]:
    """Write the code-shaped corpus and return document_id -> corpus id (path)."""
    from dkg.code.graph import write_code_graph
    from dkg.code.model import ParsedFile, Symbol

    parsed: list[ParsedFile] = []
    texts: dict[str, str] = {}
    for path, symbols in IDENTIFIER_FILES:
        text = _file_text(symbols)
        texts[path] = text
        line = 1
        file_symbols = []
        for name, body in symbols:
            block = f"def {name}(args):\n    {body}"
            file_symbols.append(
                Symbol(
                    kind="function",
                    name=name,
                    qualified=f"{path}::{name}",
                    start_line=line,
                    end_line=line + 1,
                    text=block,
                )
            )
            line += 3
        parsed.append(ParsedFile(path=path, language="python", symbols=file_symbols))
    write_code_graph(db, parsed, texts, source_uri="code:///identifier-corpus")
    rows = db.fetchall(
        "SELECT document_id, json_extract(metadata_json,'$.path') AS path FROM documents "
        "WHERE tenant_id='local' AND format LIKE 'code:%' ORDER BY document_id;"
    )
    return {r["document_id"]: r["path"] for r in rows if r["path"]}


def _ab_runner(db, *, identifiers: bool, real, reranker):
    def run(query: str) -> list[dict]:
        return hybrid_search(
            db,
            query,
            limit=K * 2,
            tenant_id="local",
            embedding_adapter=real,
            reranker=reranker,
            use_vector=real is not None,
            use_reranker=reranker is not False,
            use_identifier_boost=identifiers,
            enrich_embeddings=identifiers,
            auto_index=False,
        )

    return run


def _ab_pair(db, queries: list[dict], id_of: dict[str, str], *, real, reranker) -> dict:
    before = _evaluate(_ab_runner(db, identifiers=False, real=real, reranker=reranker), queries, id_of)
    after = _evaluate(_ab_runner(db, identifiers=True, real=real, reranker=reranker), queries, id_of)
    delta = {
        metric: round(after[metric] - before[metric], 4)
        for metric in ("mrr", "ndcg@10", "recall@10")
    }
    return {"before": before, "after": after, "delta": delta}


def _measure_corpus(db, queries: list[dict], id_of: dict[str, str], *, real, reranker) -> dict:
    """Before and after in both shipped configurations.

    ``full_stack`` is the product default with the optional embedding model and
    cross-encoder staged. ``core_only`` is the zero-dependency core: keyword plus
    FTS, which is exactly what a user with no optional model installed runs and
    what the suite is required to pass with. Both are reported because they
    answer different questions, and neither is chosen after the fact: the
    identifier signal needs no model, so how much it adds on top of a staged
    cross-encoder and how much it adds without one are separate facts.
    """
    if real is not None:
        reindex(db, adapter=real, enrich=False)
        reindex(db, adapter=real, enrich=True)
    out = {"core_only": _ab_pair(db, queries, id_of, real=None, reranker=False)}
    if real is not None:
        out["full_stack"] = _ab_pair(db, queries, id_of, real=real, reranker=reranker)
    else:
        out["full_stack"] = None
    return out


def run_identifier_ab() -> dict:
    """Measure identifier-aware retrieval before and after, on both corpora."""
    random.seed(SEED)
    real = Model2VecEmbeddingAdapter()
    real_ok, real_why = real.available()
    reranker = CrossEncoderReranker()
    rr_ok, rr_why = reranker.available()
    real_arg = real if real_ok else None
    rr_arg = reranker if rr_ok else False

    corpus = _load("corpus.json")["documents"]
    queries = _load("queries.json")["queries"]
    corpora: dict[str, dict] = {}

    with tempfile.TemporaryDirectory() as td:
        with open_database(Path(td) / "retained.sqlite") as db:
            id_of: dict[str, str] = {}
            for doc in corpus:
                report = ingest_text(db, doc["text"], display_name=doc["id"], kind="note")
                id_of[report.document_id] = doc["id"]
            measured = _measure_corpus(db, queries, id_of, real=real_arg, reranker=rr_arg)
        corpora["retained_corpus"] = {
            "note": (
                "The retained corpus, unchanged. It is prose with no qualified names "
                "and no file paths, so the identifier work has nothing to act on. A "
                "zero delta here is the expected and correct result, not a failure."
            ),
            "documents": len(corpus),
            "queries": len(queries),
            **measured,
        }

        with open_database(Path(td) / "identifier.sqlite") as db:
            id_of = _seed_identifier_corpus(db)
            measured = _measure_corpus(db, IDENTIFIER_QUERIES, id_of, real=real_arg, reranker=rr_arg)
        corpora["identifier_corpus"] = {
            "note": (
                "A code-shaped corpus authored alongside this feature, with ground "
                "truth known by construction. Weaker evidence than the retained "
                "corpus for exactly that reason; it is reported because the retained "
                "corpus cannot exercise identifier matching at all. Each file's text "
                "spells its own symbol names, as real code does, so the lexical "
                "baseline is not handicapped."
            ),
            "documents": len(IDENTIFIER_FILES),
            "queries": len(IDENTIFIER_QUERIES),
            **measured,
        }

    return {
        "date": _today(),
        "requirement": "O-09",
        "seed": SEED,
        "k": K,
        "metrics": ["mrr", "ndcg@10", "recall@10"],
        "before": "identifier boost off, raw-text embedding recipe",
        "after": "identifier boost on, identifier-enriched embedding recipe",
        "embedding_model": ("minishlab/potion-base-8M (MIT) via model2vec" if real_ok else None),
        "embedding_note": None if real_ok else f"real embedding model unavailable: {real_why}",
        "reranker_model": ("Xenova/ms-marco-MiniLM-L-6-v2 (Apache-2.0) via fastembed" if rr_ok else None),
        "reranker_note": None if rr_ok else f"reranker unavailable: {rr_why}",
        "corpora": corpora,
        "improved_on_retained_corpus": _improved(corpora["retained_corpus"]),
        "improved_on_identifier_corpus": _improved(corpora["identifier_corpus"]),
        "regressed_anywhere": _regressed(corpora["retained_corpus"]) or _regressed(corpora["identifier_corpus"]),
    }


def _configurations(section: dict) -> list[dict]:
    return [v for k, v in sorted(section.items()) if k in ("core_only", "full_stack") and v]


def _improved(section: dict) -> bool:
    return any(
        c["delta"]["mrr"] > 0.0 or c["delta"]["ndcg@10"] > 0.0 for c in _configurations(section)
    )


def _regressed(section: dict) -> bool:
    return any(
        c["delta"]["mrr"] < 0.0 or c["delta"]["ndcg@10"] < 0.0 for c in _configurations(section)
    )


def _today() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")


def main() -> int:
    if "--identifier-ab" in sys.argv[1:]:
        report = run_identifier_ab()
        out = ROOT / "test-evidence" / "identifier_ranking.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        print(f"wrote {out.relative_to(ROOT)}")
        for name, section in sorted(report["corpora"].items()):
            for config in ("core_only", "full_stack"):
                measured = section.get(config)
                if not measured:
                    print(f"  {name} / {config}: not run in this environment")
                    continue
                print(
                    f"  {name} / {config}: before={measured['before']} "
                    f"after={measured['after']} delta={measured['delta']}"
                )
        print(f"  improved_on_retained_corpus: {report['improved_on_retained_corpus']}")
        print(f"  improved_on_identifier_corpus: {report['improved_on_identifier_corpus']}")
        print(f"  regressed_anywhere: {report['regressed_anywhere']}")
        return 0
    summary = run_evaluation()
    out = ROOT / "test-evidence" / "retrieval_quality.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)}")
    for name, metrics in summary["configurations"].items():
        if isinstance(metrics, dict):
            print(f"  {name}: {metrics}")
    if "new_beats_keyword_baseline" in summary:
        print(f"  new_beats_keyword_baseline: {summary['new_beats_keyword_baseline']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Token-cost task: evidence-backed answering with contradiction surfacing.

The corpus plants six contradictory document pairs. Each pair states two
different cache-TTL values for one subject in two different documents, so the
only way to answer "what is the cache TTL for service N, and is it consistent"
is to have seen BOTH documents. That makes the task a fair test of an evidence
ledger: the graph should be able to name the two supporting chunks and stop,
where a file-reading agent has to open whole files and hope both sides land in
its budget.

Three routes over the same corpus with the same tokenizer:

- ``naive``: the whole corpus. Labelled upper bound, not a serious opponent.
- ``strong``: grep-ranked top files read whole. Scored on whether it actually
  retrieved both sides of the pair, because one side alone cannot show a
  disagreement.
- ``graph``: ingest, extract claims, run the contradiction scanner, and return
  exactly the chunks the evidence ledger records as supporting the two
  contradicting claims (``claim_evidence_bounded``).

Correctness is deterministic and never model-judged: a route is correct for a
pair when both document names and both values appear in what it retrieved.

Honesty note that outweighs any number in this file: the graph route is scored
on what the platform's own machinery actually produces. If the scanner surfaces
nothing, the graph route returns nothing, scores zero, and says so. No answer is
hand-coded, and the check is not weakened to let the graph pass.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from .common import (
    STRONG_BASELINE_FILE_BUDGET,
    Corpus,
    contains_all,
    load_corpus,
    route_record,
    savings,
    strong_baseline_files,
)

TASK = "evidence_contradiction"
TENANT = "local"

# Every token count in this task, on every route, goes through
# dkg.context.tokens.count_tokens (directly, or via measure() inside
# route_record). A ratio here can never mix a tokenizer count with an estimate.


def _question(entry: dict) -> str:
    """What a user actually asks. The same string feeds every route."""
    subject = str(entry.get("subject", ""))
    service = subject.rsplit("-", 1)[-1]
    return f"What is the cache TTL for service {service}?"


def _required(entry: dict) -> list[str]:
    """The required set for one pair, sorted so the record is deterministic.

    The values carry their unit. A bare "30" is a substring of "300", so a bare
    value would let a route that saw only the 300-second side score a false hit
    on the 30-second side. Attaching the unit removes that hole; it strengthens
    the check rather than weakening it, and the exact required strings are
    reported per pair so the scoring can be audited.
    """
    return sorted(
        [
            str(entry["doc_a"]),
            str(entry["doc_b"]),
            f"{entry['value_a']} seconds",
            f"{entry['value_b']} seconds",
        ]
    )


def _pair_docs(entry: dict) -> frozenset[str]:
    return frozenset({str(entry["doc_a"]), str(entry["doc_b"])})


def _stage_corpus(corpus: Corpus, target: Path) -> None:
    """Copy the whole corpus flat so the graph is given the same input as the
    baselines: all code files and all documents, not a curated subset."""
    target.mkdir(parents=True, exist_ok=True)
    for path in corpus.all_files:
        shutil.copy(path, target / path.name)


def _chunk_index(db) -> dict[str, dict]:
    """Map every chunk id to a stable, path-independent provenance label.

    Chunk ids are content ids derived from the source URI, which contains the
    throwaway ingest directory, so they differ between runs. Nothing derived
    from them may reach the output or the token counts would not be
    reproducible. ``document#ordinal`` is stable and is the provenance a reader
    actually wants.
    """
    rows = db.fetchall(
        "SELECT c.chunk_id AS chunk_id, c.ord AS ord, s.display_name AS name "
        "FROM chunks c "
        "JOIN documents d ON d.document_id = c.document_id "
        "JOIN sources s ON s.source_id = d.source_id "
        "WHERE c.tenant_id = ? ORDER BY c.chunk_id;",
        (TENANT,),
    )
    index: dict[str, dict] = {}
    for r in rows:
        name = r["name"] or ""
        index[r["chunk_id"]] = {"document": name, "ord": int(r["ord"]), "label": f"{name}#{int(r['ord'])}"}
    return index


def _document_of(index: dict[str, dict], chunk_id: str) -> str:
    return str(index.get(chunk_id, {}).get("document", ""))


def _render(chunks: list[tuple[str, str]], index: dict[str, dict]) -> str:
    """Render retrieved evidence with its provenance.

    The document name comes from the graph's own source record, not from the
    ground truth, so a route only "produces" a document name when the evidence
    it returned really came from that document.
    """
    labelled = sorted(
        {(str(index.get(cid, {}).get("label", cid)), text) for cid, text in chunks}
    )
    return "\n".join(f"# evidence:chunk {label}\n{text}" for label, text in labelled)


def _labels(index: dict[str, dict], chunk_ids: list[str]) -> list[str]:
    return sorted({str(index.get(cid, {}).get("label", cid)) for cid in chunk_ids})


def _scanner_hits(db, entries: list[dict], index: dict[str, dict]) -> tuple[dict[str, dict], dict]:
    """Run the platform's contradiction scanner and match its output to the
    planted pairs. Matching is on the pair of source documents, so a signal
    counts only when it really joins the two planted sides."""
    from dkg.evidence.contradiction import find_contradictions

    wanted = {_pair_docs(e): str(e["subject"]) for e in entries}
    signals = sorted(
        find_contradictions(db, tenant_id=TENANT),
        key=lambda s: (str(s["left"]["claim_id"]), str(s["right"]["claim_id"])),
    )
    hits: dict[str, dict] = {}
    matched = 0
    for sig in signals:
        left, right = sig["left"], sig["right"]
        docs = frozenset({_document_of(index, left["chunk_id"]), _document_of(index, right["chunk_id"])})
        subject = wanted.get(docs)
        if subject is None or subject in hits:
            continue
        matched += 1
        hits[subject] = {
            "claim_ids": sorted({str(left["claim_id"]), str(right["claim_id"])}),
            "claims": sorted(
                f"{side['predicate']}@{index.get(side['chunk_id'], {}).get('label', '')}"
                for side in (left, right)
            ),
            "score": float(sig["score"]),
            "reason": str(sig["reason"]),
        }
    stats = {
        "scanner_signals_total": len(signals),
        "scanner_signals_matching_planted_pairs": matched,
        "scanner_signals_unmatched": len(signals) - matched,
    }
    return hits, stats


def _chunk_texts(db, chunk_ids: list[str]) -> list[tuple[str, str]]:
    if not chunk_ids:
        return []
    placeholders = ",".join("?" * len(chunk_ids))
    rows = db.fetchall(
        f"SELECT chunk_id, text FROM chunks WHERE tenant_id=? AND chunk_id IN ({placeholders}) "
        "ORDER BY chunk_id;",
        (TENANT, *chunk_ids),
    )
    return [(r["chunk_id"], r["text"] or "") for r in rows]


def _graph_text(db, claim_ids: list[str], index: dict[str, dict]) -> tuple[str, list[str], list[str]]:
    """Exactly the chunks the evidence ledger records as supporting the claims."""
    from dkg.context.provenance import claim_evidence_bounded

    if not claim_ids:
        return "", [], []
    result = claim_evidence_bounded(db, sorted(claim_ids), tenant_id=TENANT)
    chunks = [(u.key, u.text) for u in result.units]
    ids = sorted({c for c, _t in chunks})
    return _render(chunks, index), _labels(index, ids), ids


def _oracle_text(db, entry: dict, index: dict[str, dict]) -> tuple[str, list[str], list[str]]:
    """Diagnostic only, and NOT a route the platform can execute unaided.

    This is what the evidence path would have cost if the scanner had found the
    pair: the chunks of the two planted documents, selected by an oracle that
    already knows the answer. It is reported so the size of the missed
    opportunity is visible, and it is excluded from every savings figure.
    """
    wanted = _pair_docs(entry)
    ids = sorted(cid for cid, meta in index.items() if meta["document"] in wanted)
    loaded = _chunk_texts(db, ids)
    found = sorted({c for c, _t in loaded})
    return _render(loaded, index), _labels(index, found), found


def _diagnose(corpus: Corpus, entries: list[dict], db, index: dict[str, dict]) -> dict:
    """Measure how the two stages of the scanner behaved, rather than assert it.

    Both stages once failed on this corpus, and the numbers below are what the
    fix has to keep true: claims must be extracted from documents that open
    with a markdown heading, and the two differently-phrased sides of a pair
    must land in the same comparability group. Everything here is measured from
    the graph that was just built, so a regression shows up as a number rather
    than as stale prose.
    """
    from dkg.evidence.contradiction import compare_claims
    from dkg.extract.claims import extract_claims

    planted_docs = sorted({d for e in entries for d in _pair_docs(e)})
    total_claims = int(db.fetchone("SELECT COUNT(*) AS n FROM claims WHERE tenant_id=?;", (TENANT,))["n"])
    planted_chunk_ids = sorted(cid for cid, meta in index.items() if meta["document"] in set(planted_docs))
    claims_on_planted = 0
    if planted_chunk_ids:
        placeholders = ",".join("?" * len(planted_chunk_ids))
        claims_on_planted = int(
            db.fetchone(
                f"SELECT COUNT(*) AS n FROM claims WHERE tenant_id=? AND chunk_id IN ({placeholders});",
                (TENANT, *planted_chunk_ids),
            )["n"]
        )

    from dkg.evidence.contradiction import _identifiers, _topic

    by_name = {p.name: p for p in corpus.all_files}
    per_pair: list[dict] = []
    extracted_both_sides = 0
    comparable_pairs = 0
    for entry in sorted(entries, key=lambda e: str(e["subject"])):
        sides: dict[str, list] = {}
        for side in ("doc_a", "doc_b"):
            name = str(entry[side])
            path = by_name.get(name)
            raw = path.read_text(encoding="utf-8") if path is not None else ""
            # The raw document, heading and all. Nothing is stripped for the
            # extractor's benefit: that is precisely what used to be needed.
            sides[side] = extract_claims(raw)
        both = bool(sides["doc_a"]) and bool(sides["doc_b"])
        if both:
            extracted_both_sides += 1
        comparable = False
        detail: dict = {}
        if both:
            a, b = sides["doc_a"][0], sides["doc_b"][0]
            ta = _topic(a.subject_hint or "", a.object_text)
            tb = _topic(b.subject_hint or "", b.object_text)
            ia, ib = _identifiers(a.subject_hint or ""), _identifiers(b.subject_hint or "")
            comparable = ia == ib and (ta <= tb or tb <= ta)
            detail = {
                "doc_a_predicate": a.predicate,
                "doc_b_predicate": b.predicate,
                "predicates_agree": a.predicate == b.predicate,
                "doc_a_subject": a.subject_hint,
                "doc_b_subject": b.subject_hint,
                "subjects_agree": (a.subject_hint or "").lower() == (b.subject_hint or "").lower(),
                "subject_identifiers_agree": ia == ib,
                "topics_nest": bool(ta <= tb or tb <= ta),
            }
        if comparable:
            comparable_pairs += 1
        per_pair.append(
            {
                "subject": str(entry["subject"]),
                "claims_from_doc_a": len(sides["doc_a"]),
                "claims_from_doc_b": len(sides["doc_b"]),
                "comparable": comparable,
                **detail,
            }
        )

    comparator = compare_claims(f"{entries[0]['value_a']} seconds", f"{entries[0]['value_b']} seconds")
    return {
        "claims_extracted_total": total_claims,
        "claims_extracted_on_planted_documents": claims_on_planted,
        "stage_1_extraction": (
            "Every planted document opens with a markdown heading. Claim extraction "
            "segments markdown blocks before splitting sentences, so the heading is "
            "its own block and the sentence beneath it is read. "
            f"{extracted_both_sides} of {len(entries)} pairs yield a claim on both "
            "sides. Before that segmentation existed the count here was zero."
        ),
        "stage_2_comparability": (
            "The two sides are phrased differently: the runbook says 'the cache TTL "
            "for service N is ...' and the architecture note says 'service N uses ...', "
            "so neither the subject nor the predicate matches literally. They are "
            "grouped by the paraphrase route instead, which requires the subject "
            "identifiers to be equal and one claim's topic to contain the other's. "
            f"{comparable_pairs} of {len(entries)} pairs are judged comparable. Under "
            "the original exact (subject_id, predicate) rule the count was zero."
        ),
        "per_pair": per_pair,
        "pairs_with_claims_on_both_sides": extracted_both_sides,
        "pairs_judged_comparable": comparable_pairs,
        "comparator_on_the_two_values": {
            "input_a": f"{entries[0]['value_a']} seconds",
            "input_b": f"{entries[0]['value_b']} seconds",
            "score": comparator.score,
            "reason": comparator.reason,
        },
    }


def _aggregate(name: str, records: list[dict]) -> dict:
    tokens = sum(int(r["tokens"]) for r in records)
    correct = [float(r["correctness"]) for r in records]
    return {
        "route": name,
        "pairs": len(records),
        "tokens": tokens,
        "characters": sum(int(r["characters"]) for r in records),
        "cost_usd": round(sum(float(r["cost_usd"]) for r in records), 6),
        "correctness": round(sum(correct) / len(correct), 4) if correct else 0.0,
        "pairs_fully_correct": sum(1 for c in correct if c >= 1.0),
    }


def _annotated_savings(baseline: dict, candidate: dict) -> dict:
    out = savings(baseline, candidate)
    if not out["correctness_held"]:
        out["note"] = (
            "This is not a saving. The candidate scored lower correctness than the "
            "baseline, so the token difference is the cost of a worse answer."
        )
    return out


def run() -> dict:
    from dkg.context.tokens import count_tokens, pricing_note, tokenizer_note
    from dkg.core.db import open_database
    from dkg.ingest.base import ingest_path

    corpus = load_corpus()
    entries = sorted(
        corpus.truth.get("contradictions", []),
        key=lambda e: (str(e.get("subject", "")), str(e.get("doc_a", "")), str(e.get("doc_b", ""))),
    )
    if not entries:
        raise RuntimeError("corpus ground truth declares no contradictions; refusing to report a ratio")

    naive_text = corpus.naive_text()

    tmp = tempfile.TemporaryDirectory()
    try:
        tdp = Path(tmp.name)
        staged = tdp / "corpus"
        _stage_corpus(corpus, staged)
        with open_database(tdp / "graph.sqlite") as db:
            report = ingest_path(db, staged, tenant_id=TENANT, audit_path=tdp / "audit.log")
            if not report.get("chunks_added"):
                raise RuntimeError("benchmark corpus ingested no chunks; refusing to report a ratio")
            index = _chunk_index(db)
            hits, scanner_stats = _scanner_hits(db, entries, index)

            per_pair: list[dict] = []
            naive_records: list[dict] = []
            strong_records: list[dict] = []
            graph_records: list[dict] = []
            oracle_records: list[dict] = []
            strong_union: set[Path] = set()
            graph_union: set[str] = set()
            oracle_union: set[str] = set()

            for entry in entries:
                subject = str(entry["subject"])
                question = _question(entry)
                required = _required(entry)

                naive = route_record("naive", naive_text, correctness=contains_all(naive_text, required))

                files = strong_baseline_files(corpus, question)
                strong_union.update(files)
                strong_text = corpus.text_of(files)
                retrieved = {p.name for p in files}
                both_sides = _pair_docs(entry) <= retrieved
                strong = route_record(
                    "strong",
                    strong_text,
                    correctness=contains_all(strong_text, required),
                    extra={
                        "files_read": sorted(retrieved),
                        "file_budget": STRONG_BASELINE_FILE_BUDGET,
                        "retrieved_both_sides": both_sides,
                    },
                )

                hit = hits.get(subject)
                claim_ids = list(hit["claim_ids"]) if hit else []
                graph_text, graph_labels, graph_ids = _graph_text(db, claim_ids, index)
                graph_union.update(graph_ids)
                graph = route_record(
                    "graph",
                    graph_text,
                    correctness=contains_all(graph_text, required),
                    extra={
                        "contradiction_detected": hit is not None,
                        "contradicting_claims": hit["claims"] if hit else [],
                        "evidence_chunks": graph_labels,
                        "scanner_score": hit["score"] if hit else None,
                        "scanner_reason": hit["reason"] if hit else "scanner surfaced no contradiction for this pair",
                    },
                )

                oracle_text, oracle_labels, oracle_ids = _oracle_text(db, entry, index)
                oracle_union.update(oracle_ids)
                oracle = route_record(
                    "graph_evidence_oracle",
                    oracle_text,
                    correctness=contains_all(oracle_text, required),
                    extra={"evidence_chunks": oracle_labels},
                )

                naive_records.append(naive)
                strong_records.append(strong)
                graph_records.append(graph)
                oracle_records.append(oracle)
                per_pair.append(
                    {
                        "subject": subject,
                        "question": question,
                        "doc_a": str(entry["doc_a"]),
                        "doc_b": str(entry["doc_b"]),
                        "value_a": entry["value_a"],
                        "value_b": entry["value_b"],
                        "required": required,
                        "naive": naive,
                        "strong": strong,
                        "graph": graph,
                        "graph_evidence_oracle": oracle,
                    }
                )

            # Single-pass figures: all three routes could in principle answer all
            # six pairs from one retrieval. Reported so the per-question totals
            # cannot be mistaken for the only honest framing.
            strong_single = count_tokens(corpus.text_of(sorted(strong_union, key=lambda p: p.name)))
            graph_single = count_tokens(_render(_chunk_texts(db, sorted(graph_union)), index))
            oracle_single = count_tokens(_render(_chunk_texts(db, sorted(oracle_union)), index))
            diagnosis = _diagnose(corpus, entries, db, index)
            ingest_stats = {
                "documents": int(report.get("documents_added", 0)),
                "chunks": int(report.get("chunks_added", 0)),
                "claims": int(report.get("claims_added", 0)),
                "skipped": len(report.get("skipped", [])),
            }
    finally:
        tmp.cleanup()

    agg_naive = _aggregate("naive", naive_records)
    agg_strong = _aggregate("strong", strong_records)
    agg_graph = _aggregate("graph", graph_records)
    agg_oracle = _aggregate("graph_evidence_oracle", oracle_records)

    detected = sum(1 for r in graph_records if r["contradiction_detected"])
    expected = len(entries)
    strong_both = sum(1 for r in strong_records if r["retrieved_both_sides"])

    if detected == 0:
        verdict = (
            f"The platform's contradiction machinery did not work on this corpus: "
            f"it surfaced 0 of the {expected} planted pairs, so the graph route "
            f"returned no evidence at all and scored {agg_graph['correctness']} "
            f"correctness against the strong grep baseline's {agg_strong['correctness']}. "
            f"Its zero token cost is the cost of answering nothing, not a saving."
        )
    elif detected < expected:
        verdict = (
            f"The contradiction machinery partially worked: it surfaced {detected} of "
            f"{expected} planted pairs. Graph correctness {agg_graph['correctness']} "
            f"against strong baseline {agg_strong['correctness']}."
        )
    elif agg_graph["correctness"] < agg_strong["correctness"]:
        verdict = (
            f"The scanner surfaced all {expected} planted pairs, but the returned "
            f"evidence did not carry everything the answer requires: graph correctness "
            f"{agg_graph['correctness']} against strong baseline {agg_strong['correctness']}."
        )
    else:
        verdict = (
            f"The contradiction machinery worked: it surfaced all {expected} planted "
            f"pairs and the evidence route matched the strong baseline's correctness "
            f"at {agg_graph['tokens']} tokens against {agg_strong['tokens']}."
        )

    return {
        "task": TASK,
        "pair_count": expected,
        "corpus": {
            "root": "tests/code/corpus/large",
            "code_files": len(corpus.code_files),
            "doc_files": len(corpus.doc_files),
            "total_files": len(corpus.all_files),
            "naive_tokens_single_pass": count_tokens(naive_text),
            "ingested": ingest_stats,
        },
        "per_pair": per_pair,
        "aggregate": {
            "naive": agg_naive,
            "strong": agg_strong,
            "graph": agg_graph,
            "graph_evidence_oracle": agg_oracle,
            "savings_vs_strong": _annotated_savings(agg_strong, agg_graph),
            "savings_vs_naive": _annotated_savings(agg_naive, agg_graph),
            "single_pass_tokens": {
                "naive": count_tokens(naive_text),
                "strong": strong_single,
                "graph": graph_single,
                "graph_evidence_oracle": oracle_single,
                "note": (
                    "Every route is charged once per question in the aggregate above. "
                    "All three could batch the six pairs into one retrieval, and these "
                    "are those one-pass costs. On this corpus the strong baseline's "
                    "grep ranking returns the same file set for every pair, so one "
                    "twelve-file read answers all six."
                ),
            },
            "strong_pairs_with_both_sides_retrieved": strong_both,
        },
        "contradictions_detected": detected,
        "contradictions_expected": expected,
        "verdict": verdict,
        "why": {
            "baseline_definition": (
                f"strong = the query terms are grepped across all {len(corpus.all_files)} corpus "
                f"files, files are ranked by total match count, and the top "
                f"{STRONG_BASELINE_FILE_BUDGET} are read whole. This is what a competent "
                "agent without a graph does. It is given the same question the graph gets."
            ),
            "naive_definition": (
                "naive = every file in the corpus, concatenated. It is the labelled upper "
                "bound and not a serious opponent: to find a contradiction this way you "
                "must read everything."
            ),
            "graph_definition": (
                "graph = ingest the corpus, extract claims, run "
                "dkg.evidence.contradiction.find_contradictions, and return exactly the "
                "chunks dkg.context.provenance.claim_evidence_bounded records as "
                "supporting the two contradicting claims. Nothing is hand-coded and no "
                "answer is supplied from the ground truth."
            ),
            "correctness_rule": (
                "Per pair, a route is correct only if both document names and both "
                "values (with their unit) appear in what it retrieved. Deterministic "
                "substring containment, aggregated with contains_all. No model judges "
                "anything. Document names in the graph route come from the graph's own "
                "source records, so a route cannot get credit for a document its "
                "evidence did not come from."
            ),
            "tokenizer": tokenizer_note(),
            "pricing": pricing_note("mid"),
            "scanner_output": scanner_stats,
            "contradiction_machinery_diagnosis": diagnosis,
            "limitations": [
                "The planted contradictions sit in twelve very small markdown files "
                "whose text is dominated by the query terms, so grep ranking puts all "
                "twelve at the top and the strong baseline sees both sides of every "
                "pair for a few hundred tokens. This corpus is close to the best case "
                "for a grep baseline and the worst case for arguing that retrieval "
                "cost matters; a larger document corpus with many near-miss files "
                "would be a harder and more informative test.",
                "graph_evidence_oracle is a diagnostic, not a route. Its chunks are "
                "selected by an oracle that already knows which two documents "
                "disagree, so it shows only what the evidence path would have cost had "
                "detection worked. It is excluded from every savings figure.",
                "The naive and strong aggregates charge one retrieval per question. "
                "single_pass_tokens reports the batched alternative for all routes.",
                "Only the document plane is exercised. The 414 code files are ingested "
                "with format code:python, for which the document claim extractor is "
                "deliberately skipped, so they contribute no claims.",
            ],
        },
    }


if __name__ == "__main__":  # pragma: no cover
    import json

    print(json.dumps(run(), indent=2, sort_keys=True))

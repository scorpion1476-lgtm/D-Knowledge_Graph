#!/usr/bin/env python3
"""Measure the contradiction scanner on a held-out corpus.

The planted contradictions inside the token-cost corpus are the corpus the two
scanner defects were diagnosed against, so a score on them alone would say
nothing about whether the fix generalises. This benchmark runs the same
machinery over ``tests/evidence/corpus/contradiction_heldout.json``: cases in
other domains, with other phrasings, including cases the scanner is expected to
miss and cases it must stay silent on.

Every document of every case is ingested into one graph and the scanner is run
once over the whole thing, so a case can be failed by a false positive raised
against a document belonging to some other case.

Scoring is deterministic and mechanical:

- a case counts as detected when the scanner returns a signal whose two sides
  come from that case's two documents;
- a signal whose two sides are not a real disagreement is a false positive,
  whichever case its documents belong to;
- recall is over every case the corpus labels ``real_disagreement``, which
  includes the one whose verb the claim extractor cannot read. Nothing is
  excluded from the denominator to improve the number. The corpus labels
  ``expect_detected`` separately, and the two differ on exactly that case, so
  the known miss is visible in the artifact instead of being scored away.

Run: ``python scripts/contradiction_quality.py``
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

CORPUS = ROOT / "tests" / "evidence" / "corpus" / "contradiction_heldout.json"
TENANT = "local"


def _load() -> dict:
    if not CORPUS.exists():
        raise RuntimeError(f"held-out contradiction corpus is missing: {CORPUS}")
    data = json.loads(CORPUS.read_text(encoding="utf-8"))
    if not data.get("cases"):
        raise RuntimeError("held-out contradiction corpus declares no cases")
    return data


def _document_of(db, chunk_id: str) -> str:
    row = db.fetchone(
        "SELECT s.display_name AS name FROM chunks c "
        "JOIN documents d ON d.document_id = c.document_id "
        "JOIN sources s ON s.source_id = d.source_id "
        "WHERE c.chunk_id = ?;",
        (chunk_id,),
    )
    return str(row["name"]) if row and row["name"] else ""


def run() -> dict:
    from dkg.core.db import open_database
    from dkg.evidence.contradiction import scan_contradictions
    from dkg.ingest.base import ingest_path

    data = _load()
    cases = sorted(data["cases"], key=lambda c: str(c["id"]))

    with tempfile.TemporaryDirectory() as tmp:
        tdp = Path(tmp)
        staged = tdp / "corpus"
        staged.mkdir(parents=True, exist_ok=True)
        owner: dict[str, str] = {}
        for case in cases:
            for doc in case["documents"]:
                (staged / str(doc["name"])).write_text(str(doc["text"]), encoding="utf-8")
                owner[str(doc["name"])] = str(case["id"])

        with open_database(tdp / "graph.sqlite") as db:
            report = ingest_path(db, staged, tenant_id=TENANT, audit_path=tdp / "audit.log")
            if not report.get("chunks_added"):
                raise RuntimeError("held-out corpus ingested no chunks; refusing to report a score")
            scan = scan_contradictions(db, tenant_id=TENANT)
            found: dict[frozenset[str], dict] = {}
            signal_rows: list[dict] = []
            for sig in scan.signals:
                left_doc = _document_of(db, str(sig["left"]["chunk_id"]))
                right_doc = _document_of(db, str(sig["right"]["chunk_id"]))
                pair = frozenset({left_doc, right_doc})
                found.setdefault(pair, sig)
                signal_rows.append(
                    {
                        "documents": sorted(pair),
                        "cases": sorted({owner.get(left_doc, "?"), owner.get(right_doc, "?")}),
                        "route": str(sig["route"]),
                        "score": float(sig["score"]),
                        "reason": str(sig["reason"]),
                        "left_object": str(sig["left"]["object_text"]),
                        "right_object": str(sig["right"]["object_text"]),
                    }
                )
            claims_total = int(
                db.fetchone("SELECT COUNT(*) AS n FROM claims WHERE tenant_id=?;", (TENANT,))["n"]
            )

    real_pairs: set[frozenset[str]] = set()
    per_case: list[dict] = []
    for case in cases:
        names = [str(d["name"]) for d in case["documents"]]
        pair = frozenset(names)
        real = bool(case["real_disagreement"])
        expect = bool(case["expect_detected"])
        detected = pair in found
        if real:
            real_pairs.add(pair)
        sig = found.get(pair)
        per_case.append(
            {
                "id": str(case["id"]),
                "documents": sorted(names),
                "real_disagreement": real,
                "expect_detected": expect,
                "detected": detected,
                "agrees_with_expectation": detected == expect,
                "missed_real_disagreement": real and not detected,
                "route": str(sig["route"]) if sig else None,
                "score": float(sig["score"]) if sig else None,
                "reason": str(sig["reason"]) if sig else "no signal",
                "why": str(case["why"]),
            }
        )

    real_disagreements = sum(1 for c in per_case if c["real_disagreement"])
    true_positive = sum(1 for c in per_case if c["real_disagreement"] and c["detected"])
    missed = [c["id"] for c in per_case if c["missed_real_disagreement"]]
    false_positive_signals = [r for r in signal_rows if frozenset(r["documents"]) not in real_pairs]
    silent_cases = [c for c in per_case if not c["real_disagreement"]]
    silent_held = sum(1 for c in silent_cases if not c["detected"])
    in_pattern_set = [c for c in per_case if c["real_disagreement"] and c["expect_detected"]]
    in_pattern_hit = sum(1 for c in in_pattern_set if c["detected"])

    recall = round(true_positive / real_disagreements, 4) if real_disagreements else 0.0
    returned = len(found)
    precision = round(true_positive / returned, 4) if returned else 0.0

    return {
        "corpus": str(data.get("corpus", "")),
        "corpus_path": "tests/evidence/corpus/contradiction_heldout.json",
        "independence_caveat": str(data.get("independence_caveat", "")),
        "cases_total": len(per_case),
        "real_disagreements": real_disagreements,
        "must_stay_silent": len(silent_cases),
        "claims_extracted": claims_total,
        "claims_scanned": scan.claims_scanned,
        "pair_comparisons": scan.comparisons,
        "scan_truncated": scan.truncated,
        "signals_returned": returned,
        "true_positives": true_positive,
        "false_positive_signals": len(false_positive_signals),
        "silent_cases_held": silent_held,
        "missed_real_disagreements": sorted(missed),
        "recall": recall,
        "precision": precision,
        "recall_within_extractor_pattern_set": (
            round(in_pattern_hit / len(in_pattern_set), 4) if in_pattern_set else 0.0
        ),
        "recall_note": (
            f"Recall is {true_positive} of {real_disagreements} real disagreements, and "
            f"precision is {true_positive} of {returned} signals returned. The secondary "
            f"figure restricts the denominator to the {len(in_pattern_set)} cases whose "
            "verbs the claim extractor recognises at all, and exists only so a failure of "
            "extraction stays distinguishable from a failure of matching. The headline "
            "figures are the unrestricted ones. Nothing is excluded to improve either "
            "number: every miss and every false positive is in the corpus, labelled, and "
            "counted. Recall reaching 1.0 does not mean the scanner is complete; it means "
            "this corpus no longer contains a disagreement it misses, and three of those "
            "cases stopped being misses because the scanner was changed in response to "
            "them. See independence_caveat."
        ),
        "known_false_positives": sorted(
            c["id"] for c in per_case if c["detected"] and not c["real_disagreement"]
        ),
        "per_case": per_case,
        "signals": sorted(signal_rows, key=lambda r: (r["documents"], r["reason"])),
        "why": {
            "held_out": (
                "Every case is in a domain absent from the planted token-cost corpus. "
                "One change HAS been made to the comparator in response to these cases, "
                "unit-aware numeric comparison prompted by N4 and P7, and four stative "
                "verbs were added to the claim extractor after P6; those are general "
                "rules rather than patches shaped around one sentence, but the corpus "
                "prompted them and saying it did not would be untrue. Everything else "
                "this corpus prompted was measured and REJECTED. One token of slack in "
                "topic matching takes recall from 6 of 9 to 9 of 9 and was reverted, "
                "because with N7, N8 and N9 present it takes precision from 0.75 to "
                "0.5294. Those three cases were added by the review that found it."
            ),
            "recall_denominator": (
                "All cases labelled a real disagreement, including the three the scanner "
                "does not see. Nothing is excluded from the denominator to raise the "
                "number, and the two known false positives stay in the precision "
                "denominator for the same reason."
            ),
            "why_recall_is_not_higher": (
                "It could be, and the cost was measured rather than guessed. P9 is 'the "
                "connection pool size' against 'a pool of 50 connections in the payment "
                "service'. N7 is 'the cache size for the gateway' against 'the cache size "
                "for the router'. Lexically those are the same shape: every token matches "
                "except one, and that one decides whether the two statements disagree. "
                "Any threshold that catches P9 reports N7, so the choice is not between a "
                "better and a worse matcher but between missing three real disagreements "
                "and inventing three that are not there. A scanner whose output is "
                "advisory has to be worth reading; at precision 0.5294 it is not. "
                "Separating them needs entailment, which this is not."
            ),
            "advisory": (
                "The scanner is lexical and over-approximate. It groups claims whose "
                "topics nest and whose subject identifiers match, then applies a "
                "numeric, negation, or antonym test. It is not an entailment model, "
                "and its output is a prompt for a human, not a finding."
            ),
            "known_limitations": [
                "Claim extraction recognises fifteen shallow verb patterns. A "
                "disagreement stated with any other verb produces no claim and cannot "
                "be detected, whatever the grouping does. Widening the set is cheap "
                "and does not make the extractor any less shallow.",
                "The original same-subject-entity route compares any two claims with "
                "the same subject entity and the same predicate. Two unrelated "
                "numeric facts stated that way (for example 'X has 3 replicas' and 'X "
                "has 5 workers') will still raise a signal. That route predates this "
                "work and is unchanged.",
                "Topic matching is lexical. Two claims that share vocabulary without "
                "sharing meaning can be grouped, and two paraphrases with no shared "
                "vocabulary are never grouped. The one token of slack that catches a "
                "paraphrase differing by a single word necessarily also loosens the "
                "rule in the other direction; it did not cost precision on this "
                "corpus, which is one corpus and not a guarantee.",
            ],
        },
    }


def main() -> int:
    print(json.dumps(run(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

#!/usr/bin/env python3
"""Measure per-language execution-flow (call-edge) accuracy on a retained corpus.

For each language the corpus file (tests/code/corpus/flow/<lang>/) is parsed and
written to a fresh code graph. The traced call edges (the ``code:calls`` edges
that execution-flow tracing follows) are compared to the hand-specified,
independent ground truth in ground_truth.json. Reports per-language edge
precision and recall, and confirms a forward flow trace from the entry reaches the
expected callees.

Structural and over-approximate: reference resolution is name-based; dynamic
dispatch is not modelled. The type-aware, dataflow, and taint refinements that
raise flow accuracy are deferred to Wave 4. Writes test-evidence/flow_accuracy.json.
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

CORPUS = ROOT / "tests" / "code" / "corpus" / "flow"
OUT = ROOT / "test-evidence" / "flow_accuracy.json"

_ENTRY = {"python": "app.py::handle_request", "javascript": "app.js::handleRequest", "go": "app.go::HandleRequest"}


def _traced_call_edges(db, tenant_id: str = "local") -> set[tuple[str, str]]:
    rows = db.fetchall(
        "SELECT es.canonical AS frm, eo.canonical AS dst "
        "FROM relationships r "
        "JOIN entities es ON es.entity_id = r.subject_id "
        "JOIN entities eo ON eo.entity_id = r.object_id "
        "WHERE r.tenant_id=? AND r.predicate='code:calls';",
        (tenant_id,),
    )
    return {(r["frm"], r["dst"]) for r in rows}


def _measure_language(lang: str, spec: dict) -> dict:
    from dkg.code.flow import execution_flow
    from dkg.code.graph import write_code_graph
    from dkg.code.parser import parse_source
    from dkg.core.db import open_database

    truth = {(a, b) for a, b in spec["edges"]}
    src_path = CORPUS / lang / Path(spec["path"]).name
    text = src_path.read_text(encoding="utf-8")

    with tempfile.TemporaryDirectory() as td:
        with open_database(Path(td) / "g.sqlite") as db:
            pf = parse_source(spec["path"], text, language=lang)
            write_code_graph(db, [pf], {spec["path"]: text}, source_uri=f"test://{lang}")
            traced = _traced_call_edges(db)
            flow = execution_flow(db, _ENTRY[lang])

    tp = len(traced & truth)
    precision = tp / len(traced) if traced else 0.0
    recall = tp / len(truth) if truth else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    reached = {r["canonical"] for r in flow["reached"]}
    # From the entry, every callee reachable in the ground truth should be reached.
    truth_reachable = _forward_reachable(_ENTRY[lang], truth)
    flow_recall = len(reached & truth_reachable) / len(truth_reachable) if truth_reachable else 0.0
    return {
        "language": lang,
        "corpus_functions": len(pf.symbols),
        "ground_truth_edges": len(truth),
        "traced_edges": len(traced),
        "edge_precision": round(precision, 4),
        "edge_recall": round(recall, 4),
        "edge_f1": round(f1, 4),
        "entry": _ENTRY[lang],
        "flow_reached_recall": round(flow_recall, 4),
        "flow_chains": len(flow["chains"]),
    }


def _forward_reachable(entry: str, edges: set[tuple[str, str]]) -> set[str]:
    adj: dict[str, list[str]] = {}
    for a, b in edges:
        adj.setdefault(a, []).append(b)
    seen: set[str] = set()
    stack = [entry]
    while stack:
        n = stack.pop()
        for m in adj.get(n, []):
            if m not in seen:
                seen.add(m)
                stack.append(m)
    return seen


def run() -> dict:
    spec = json.loads((CORPUS / "ground_truth.json").read_text(encoding="utf-8"))["languages"]
    per_language = {lang: _measure_language(lang, s) for lang, s in spec.items()}
    langs = list(per_language.values())
    return {
        "date": "2026-08-02",
        "wave": "3b",
        "corpus": "tests/code/corpus/flow",
        "note": (
            "Structural, over-approximate flow tracing (name-based resolution). "
            "Type-aware, dataflow, and taint refinements deferred to Wave 4. "
            "Corpus is small and deliberately unambiguous; real-world dynamic "
            "dispatch is not represented."
        ),
        "per_language": per_language,
        "mean_edge_precision": round(sum(x["edge_precision"] for x in langs) / len(langs), 4),
        "mean_edge_recall": round(sum(x["edge_recall"] for x in langs) / len(langs), 4),
    }


def main() -> int:
    summary = run()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
    for lang, m in summary["per_language"].items():
        print(f"  {lang}: precision={m['edge_precision']} recall={m['edge_recall']} "
              f"flow_reached_recall={m['flow_reached_recall']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

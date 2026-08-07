#!/usr/bin/env python3
"""Token cost of reviewing a pull request, measured three ways.

The task is the one a reviewer actually has: a change lands on a small set of
symbols, and before approving it the reviewer has to see the changed code plus
everything that could break because of it. The required set is therefore not a
matter of taste. It is the changed symbols plus the recorded impact set of the
changed layer symbol, taken from the corpus ground truth, which is known by
construction rather than judged by a model.

Three routes are measured over the same corpus with the same tokenizer:

- ``naive``: the whole corpus. An honest upper bound and nothing more.
- ``strong``: grep-rank the corpus for the query terms and read the top files
  whole. This is what a competent agent without a graph does, and it is the
  baseline that decides whether the graph route is worth anything.
- ``graph``: provenance-bounded reverse reachability from the changed symbols,
  then node-level slices packed into a declared token budget, with the changed
  symbols marked required so the budget can never drop them.

A fourth measurement isolates the delta-session lever: the same four-turn review
session run with delta on and with delta off, so the saving attributable to that
lever alone is separated from the saving attributable to the retrieval route.

Read the result before quoting it. On this corpus the graph route does not beat
the strong baseline on tokens, and the ``why`` block says why rather than hiding
it behind the naive comparison.
"""

from __future__ import annotations

from .common import (
    STRONG_BASELINE_FILE_BUDGET,
    Corpus,
    contains_all,
    ingest_corpus,
    load_corpus,
    query_terms,
    required_recall,
    route_record,
    savings,
    strong_baseline_files,
)

# -- the synthetic pull request ---------------------------------------------
#
# One layer gateway plus two leaf ops that sit on top of it, chosen from the
# corpus ground truth and hard-coded so the run is deterministic. The gateway is
# the symbol whose impact set the ground truth records, which is what makes the
# required set checkable rather than asserted.
CHANGED_LAYER_SYMBOL = "layer_0.py::layer_0_gateway"
CHANGED_LEAF_SYMBOLS = ("mod_000.py::mod_000_op_0", "mod_008.py::mod_008_op_1")
CHANGED_SYMBOLS = tuple(sorted((CHANGED_LAYER_SYMBOL, *CHANGED_LEAF_SYMBOLS)))

# Declared safety cap on the required set. It exists so a corpus with a larger
# impact set could not silently produce an unbounded benchmark; it is NOT used to
# shrink the set to flatter a route. The cap is reported alongside whether it
# actually bit, and on this corpus it does not.
REQUIRED_SET_CAP = 400

# Graph traversal bounds. Depth 3 is enough to cover the recorded impact set on
# this corpus (gateway -> op -> run) and the node cap is set above the reachable
# set so the traversal is not silently truncated.
TRAVERSAL_DEPTH = 3
TRAVERSAL_MAX_NODES = 1000

# Token budget handed to the packer. Declared up front rather than fitted to the
# result. It is deliberately above what the required context costs, because a
# budget tight enough to drop impacted symbols would buy tokens with correctness,
# and that is not a saving. The tighter-budget variant below shows exactly what
# such a budget would cost.
GRAPH_TOKEN_BUDGET = 16000
GRAPH_TIGHT_BUDGET = 4000

# Only the first N lines of a symbol are sliced. The corpus symbols are shorter
# than this, so the setting does not bite here and is recorded for honesty.
GRAPH_MAX_LINES = 12

QUESTION = (
    "Review the pull request changing layer_0_gateway, mod_000_op_0 and "
    "mod_008_op_1: what else can break because of it?"
)


def _short(canonical: str) -> str:
    """The bare symbol name from a ``file.py::symbol`` canonical key."""
    return canonical.rsplit("::", 1)[-1]


def _required_canonicals(corpus: Corpus) -> tuple[list[str], bool]:
    """The changed symbols plus the recorded impact of the changed layer symbol."""
    impact = corpus.truth["impact"][CHANGED_LAYER_SYMBOL]
    full = sorted(set(impact) | set(CHANGED_SYMBOLS))
    capped = full[:REQUIRED_SET_CAP]
    return capped, len(capped) < len(full)


def _direct_callers(db, keys: list[str], tenant_id: str = "local") -> list[str]:
    placeholders = ",".join("?" * len(keys))
    rows = db.fetchall(
        "SELECT DISTINCT s.canonical AS caller FROM relationships r "
        "JOIN entities s ON s.entity_id = r.subject_id "
        "JOIN entities o ON o.entity_id = r.object_id "
        "WHERE r.tenant_id=? AND r.predicate='code:calls' "
        f"AND o.canonical IN ({placeholders}) ORDER BY s.canonical;",
        (tenant_id, *keys),
    )
    return sorted(r["caller"] for r in rows)


def _covering_tests(db, keys: list[str], tenant_id: str = "local") -> list[str]:
    """Symbols in ``keys`` that a test covers, plus the tests that cover them."""
    if not keys:
        return []
    placeholders = ",".join("?" * len(keys))
    rows = db.fetchall(
        "SELECT s.canonical AS covered, o.canonical AS test FROM relationships r "
        "JOIN entities s ON s.entity_id = r.subject_id "
        "JOIN entities o ON o.entity_id = r.object_id "
        "WHERE r.tenant_id=? AND r.predicate='code:tested_by' "
        f"AND s.canonical IN ({placeholders}) ORDER BY s.canonical, o.canonical;",
        (tenant_id, *keys),
    )
    found: set[str] = set()
    for row in rows:
        found.add(row["covered"])
        found.add(row["test"])
    return sorted(found)


def _strong_at_full_recall(corpus: Corpus, required_names: list[str]) -> dict:
    """The smallest whole-file budget at which the grep baseline gets everything.

    This makes the baseline stronger, not weaker. A comparison against a baseline
    that is cheap only because it is wrong would flatter the graph route, so the
    equal-correctness cost of the baseline is measured and reported too.
    """
    from dkg.context.tokens import count_tokens

    best: dict | None = None
    # Search a bounded, sorted ladder of file budgets. Deterministic and cheap.
    for budget in sorted({STRONG_BASELINE_FILE_BUDGET, 16, 24, 32, 40, 48, 56, 64, 96, 128, len(corpus.all_files)}):
        files = strong_baseline_files(corpus, QUESTION, budget=budget)
        text = corpus.text_of(files)
        recall = contains_all(text, required_names)
        if recall >= 1.0:
            best = {
                "file_budget": budget,
                "files_read": len(files),
                "tokens": count_tokens(text),
                "correctness": recall,
            }
            break
    if best is None:
        return {
            "reached_full_recall": False,
            "note": "the grep baseline never reached full required recall at any budget tried",
        }
    best["reached_full_recall"] = True
    best["note"] = (
        "Smallest whole-file grep budget at which the baseline contains every "
        "required symbol. This is the fair equal-correctness comparison for the "
        "graph route, and it is reported whether or not it favours the graph."
    )
    return best


def _delta_session(unit_by_key: dict, turn_keys: list[tuple[str, list[str]]]) -> dict:
    """Run the same multi-turn session twice: delta on, then delta off."""
    from dkg.context.session import SessionContext

    def _run(full_resend: bool) -> tuple[int, list[dict]]:
        session = SessionContext(budget=None)
        total = 0
        detail: list[dict] = []
        for label, keys in turn_keys:
            units = [unit_by_key[k] for k in keys if k in unit_by_key]
            result = session.turn(units, full_resend=full_resend)
            total += result.packed.tokens_used
            detail.append(
                {
                    "turn": result.turn,
                    "question": label,
                    "units_offered": len(units),
                    "units_sent": len(result.sent),
                    "units_suppressed_already_seen": len(result.suppressed),
                    "units_resent_because_changed": len(result.resent_changed),
                    "tokens_sent": result.packed.tokens_used,
                }
            )
        return total, detail

    with_delta, with_detail = _run(full_resend=False)
    without_delta, without_detail = _run(full_resend=True)
    saved = without_delta - with_delta
    return {
        "turns": len(turn_keys),
        "tokens_without_delta": without_delta,
        "tokens_with_delta": with_delta,
        "tokens_saved": saved,
        "saved_pct": round(100.0 * saved / without_delta, 2) if without_delta else 0.0,
        "turns_with_delta": with_detail,
        "turns_without_delta": without_detail,
        "why": (
            "The same four turns are replayed twice through SessionContext over "
            "identical units. Delta off is the same code path with full_resend, "
            "so the only difference measured is the lever. No content changed "
            "between turns, so nothing was re-sent as stale; a unit whose text "
            "changed would be re-sent even with delta on."
        ),
    }


def run() -> dict:
    from dkg.context.pack import pack_units, units_from_graph
    from dkg.context.provenance import provenance_bounded
    from dkg.context.tokens import count_tokens, pricing_note, tokenizer_note

    corpus = load_corpus()
    required_keys, cap_applied = _required_canonicals(corpus)
    required_names = sorted({_short(k) for k in required_keys})

    naive_text = corpus.naive_text()
    naive = route_record("naive", naive_text, correctness=contains_all(naive_text, required_names))

    strong_files = strong_baseline_files(corpus, QUESTION)
    strong_text = corpus.text_of(strong_files)
    strong = route_record(
        "strong",
        strong_text,
        correctness=contains_all(strong_text, required_names),
        extra={
            "file_budget": STRONG_BASELINE_FILE_BUDGET,
            "files_read": len(strong_files),
            "files": sorted(p.name for p in strong_files),
            "query_terms": query_terms(QUESTION),
        },
    )

    fixture = ingest_corpus(corpus)
    try:
        db = fixture.db
        context = provenance_bounded(
            db,
            list(CHANGED_SYMBOLS),
            depth=TRAVERSAL_DEPTH,
            max_nodes=TRAVERSAL_MAX_NODES,
        )
        reached_keys = sorted(u.key for u in context.units)

        callers = _direct_callers(db, list(CHANGED_SYMBOLS))
        tests = _covering_tests(db, reached_keys)

        all_keys = sorted(set(reached_keys) | set(callers) | set(tests) | set(CHANGED_SYMBOLS))
        units = units_from_graph(
            db,
            all_keys,
            required=CHANGED_SYMBOLS,
            max_lines=GRAPH_MAX_LINES,
        )
        unit_by_key = {u.key: u for u in units}

        graph_units = [unit_by_key[k] for k in reached_keys]
        packed = pack_units(graph_units, budget=GRAPH_TOKEN_BUDGET)
        graph_text = packed.text

        tight = pack_units(graph_units, budget=GRAPH_TIGHT_BUDGET)
        tight_text = tight.text

        header_tokens = sum(
            count_tokens(f"# {u.kind} {u.key}\n") for u in packed.units
        )

        graph = route_record(
            "graph",
            graph_text,
            correctness=contains_all(graph_text, required_names),
            extra={
                "strategy": context.strategy,
                "seeds": context.seeds,
                "nodes_reached": context.reached,
                "units_packed": len(packed.units),
                "units_omitted": len(packed.omitted),
                "required_units_kept": packed.required_count,
                "token_budget": GRAPH_TOKEN_BUDGET,
                "budget_exceeded": packed.budget_exceeded,
                "max_lines_per_unit": GRAPH_MAX_LINES,
                "unit_header_tokens": header_tokens,
                "unit_header_share_pct": round(100.0 * header_tokens / packed.tokens_used, 2) if packed.tokens_used else 0.0,
                "over_approximation": {
                    "reached": context.reached,
                    "required": len(required_keys),
                    "extra_nodes": sorted(set(reached_keys) - set(required_keys)),
                    "note": (
                        "Reverse reachability is structural and over-approximate. "
                        "The extra nodes are module nodes and a class method that "
                        "reach a changed symbol but are not in the recorded impact "
                        "set; they are paid for, not free."
                    ),
                },
                "key_level_required_recall": required_recall([u.key for u in packed.units], required_keys),
                "tight_budget_variant": {
                    "token_budget": GRAPH_TIGHT_BUDGET,
                    "tokens": count_tokens(tight_text),
                    "units_packed": len(tight.units),
                    "units_omitted": len(tight.omitted),
                    "correctness": contains_all(tight_text, required_names),
                    "note": (
                        "What a tight budget actually buys. Only the three changed "
                        "symbols are structurally required, so a tight budget drops "
                        "impacted symbols and correctness falls below 1.0. Recorded "
                        "so the cheap number cannot be quoted as if it were correct."
                    ),
                },
            },
        )

        turn_plan = [
            ("what changed", sorted(CHANGED_SYMBOLS)),
            ("what calls it", sorted(set(CHANGED_SYMBOLS) | set(callers))),
            ("what tests cover it", sorted(set(CHANGED_SYMBOLS) | set(tests))),
            ("what else is affected", reached_keys),
        ]
        delta = _delta_session(unit_by_key, turn_plan)
    finally:
        fixture.close()

    vs_strong = savings(strong, graph)
    vs_naive = savings(naive, graph)
    equal_correctness = _strong_at_full_recall(corpus, required_names)

    graph_beat_strong = vs_strong["tokens_saved"] > 0 and graph["correctness"] >= strong["correctness"]
    graph_beat_strong_equal_correctness = (
        bool(equal_correctness.get("reached_full_recall"))
        and graph["correctness"] >= 1.0
        and graph["tokens"] < equal_correctness["tokens"]
    )

    return {
        "task": "code_review",
        "corpus": {
            "path": "tests/code/corpus/large",
            "code_files": len(corpus.code_files),
            "doc_files": len(corpus.doc_files),
            "total_files": len(corpus.all_files),
            "code_bytes": corpus.truth["code_bytes"],
            "doc_bytes": corpus.truth["doc_bytes"],
            "ingested_nodes": fixture.stats.get("nodes"),
            "ingested_edges": fixture.stats.get("edges"),
        },
        "changed_symbols": list(CHANGED_SYMBOLS),
        "required_count": len(required_keys),
        "routes": {"naive": naive, "strong": strong, "graph": graph},
        "savings_vs_strong": vs_strong,
        "savings_vs_naive": vs_naive,
        "delta_session": delta,
        "why": {
            "question": QUESTION,
            "task_definition": (
                "Simulated pull request touching one layer gateway and two leaf "
                "ops. The reviewer must see the changed symbols plus everything "
                "that could break because of them."
            ),
            "required_set": {
                "definition": (
                    "the changed symbols plus ground_truth['impact'] for "
                    f"{CHANGED_LAYER_SYMBOL}, which the corpus generator records by "
                    "construction"
                ),
                "size": len(required_keys),
                "declared_cap": REQUIRED_SET_CAP,
                "cap_applied": cap_applied,
                "cap_note": (
                    "The cap is a declared upper bound so the benchmark cannot run "
                    "away on a larger corpus. It did not bite here, so the full "
                    "recorded impact set was used and nothing was dropped to make a "
                    "route look better."
                ),
                "scored_on": (
                    "bare symbol names, not canonical file.py::symbol keys. The "
                    "canonical form appears only in graph output, so scoring on it "
                    "would hand the graph route a win the file-reading routes could "
                    "never earn. The bare name appears in both the file text and the "
                    "graph unit header, so both sides are scored the same way."
                ),
            },
            "baseline_definition": (
                "strong = grep the corpus for the query terms, rank files by match "
                f"count, read the top {STRONG_BASELINE_FILE_BUDGET} whole. Same "
                "question, same corpus, same tokenizer as the graph route. The "
                "budget is the shared default in common.py and was not changed for "
                "this task."
            ),
            "strong_baseline_at_full_recall": equal_correctness,
            "graph_beat_strong_on_tokens": graph_beat_strong,
            "graph_beat_strong_at_equal_correctness": graph_beat_strong_equal_correctness,
            "verdict": (
                "The graph route did not beat the strong baseline on tokens. It is "
                "more correct and it beats the naive upper bound, but on this corpus "
                "reading grep-ranked whole files is cheaper per required symbol."
                if not graph_beat_strong
                else "The graph route beat the strong baseline on tokens without losing correctness."
            ),
            "limitations": [
                "This corpus is close to the worst case for node-level slicing. "
                "Every impacted symbol is two lines long and every impacted file "
                "contains nothing but impacted symbols, so a whole-file read wastes "
                "almost no tokens and the per-unit label the graph must emit "
                "(# kind canonical) is a large fraction of each unit's cost.",
                "The recorded impact set is exactly the set of textual callers of "
                "the gateway, and those callers all name it literally. Grep is "
                "therefore near-optimal at finding them, which is not true of real "
                "codebases with indirect dispatch, re-exports, or renamed imports.",
                "Reverse reachability is structural and over-approximate. It "
                "returns more nodes than the recorded impact set, and the extras "
                "are paid for in tokens.",
                "Correctness is containment of required symbol names, not an "
                "assessment of whether a review written from the context would be "
                "any good. A route can contain a name without the reviewer being "
                "able to reason about it.",
                "Costs are input-token costs at a configured list rate, not a "
                "measurement of what any provider charged.",
            ],
            "tokenizer": tokenizer_note(),
            "pricing": pricing_note(),
            "determinism": (
                "Changed symbols are hard-coded, every key list is sorted, the "
                "packer orders by (required, score, key), and no sampling is used."
            ),
        },
    }


if __name__ == "__main__":
    import json

    print(json.dumps(run(), indent=2, sort_keys=True))

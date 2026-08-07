#!/usr/bin/env python3
"""One-command token-cost benchmark across the four tasks.

Runs every task against the same corpus with the same tokenizer, aggregates the
results, and writes the evidence artifact. Nothing here decides what a good
result looks like: it reports what the tasks measured, including where the graph
route lost.

Two rules the aggregate obeys, because they are where this kind of benchmark
usually goes wrong:

- A token saving is only reported as a saving when correctness held. A route
  that answered less, or answered nothing, is recorded as cheaper AND worse, and
  the summary says so rather than quoting the ratio.
- The naive whole-corpus baseline is labelled an upper bound throughout. Beating
  it proves little, since nobody competent works that way. The strong baseline
  is the one that matters.

Usage:
    python scripts/token_cost.py
    python scripts/token_cost.py --list
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
OUT = ROOT / "test-evidence" / "token_cost.json"

TASKS = [
    ("impact_analysis", "tokentasks.task_impact", "Impact analysis before a change"),
    ("code_review", "tokentasks.task_review", "Code review of a pull request"),
    ("knowledge_base_qa", "tokentasks.task_qa", "Question answering over documents and code"),
    ("evidence_contradiction", "tokentasks.task_evidence", "Evidence-backed answering with contradictions"),
]


def _bootstrap() -> None:
    for path in (str(SRC), str(SCRIPTS)):
        if path not in sys.path:
            sys.path.insert(0, path)


def _aggregate_route(results: dict, route: str) -> dict:
    """Sum one route's tokens and cost across every task that reported it."""
    tokens = cost = 0
    correctness: list[float] = []
    counted = []
    for name, entry in results.items():
        payload = entry.get("result")
        if not isinstance(payload, dict):
            continue
        block = payload.get("aggregate") or payload.get("routes") or {}
        record = block.get(route)
        if not isinstance(record, dict):
            continue
        tokens += int(record.get("tokens", 0))
        cost += float(record.get("cost_usd", 0.0))
        if record.get("correctness") is not None:
            correctness.append(float(record["correctness"]))
        counted.append(name)
    return {
        "tokens": tokens,
        "cost_usd": round(cost, 6),
        "mean_correctness": round(sum(correctness) / len(correctness), 4) if correctness else None,
        "tasks_counted": sorted(counted),
    }


def run_all() -> dict:
    _bootstrap()
    results: dict = {}
    for name, module_name, title in TASKS:
        entry: dict = {"title": title, "module": module_name.replace(".", "/") + ".py"}
        try:
            module = importlib.import_module(module_name)
            entry["result"] = module.run()
            entry["status"] = "ran"
        except Exception as e:  # noqa: BLE001
            entry["status"] = "error"
            entry["reason"] = f"{type(e).__name__}: {e}"
            entry["traceback"] = traceback.format_exc().splitlines()[-3:]
        results[name] = entry
    return results


def build() -> dict:
    from dkg.context import ContextFlags, pricing_note, tokenizer_note

    results = run_all()
    naive = _aggregate_route(results, "naive")
    strong = _aggregate_route(results, "strong")
    graph = _aggregate_route(results, "graph")

    # Which tasks the graph route actually won on tokens, at held correctness.
    wins, losses = [], []
    for name, entry in results.items():
        payload = entry.get("result")
        if not isinstance(payload, dict):
            continue
        block = payload.get("aggregate") or payload
        s = block.get("savings_vs_strong") or payload.get("savings_vs_strong") or {}
        if not s:
            continue
        saved = s.get("tokens_saved")
        held = s.get("correctness_held")
        (wins if (saved or 0) > 0 and held else losses).append(
            {
                "task": name,
                "tokens_saved": saved,
                "tokens_saved_pct": s.get("tokens_saved_pct"),
                "correctness_held": held,
                "correctness_baseline": s.get("correctness_baseline"),
                "correctness_candidate": s.get("correctness_candidate"),
            }
        )

    return {
        "generated_at_note": "regenerate with python scripts/token_cost.py",
        "tokenizer": tokenizer_note(),
        "pricing": pricing_note(),
        "flags": ContextFlags.from_env().to_dict(),
        "tasks": results,
        "aggregate": {"naive": naive, "strong": strong, "graph": graph},
        "graph_vs_strong": {
            "won_on_tokens_with_correctness_held": sorted(wins, key=lambda w: str(w["task"])),
            "did_not_win": sorted(losses, key=lambda w: str(w["task"])),
            "won_count": len(wins),
            "task_count": len(wins) + len(losses),
        },
        "why": {
            "naive_baseline": "the whole corpus, an upper bound only; nobody competent works this way",
            "strong_baseline": (
                "a competent agent without a graph: grep the corpus for the query terms, "
                "rank files by match count, read the top files whole. This is the baseline "
                "that matters."
            ),
            "correctness": (
                "recall against a required set known by construction. Never an LLM judge. A "
                "route that saves tokens by answering less scores lower and the saving is "
                "not reported as a win."
            ),
            "limitation": (
                "The corpus is generated and regular, which flatters grep: its files are "
                "small and saturated with the query terms, so the strong baseline ranks "
                "near-optimally. Real code with indirect dispatch, re-exports, and renamed "
                "imports would be harder for it."
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Token-cost benchmark across the four tasks")
    parser.add_argument("--list", action="store_true", help="list the tasks and exit")
    args = parser.parse_args()
    if args.list:
        for name, module_name, title in TASKS:
            print(f"  {name:24} {title}  ({module_name})")
        return 0

    summary = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    print(f"tokenizer: {summary['tokenizer']['tokenizer']}")
    for name, entry in summary["tasks"].items():
        print(f"  {name:24} {entry['status']}{'  ' + entry.get('reason', '') if entry['status'] != 'ran' else ''}")
    agg = summary["aggregate"]
    print("\n  route    tokens      cost_usd   mean_correctness")
    for route in ("naive", "strong", "graph"):
        r = agg[route]
        print(f"  {route:8} {r['tokens']:10} {r['cost_usd']:10} {r['mean_correctness']}")
    gv = summary["graph_vs_strong"]
    print(f"\ngraph beat the strong baseline on tokens (correctness held) in "
          f"{gv['won_count']} of {gv['task_count']} tasks")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Measure per-language symbol extraction accuracy on the expanded corpus.

For every language in the corpus this reports symbol precision and recall of the
extracted (kind, name) multiset against the hand-labelled ground truth, plus the
counts behind the ratios and the names of everything missed or invented, so a
score below 1.0 stays actionable rather than being an opaque number.

Three outcomes are distinguished and never conflated:

- measured: the parser ran and the numbers below are real.
- not_measured_in_this_environment: the grammar this language needs is not
  installed here. That is not a score of zero, and it is not a pass either.
- unsupported: no permissive grammar and no fallback exists. Reported with the
  reason, never scored.

Fidelity is reported alongside every score. A language parsed by a real grammar
and a language read by the documented pattern fallback are both measured, but
they are never presented as the same kind of result.

Writes test-evidence/language_accuracy.json. Deterministic and seeded; no
network access. No forced green.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "code" / "corpus"
GROUND_TRUTH = CORPUS / "language_ground_truth.json"
OUT = ROOT / "test-evidence" / "language_accuracy.json"

sys.path.insert(0, str(ROOT / "src"))

# Languages this project has decided it cannot support, with the reason. Listed
# here rather than silently omitted, so the gap is visible in the published
# evidence instead of only in a commit message.
UNSUPPORTED: dict[str, str] = {}


def _grammar_ready(language: str) -> tuple[bool, str]:
    """Whether the grammar this corpus language needs is importable here."""
    from dkg.code.capability import grammar_available
    from dkg.code.fallback import FALLBACK_SPECS

    if language in FALLBACK_SPECS or language == "xs":
        # A fallback needs no grammar at all: it is pure pattern matching, so it
        # is measurable on every machine. Perl XS is named separately because,
        # unlike the five in FALLBACK_SPECS, no grammar for it exists anywhere
        # to install, so it can never move off this path.
        return True, ""
    # The composite formats are parsed by another language's grammar.
    needed = {
        "jupyter": "python",
        "databricks": "python",
        "vue": "typescript",
        "svelte": "typescript",
        "astro": "typescript",
        "ansible": "yaml",
    }.get(language, language)
    if grammar_available(needed):
        return True, ""
    return False, f"grammar for {needed!r} is not installed in this environment"


def measure() -> dict:
    from dkg.code.parser import parse_source

    truth = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
    per_language: dict = {}
    for language, spec in sorted(truth.items()):
        if language.startswith("_"):
            continue
        if language in UNSUPPORTED:
            per_language[language] = {"status": "unsupported", "reason": UNSUPPORTED[language]}
            continue
        ready, reason = _grammar_ready(language)
        if not ready:
            per_language[language] = {
                "status": "not_measured_in_this_environment",
                "reason": reason,
                "fidelity": spec.get("fidelity", "grammar"),
                "grammar": spec.get("grammar", ""),
                "licence": spec.get("licence", ""),
            }
            continue
        got_all: Counter = Counter()
        exp_all: Counter = Counter()
        per_file: dict = {}
        error: str | None = None
        for rel, expected in sorted(spec["files"].items()):
            path = CORPUS / "langs" / rel
            try:
                parsed = parse_source(path)
            except Exception as e:  # noqa: BLE001 - a parse failure is a real result
                error = f"{rel}: {type(e).__name__}: {e}"
                break
            got = Counter((s.kind, s.name) for s in parsed.symbols if s.kind != "module")
            exp = Counter(tuple(item) for item in expected)
            got_all += got
            exp_all += exp
            per_file[rel] = {
                "expected": sum(exp.values()),
                "extracted": sum(got.values()),
                "correct": sum((got & exp).values()),
            }
        if error is not None:
            per_language[language] = {
                "status": "parse_error",
                "reason": error,
                "fidelity": spec.get("fidelity", "grammar"),
            }
            continue
        correct = sum((got_all & exp_all).values())
        extracted = sum(got_all.values())
        expected_total = sum(exp_all.values())
        entry = {
            "status": "measured",
            "fidelity": spec.get("fidelity", "grammar"),
            "grammar": spec.get("grammar", ""),
            "licence": spec.get("licence", ""),
            "files": len(spec["files"]),
            "expected": expected_total,
            "extracted": extracted,
            "correct": correct,
            "precision": round(correct / extracted, 4) if extracted else 0.0,
            "recall": round(correct / expected_total, 4) if expected_total else 0.0,
            "per_file": per_file,
        }
        missed = sorted(f"{k}:{n}" for (k, n), c in (exp_all - got_all).items() for _ in range(c))
        spurious = sorted(f"{k}:{n}" for (k, n), c in (got_all - exp_all).items() for _ in range(c))
        if missed:
            entry["missed"] = missed
        if spurious:
            entry["spurious"] = spurious
        per_language[language] = entry
    return per_language


HARD_GROUND_TRUTH = CORPUS / "hard_ground_truth.json"


def measure_hard() -> dict:
    """Score the held-out corpus of constructs the parser was not tuned against.

    Reported separately and never merged into the main figures: the main corpus
    was authored alongside the parser, this one was labelled before it was ever
    run, and the two are different strengths of evidence.
    """
    from dkg.code.parser import parse_source

    truth = json.loads(HARD_GROUND_TRUTH.read_text(encoding="utf-8"))
    per_language: dict = {}
    for language, spec in sorted(truth.items()):
        if language.startswith("_"):
            continue
        ready, reason = _grammar_ready(language)
        if not ready:
            per_language[language] = {"status": "not_measured_in_this_environment", "reason": reason}
            continue
        got_all: Counter = Counter()
        exp_all: Counter = Counter()
        for rel, expected in sorted(spec["files"].items()):
            parsed = parse_source(CORPUS / "hard" / rel)
            got_all += Counter((s.kind, s.name) for s in parsed.symbols if s.kind != "module")
            exp_all += Counter(tuple(item) for item in expected)
        got_names = Counter(name for (_kind, name), count in got_all.items() for _ in range(count))
        exp_names = Counter(name for (_kind, name), count in exp_all.items() for _ in range(count))
        correct = sum((got_all & exp_all).values())
        name_correct = sum((got_names & exp_names).values())
        extracted, expected_total = sum(got_all.values()), sum(exp_all.values())
        entry = {
            "status": "measured",
            "expected": expected_total,
            "extracted": extracted,
            "correct": correct,
            "precision": round(correct / extracted, 4) if extracted else 0.0,
            "recall": round(correct / expected_total, 4) if expected_total else 0.0,
            "name_precision": round(name_correct / extracted, 4) if extracted else 0.0,
            "name_recall": round(name_correct / expected_total, 4) if expected_total else 0.0,
        }
        missed = sorted(f"{k}:{n}" for (k, n), c in (exp_all - got_all).items() for _ in range(c))
        spurious = sorted(f"{k}:{n}" for (k, n), c in (got_all - exp_all).items() for _ in range(c))
        if missed:
            entry["missed"] = missed
        if spurious:
            entry["spurious"] = spurious
        per_language[language] = entry
    return per_language


def summarise_hard(per_language: dict) -> dict:
    measured = {k: v for k, v in per_language.items() if v.get("status") == "measured"}
    if not measured:
        return {"languages_measured": 0}
    extracted = sum(v["extracted"] for v in measured.values())
    expected = sum(v["expected"] for v in measured.values())
    correct = sum(v["correct"] for v in measured.values())
    return {
        "languages_measured": len(measured),
        "labelled_symbols": expected,
        "micro_precision": round(correct / extracted, 4) if extracted else 0.0,
        "micro_recall": round(correct / expected, 4) if expected else 0.0,
        "macro_precision": round(sum(v["precision"] for v in measured.values()) / len(measured), 4),
        "macro_recall": round(sum(v["recall"] for v in measured.values()) / len(measured), 4),
        "macro_name_precision": round(sum(v["name_precision"] for v in measured.values()) / len(measured), 4),
        "macro_name_recall": round(sum(v["name_recall"] for v in measured.values()) / len(measured), 4),
    }


def summarise(per_language: dict) -> dict:
    measured = {k: v for k, v in per_language.items() if v.get("status") == "measured"}
    by_fidelity: dict[str, list[str]] = {}
    for name, entry in measured.items():
        by_fidelity.setdefault(entry["fidelity"], []).append(name)
    total_expected = sum(v["expected"] for v in measured.values())
    total_extracted = sum(v["extracted"] for v in measured.values())
    total_correct = sum(v["correct"] for v in measured.values())
    return {
        "languages_total": len([k for k in per_language if not k.startswith("_")]),
        "languages_measured": len(measured),
        "languages_not_measured_here": len(
            [v for v in per_language.values() if v.get("status") == "not_measured_in_this_environment"]
        ),
        "languages_unsupported": len([v for v in per_language.values() if v.get("status") == "unsupported"]),
        "languages_parse_error": len([v for v in per_language.values() if v.get("status") == "parse_error"]),
        "by_fidelity": {k: sorted(v) for k, v in sorted(by_fidelity.items())},
        "corpus_files": sum(v.get("files", 0) for v in measured.values()),
        "corpus_labelled_symbols": total_expected,
        "micro_precision": round(total_correct / total_extracted, 4) if total_extracted else 0.0,
        "micro_recall": round(total_correct / total_expected, 4) if total_expected else 0.0,
        "macro_precision": round(
            sum(v["precision"] for v in measured.values()) / len(measured), 4
        ) if measured else 0.0,
        "macro_recall": round(
            sum(v["recall"] for v in measured.values()) / len(measured), 4
        ) if measured else 0.0,
    }


def main() -> int:
    os.environ.setdefault("PYTHONHASHSEED", "0")
    per_language = measure()
    hard = measure_hard()
    payload = {
        "benchmark": "per-language source parsing accuracy",
        "seed": "PYTHONHASHSEED=0",
        "metric": "symbol (kind, name) multiset precision and recall against hand-labelled ground truth",
        "corpus": "tests/code/corpus/langs",
        "ground_truth": "tests/code/corpus/language_ground_truth.json",
        "note": (
            "Module symbols are excluded from scoring. A language whose grammar is absent here is "
            "reported not measured, never scored zero. Fallback-level languages are measured but "
            "are never presented as fully parsed."
        ),
        "corpus_provenance": (
            "The per-language corpus was written by the same author as the parser and iterated "
            "against it, so a perfect score on it is weaker evidence than it looks. The held-out "
            "corpus below was written and labelled before it was ever parsed and is the stronger "
            "of the two numbers."
        ),
        "summary": summarise(per_language),
        "languages": per_language,
        "held_out": {
            "corpus": "tests/code/corpus/hard",
            "ground_truth": "tests/code/corpus/hard_ground_truth.json",
            "note": (
                "Constructs a definition-shaped parser finds hard: nested and anonymous "
                "definitions, generics, extensions, metaclasses, records, enums with bodies, "
                "function-valued bindings, and singleton classes. Labelled before measurement."
            ),
            "summary": summarise_hard(hard),
            "languages": hard,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary = payload["summary"]
    print(f"wrote {OUT.relative_to(ROOT)}")
    print(
        f"measured {summary['languages_measured']}/{summary['languages_total']} languages, "
        f"{summary['corpus_files']} files, {summary['corpus_labelled_symbols']} labelled symbols"
    )
    print(
        f"micro precision {summary['micro_precision']} recall {summary['micro_recall']}; "
        f"macro precision {summary['macro_precision']} recall {summary['macro_recall']}"
    )
    for name, entry in sorted(per_language.items()):
        if entry.get("status") != "measured":
            print(f"  {name:12s} {entry.get('status')}: {entry.get('reason', '')}")
            continue
        flag = "" if entry["precision"] == 1.0 and entry["recall"] == 1.0 else "  <-- imperfect"
        print(
            f"  {name:12s} {entry['fidelity']:8s} P={entry['precision']:.4f} R={entry['recall']:.4f} "
            f"({entry['correct']}/{entry['expected']} labelled, {entry['extracted']} extracted){flag}"
        )
    hard_summary = payload["held_out"]["summary"]
    print("\nheld-out hard-construct corpus (labelled before measurement):")
    print(
        f"  micro precision {hard_summary.get('micro_precision')} recall {hard_summary.get('micro_recall')}; "
        f"name-only macro precision {hard_summary.get('macro_name_precision')} "
        f"recall {hard_summary.get('macro_name_recall')}"
    )
    for name, entry in sorted(hard.items()):
        if entry.get("status") != "measured":
            print(f"  {name:12s} {entry.get('status')}")
            continue
        print(
            f"  {name:12s} P={entry['precision']:.4f} R={entry['recall']:.4f} "
            f"(names P={entry['name_precision']:.4f} R={entry['name_recall']:.4f})"
        )
        if entry.get("missed"):
            print(f"      missed: {', '.join(entry['missed'])}")
        if entry.get("spurious"):
            print(f"      spurious: {', '.join(entry['spurious'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

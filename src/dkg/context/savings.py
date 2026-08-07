"""An estimated context-savings record attached to an analysis result.

The claim a code graph makes is that answering a structural question by reading
the graph costs less than answering it by reading the code. That claim is
measurable per answer, and until now it was only measured in a benchmark. This
attaches the measurement to the answer itself.

Four honesty rules, each of which the code enforces rather than merely states.

ESTIMATED MEANS ESTIMATED. The default figures come from the documented
character-based estimator, not from a tokenizer, and every record says so in a
field a reader cannot miss. A real-tokenizer cross-check is available, opt-in
because loading a tokenizer for every answer would be absurd, and when it runs
the CALIBRATION ERROR between the two is published rather than the estimate
being quietly replaced.

THE BASELINE IS THE HONEST ONE. It is the cost of reading the source files this
answer names, because a structural answer does not remove the need to read the
code it points at; it removes the need to read everything else. A baseline of
"the whole repository" would produce a far better number and would be a lie
about what the alternative is.

THE BREAKDOWN SUMS EXACTLY. The per-category figures add up to the reported graph
cost with no remainder, because the serialisation's own structural overhead is a
category rather than a rounding difference someone has to explain.

A FILE THAT COULD NOT BE READ IS NOT COUNTED AS FREE. It is excluded from the
baseline and reported, so a saving is never inflated by a file that was missing.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from .tokens import count_tokens, estimate_tokens, tokenizer_available, tokenizer_name

# Bounds on the baseline read. Measuring a saving must not become a way to read
# an entire repository into memory.
MAX_BASELINE_FILES = 200
MAX_BASELINE_FILE_BYTES = 1_000_000

# The key a record is attached under.
RECORD_KEY = "context_savings"

# Result keys that are structural noise rather than an answer, excluded from the
# per-category breakdown's own naming but still counted in the total under the
# structure category.
_ENVELOPE_KEYS = ("why", "verbosity", "verbosity_note")


def paths_in(payload: object, *, limit: int = MAX_BASELINE_FILES) -> list[str]:
    """Repository-relative source paths an analysis result names.

    Two sources: an explicit ``path`` field, and the file part of a canonical
    name (``path/to/file.py::Symbol``). Deduplicated and sorted, so the baseline
    is deterministic for a given answer.
    """
    found: set[str] = set()

    def walk(node: object) -> None:
        if len(found) >= limit:
            return
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("path", "file") and isinstance(value, str) and value.strip():
                    found.add(value.strip())
                elif key in ("canonical", "name", "from", "to", "entry") and isinstance(value, str):
                    head = value.split("::", 1)[0].strip()
                    if head and ("/" in head or "." in head) and " " not in head:
                        found.add(head)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return sorted(found)[:limit]


def _baseline(paths: Iterable[str], root: Path) -> tuple[int, list[str], list[dict]]:
    """Estimated tokens of reading the named files, plus what was excluded."""
    total = 0
    read: list[str] = []
    excluded: list[dict] = []
    for rel in paths:
        candidate = (root / rel).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            excluded.append({"path": rel, "reason": "escapes the root"})
            continue
        if not candidate.is_file():
            excluded.append({"path": rel, "reason": "not a readable file here"})
            continue
        # Size first, then read. Checking the cap after read_bytes() would pull
        # the whole file into memory before rejecting it, which is most of what
        # the cap is for.
        try:
            if candidate.stat().st_size > MAX_BASELINE_FILE_BYTES:
                excluded.append({"path": rel, "reason": "larger than the per-file cap"})
                continue
            raw = candidate.read_bytes()
        except OSError as e:
            excluded.append({"path": rel, "reason": f"unreadable: {e}"})
            continue
        total += estimate_tokens(raw.decode("utf-8", errors="replace"))
        read.append(rel)
    return total, read, excluded


def _breakdown(payload: dict) -> tuple[list[dict], int]:
    """Per-top-level-key token estimates that sum EXACTLY to the whole.

    Each category is measured on its own serialisation; the difference between
    their sum and the serialisation of the whole payload is the envelope (the
    braces, the commas, the key names) and is reported as its own category
    rather than dropped, which is what makes the breakdown add up.
    """
    whole = estimate_tokens(_dumps(payload))
    categories: list[dict] = []
    parts = 0
    for key in sorted(payload):
        if key == RECORD_KEY:
            continue
        tokens = estimate_tokens(_dumps(payload[key]))
        parts += tokens
        categories.append({"category": key, "tokens": tokens})
    structure = whole - parts
    categories.append(
        {
            "category": "serialisation",
            "tokens": structure,
            "note": "the envelope: braces, commas, and key names, so the breakdown sums exactly",
        }
    )
    return categories, whole


def _dumps(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def savings_record(
    payload: dict,
    *,
    repo_root: str | Path,
    paths: Iterable[str] | None = None,
    verify: bool = False,
) -> dict:
    """The savings record for one analysis result. Reads only.

    ``verify`` runs the real tokenizer over both sides and publishes the
    calibration error against the estimate. It never replaces the estimate: a
    figure labelled estimated must stay the figure that was labelled.
    """
    root = Path(repo_root).resolve()
    named = list(paths) if paths is not None else paths_in(payload)
    baseline, read, excluded = _baseline(named, root)
    categories, graph_tokens = _breakdown(payload)
    saved = baseline - graph_tokens
    percent = round(100.0 * saved / baseline, 2) if baseline > 0 else 0.0

    record = {
        "estimated": True,
        "estimator": "in-repo character-run estimator, not a tokenizer",
        "baseline_tokens": baseline,
        "graph_tokens": graph_tokens,
        "saved_tokens": saved,
        "saved_percent": percent,
        "breakdown": categories,
        "baseline_files": read,
        "baseline_files_excluded": excluded,
        "why": {
            "baseline": (
                "the cost of reading the source files THIS answer names. A "
                "structural answer does not remove the need to read the code it "
                "points at; it removes the need to read everything else. A "
                "whole-repository baseline would produce a much better number "
                "and would misdescribe the alternative."
            ),
            "breakdown": (
                "the per-category figures sum exactly to graph_tokens; the "
                "serialisation envelope is a category rather than a residue"
            ),
            "excluded_files": (
                "a file that could not be read is excluded from the baseline and "
                "listed, so a saving is never inflated by a file that was missing"
            ),
            "negative_saving": (
                "a negative saved_tokens is reported as it is. It means this "
                "answer was more expensive than reading the files it names, "
                "which happens for a small answer over a small file."
            ),
            "labelled": (
                "ESTIMATED. These are estimator counts, not tokenizer counts. Run "
                "with the cross-check to see how far apart they are."
            ),
        },
        "cross_check": None,
    }
    if verify:
        record["cross_check"] = _cross_check(payload, read, root, baseline, graph_tokens)
    return record


def _cross_check(payload, read, root: Path, est_baseline: int, est_graph: int) -> dict:
    """Recount both sides with the real tokenizer and publish the error."""
    if not tokenizer_available():
        return {
            "ran": False,
            "reason": (
                "no real tokenizer is available here, so there is nothing to "
                "calibrate against. The estimate stands and stays labelled."
            ),
            "tokenizer": tokenizer_name(),
        }
    real_baseline = 0
    for rel in read:
        try:
            real_baseline += count_tokens(
                (root / rel).read_bytes().decode("utf-8", errors="replace")
            )
        except OSError:
            continue
    real_graph = count_tokens(_dumps(payload))
    return {
        "ran": True,
        "tokenizer": tokenizer_name(),
        "baseline_tokens": real_baseline,
        "graph_tokens": real_graph,
        "saved_tokens": real_baseline - real_graph,
        "saved_percent": round(100.0 * (real_baseline - real_graph) / real_baseline, 2)
        if real_baseline > 0
        else 0.0,
        "calibration_error": {
            "baseline_percent": _error(est_baseline, real_baseline),
            "graph_percent": _error(est_graph, real_graph),
            "note": (
                "how far the estimator is from the tokenizer on THIS answer, as "
                "a signed percentage of the tokenizer count. Published rather "
                "than used to silently correct the estimate."
            ),
        },
    }


def _error(estimated: int, real: int) -> float:
    if real == 0:
        return 0.0
    return round(100.0 * (estimated - real) / real, 2)


def attach_savings(
    payload: dict,
    *,
    repo_root: str | Path,
    verify: bool = False,
    enabled: bool = True,
) -> dict:
    """Attach a savings record to a result, in place, and return the result."""
    if not enabled or not isinstance(payload, dict):
        return payload
    record: dict = savings_record(payload, repo_root=repo_root, verify=verify)
    payload[RECORD_KEY] = record
    return payload

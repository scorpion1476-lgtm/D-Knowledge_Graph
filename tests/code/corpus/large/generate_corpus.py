#!/usr/bin/env python3
"""Generate the large mixed corpus for the token-cost benchmark.

The small token corpus (38 files) was too small for the ratio to mean anything:
the graph route only starts paying once a repository is bigger than a context
window. This builds a corpus of a few thousand graph nodes, with every fact the
benchmark scores against known by construction rather than labelled by hand or
judged by a model.

Shape:

- ``code/`` holds ``LAYERS`` layer modules over a shared ``core``, and
  ``MODULES`` leaf modules over the layers. Leaf module i uses layer i % LAYERS,
  so the true impact set of any layer symbol is exactly the leaves on that
  layer, which is arithmetic rather than a guess.
- ``docs/`` holds one note per layer plus a set of deliberately contradictory
  pairs. A contradiction is planted by stating a specific numeric threshold in
  one note and a different value for the same subject in another, so the pair
  is machine-checkable.
- ``ground_truth.json`` records the impact sets, the review-required symbols,
  the question-answering targets, and the contradiction pairs.

Determinism: no randomness anywhere, so regenerating produces byte-identical
files and the published numbers stay reproducible.

Usage:
    python tests/code/corpus/large/generate_corpus.py
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
CODE = HERE / "code"
DOCS = HERE / "docs"

MODULES = 400
LAYERS = 8
CORE_UTILS = 10
FUNCS_PER_MODULE = 6
CONTRADICTION_PAIRS = 6


def _core() -> str:
    lines = ['"""Shared utilities every layer depends on."""', ""]
    for i in range(CORE_UTILS):
        lines += [f"def core_util_{i}(value):", f"    return value + {i}", ""]
    lines += ["def core_entry(value):", "    total = 0"]
    for i in range(CORE_UTILS):
        lines.append(f"    total += core_util_{i}(value)")
    lines += ["    return total", ""]
    lines += ["def core_unused_helper(value):", "    return value", ""]
    return "\n".join(lines)


def _layer(index: int) -> str:
    lines = [
        f'"""Layer {index}, between the leaf modules and core."""',
        "",
        "from core import core_entry, core_util_0",
        "",
    ]
    for i in range(3):
        lines += [f"def layer_{index}_step_{i}(value):", f"    return core_util_0(value) + {i}", ""]
    lines += [f"def layer_{index}_gateway(value):", "    total = core_entry(value)"]
    for i in range(3):
        lines.append(f"    total += layer_{index}_step_{i}(value)")
    lines += ["    return total", ""]
    lines += [
        f"class Layer{index}Service:",
        "    def handle(self, value):",
        f"        return layer_{index}_gateway(value)",
        "",
        "    def describe(self):",
        f'        return "layer {index}"',
        "",
    ]
    return "\n".join(lines)


def _module(index: int) -> str:
    layer = index % LAYERS
    lines = [
        f'"""Leaf module {index:03d}."""',
        "",
        f"from layer_{layer} import layer_{layer}_gateway",
        "",
    ]
    for i in range(FUNCS_PER_MODULE - 1):
        lines += [
            f"def mod_{index:03d}_op_{i}(value):",
            f"    return layer_{layer}_gateway(value) + {i}",
            "",
        ]
    lines += [f"def mod_{index:03d}_run(value):", "    total = 0"]
    for i in range(FUNCS_PER_MODULE - 1):
        lines.append(f"    total += mod_{index:03d}_op_{i}(value)")
    lines += ["    return total", ""]
    if index % 10 == 0:
        lines += [f"def mod_{index:03d}_orphan(value):", "    return value", ""]
    return "\n".join(lines)


def _test_module(index: int) -> str:
    return "\n".join(
        [
            f'"""Tests for leaf module {index:03d}."""',
            "",
            f"from mod_{index:03d} import mod_{index:03d}_run",
            "",
            f"def test_mod_{index:03d}_run():",
            f"    assert mod_{index:03d}_run(1) is not None",
            "",
        ]
    )


def _layer_note(index: int) -> str:
    return "\n".join(
        [
            f"# Layer {index} operations note",
            "",
            f"Layer {index} routes leaf traffic through `layer_{index}_gateway`, which",
            "calls `core_entry` and the layer steps.",
            "",
            f"The retry budget for layer {index} is {3 + index} attempts before the",
            "request is abandoned.",
            "",
            f"Ownership of layer {index} sits with the platform group.",
            "",
        ]
    )


def _contradiction(pair: int) -> tuple[str, str, dict]:
    """One machine-checkable contradictory pair on a shared subject."""
    subject = f"cache-ttl-service-{pair}"
    a_value, b_value = 30 + pair, 300 + pair
    a = "\n".join(
        [
            f"# Runbook for service {pair}",
            "",
            f"The cache TTL for service {pair} is {a_value} seconds. Operators should",
            "not change this without review.",
            "",
        ]
    )
    b = "\n".join(
        [
            f"# Architecture note for service {pair}",
            "",
            f"Service {pair} uses a cache TTL of {b_value} seconds, chosen to reduce",
            "load on the upstream store.",
            "",
        ]
    )
    truth = {
        "subject": subject,
        "doc_a": f"runbook_{pair}.md",
        "doc_b": f"architecture_{pair}.md",
        "value_a": a_value,
        "value_b": b_value,
        "kind": "numeric_disagreement",
    }
    return a, b, truth


def generate() -> dict:
    for directory in (CODE, DOCS):
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True, exist_ok=True)

    (CODE / "core.py").write_text(_core(), encoding="utf-8")
    for layer in range(LAYERS):
        (CODE / f"layer_{layer}.py").write_text(_layer(layer), encoding="utf-8")
    tested = [0, 1, 2, 3, 4]
    for index in range(MODULES):
        (CODE / f"mod_{index:03d}.py").write_text(_module(index), encoding="utf-8")
    for index in tested:
        (CODE / f"test_mod_{index:03d}.py").write_text(_test_module(index), encoding="utf-8")

    for layer in range(LAYERS):
        (DOCS / f"layer_{layer}_note.md").write_text(_layer_note(layer), encoding="utf-8")
    contradictions = []
    for pair in range(CONTRADICTION_PAIRS):
        a, b, truth = _contradiction(pair)
        (DOCS / truth["doc_a"]).write_text(a, encoding="utf-8")
        (DOCS / truth["doc_b"]).write_text(b, encoding="utf-8")
        contradictions.append(truth)

    # Impact truth by construction: changing a layer gateway reaches exactly the
    # leaves on that layer, through their ops and run functions.
    impact_truth = {}
    for layer in range(LAYERS):
        leaves = [i for i in range(MODULES) if i % LAYERS == layer]
        reached = []
        for i in leaves:
            reached += [f"mod_{i:03d}.py::mod_{i:03d}_op_{k}" for k in range(FUNCS_PER_MODULE - 1)]
            reached.append(f"mod_{i:03d}.py::mod_{i:03d}_run")
        impact_truth[f"layer_{layer}.py::layer_{layer}_gateway"] = sorted(reached)

    code_files = sorted(p.name for p in CODE.glob("*.py"))
    doc_files = sorted(p.name for p in DOCS.glob("*.md"))
    truth = {
        "generator": "tests/code/corpus/large/generate_corpus.py",
        "deterministic": True,
        "modules": MODULES,
        "layers": LAYERS,
        "code_files": len(code_files),
        "doc_files": len(doc_files),
        "code_bytes": sum((CODE / n).stat().st_size for n in code_files),
        "doc_bytes": sum((DOCS / n).stat().st_size for n in doc_files),
        "tested_modules": tested,
        "impact": impact_truth,
        "contradictions": contradictions,
        "unreferenced": ["core_unused_helper"] + [f"mod_{i:03d}_orphan" for i in range(0, MODULES, 10)],
        "hub_symbols": ["core.py::core_util_0", "core.py::core_entry"],
        "qa": [
            {
                "question": f"What is the retry budget for layer {i}?",
                "answer_contains": str(3 + i),
                "required_docs": [f"layer_{i}_note.md"],
            }
            for i in range(LAYERS)
        ],
    }
    (HERE / "ground_truth.json").write_text(json.dumps(truth, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return truth


if __name__ == "__main__":
    info = generate()
    print(
        f"wrote {info['code_files']} code files ({info['code_bytes']} bytes) and "
        f"{info['doc_files']} docs ({info['doc_bytes']} bytes) into {HERE}"
    )

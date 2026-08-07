#!/usr/bin/env python3
"""Generate the retained corpus for the token-efficiency benchmark.

The benchmark compares the cost of handing a whole repository to a model against
the cost of asking the graph a targeted question. That comparison is only
meaningful on a corpus whose size and shape are stated, so this generator builds
one deterministically and the result is committed. Regenerating it must produce
byte-identical files.

Shape, chosen so the questions have real answers rather than trivial ones:

- ``core.py`` holds shared utilities that many modules call. These become the
  hubs, so a blast-radius question has a large true answer.
- ``layer_<n>.py`` (one per layer) sits between core and the leaf modules, so
  call chains have depth and some symbols become genuine cut vertices.
- ``mod_<nn>.py`` are the leaves. Each calls its own layer and, for some, core
  directly.
- A handful of functions are deliberately never called, so the knowledge-gap
  question has a non-empty answer.
- Two modules have tests, so the untested-hotspot question is not vacuous.

Every relationship is known by construction and is written to
``structure.json`` next to the sources.

Usage:
    python tests/code/corpus/tokens/generate_corpus.py
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

MODULES = 30
FUNCS_PER_MODULE = 8
LAYERS = 5
CORE_UTILS = 6


def _core() -> str:
    lines = ['"""Shared utilities called from across the corpus."""', ""]
    for i in range(CORE_UTILS):
        lines += [
            f"def core_util_{i}(value):",
            f"    return value + {i}",
            "",
        ]
    lines += [
        "def core_entry(value):",
        "    total = 0",
    ]
    for i in range(CORE_UTILS):
        lines.append(f"    total += core_util_{i}(value)")
    lines += ["    return total", ""]
    # Never referenced anywhere: the knowledge-gap question needs a true answer.
    lines += [
        "def core_unused_helper(value):",
        "    return value",
        "",
    ]
    return "\n".join(lines)


def _layer(index: int) -> str:
    lines = [
        f'"""Layer {index}: sits between the leaf modules and core."""',
        "",
        "from core import core_entry, core_util_0",
        "",
    ]
    for i in range(4):
        lines += [
            f"def layer_{index}_step_{i}(value):",
            f"    return core_util_0(value) + {i}",
            "",
        ]
    lines += [
        f"def layer_{index}_gateway(value):",
        "    total = core_entry(value)",
    ]
    for i in range(4):
        lines.append(f"    total += layer_{index}_step_{i}(value)")
    lines += ["    return total", ""]
    return "\n".join(lines)


def _module(index: int) -> str:
    layer = index % LAYERS
    lines = [
        f'"""Leaf module {index:02d}."""',
        "",
        f"from layer_{layer} import layer_{layer}_gateway",
        "",
    ]
    for i in range(FUNCS_PER_MODULE - 2):
        lines += [
            f"def mod_{index:02d}_op_{i}(value):",
            f"    return layer_{layer}_gateway(value) + {i}",
            "",
        ]
    lines += [
        f"def mod_{index:02d}_run(value):",
        "    total = 0",
    ]
    for i in range(FUNCS_PER_MODULE - 2):
        lines.append(f"    total += mod_{index:02d}_op_{i}(value)")
    lines += ["    return total", ""]
    # One unreferenced function every fifth module.
    if index % 5 == 0:
        lines += [
            f"def mod_{index:02d}_orphan(value):",
            "    return value",
            "",
        ]
    return "\n".join(lines)


def _test_module(index: int) -> str:
    return "\n".join(
        [
            f'"""Tests for leaf module {index:02d}."""',
            "",
            f"from mod_{index:02d} import mod_{index:02d}_run",
            "",
            f"def test_mod_{index:02d}_run():",
            f"    assert mod_{index:02d}_run(1) is not None",
            "",
        ]
    )


def generate() -> dict:
    HERE.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    def write(name: str, text: str) -> None:
        (HERE / name).write_text(text, encoding="utf-8")
        written.append(name)

    write("core.py", _core())
    for layer in range(LAYERS):
        write(f"layer_{layer}.py", _layer(layer))
    for index in range(MODULES):
        write(f"mod_{index:02d}.py", _module(index))
    tested = [0, 1]
    for index in tested:
        write(f"test_mod_{index:02d}.py", _test_module(index))

    source_files = sorted(n for n in written if n.endswith(".py"))
    total_bytes = sum((HERE / n).stat().st_size for n in source_files)
    structure = {
        "generator": "tests/code/corpus/tokens/generate_corpus.py",
        "deterministic": True,
        "modules": MODULES,
        "layers": LAYERS,
        "functions_per_module": FUNCS_PER_MODULE,
        "core_utilities": CORE_UTILS,
        "tested_modules": tested,
        "files": len(source_files),
        "bytes": total_bytes,
        "known_by_construction": {
            "core_util_0": "called by every layer gateway and every layer step",
            "core_entry": "called by every layer gateway",
            "unreferenced": ["core_unused_helper"] + [f"mod_{i:02d}_orphan" for i in range(0, MODULES, 5)],
            "hub_symbols": ["core_util_0", "core_entry"],
        },
    }
    (HERE / "structure.json").write_text(json.dumps(structure, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return structure


if __name__ == "__main__":
    info = generate()
    print(f"wrote {info['files']} source files, {info['bytes']} bytes, into {HERE}")

#!/usr/bin/env python3
"""Generate docs/LANGUAGES.md from the live language registry.

Hand-typing a 41-row language table is how a table goes stale: a grammar moves
between extras, an extension is added, and the document keeps claiming the old
shape. This reads the registry the parser itself uses, so the document cannot
disagree with the code.

Availability is deliberately NOT written into the file. Whether a grammar is
installed is a property of the machine that ran this script, not of the
project, and baking it in would publish one developer's environment as though
it were the product. Run `dkg code-languages` for the live per-machine answer.

Run: python scripts/language_inventory.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

OUT = ROOT / "docs" / "LANGUAGES.md"

EXTRA_ORDER = ["code", "code-extended", "code-full", "code-bundle"]
EXTRA_NOTE = {
    None: (
        "Perl XS. No permissive Tree-sitter grammar for `.xs` exists in any source "
        "available to this project, including the multi-grammar bundle, so there is no "
        "extra to install and no upgrade to offer. It is read by the documented pattern "
        "extractor in `src/dkg/code/xs.py` at `fallback` fidelity, is never presented as "
        "a parse, and every edge leaving such a file is confidence-scaled."
    ),
    "code": "The starter set. Installed by `pip install -e \".[code]\"`.",
    "code-extended": "Common application languages and single-file components.",
    "code-full": "The remaining grammars, including shells, systems languages, and infrastructure formats.",
    "code-bundle": (
        "Languages with no installable dedicated permissive grammar package. They are "
        "read through a bundle whose every grammar has been licence-audited into "
        "`docs/grammar_bundle_licences.json`. Without this extra they degrade to the "
        "documented pattern extractor at `fallback` fidelity, which is labelled as such "
        "everywhere it surfaces and never presented as a real parse."
    ),
}

FIDELITY_NOTE = {
    "grammar": "a real Tree-sitter parse of the whole file",
    "composite": (
        "the file is unwrapped first (a notebook's code cells, a component's script "
        "block, an infrastructure file's block structure) and its code is then parsed "
        "with another language's grammar"
    ),
    "fallback": (
        "the documented line-oriented pattern extractor, used where no dedicated "
        "permissive grammar package is installable. Honestly lower fidelity: every "
        "edge leaving such a file is confidence-scaled and the language is never "
        "reported as though it had been parsed"
    ),
    "grammar or fallback": (
        "`grammar` when the optional `code-bundle` extra is installed, and the "
        "documented pattern extractor at `fallback` fidelity when it is not. Which "
        "one is in force is a property of the machine, so both are stated here"
    ),
}


def main() -> int:
    from dkg.code.parser import language_inventory

    languages = language_inventory()
    if not languages:  # pragma: no cover - registry shape guard
        sys.exit("the language inventory is empty; update this script")

    # These five report `grammar` when the optional bundle is installed and
    # `fallback` when it is not. Whichever is in force on the machine that ran
    # this script is an accident of that machine, so the document states both
    # rather than publishing one developer's environment as a product fact.
    from dkg.code.fallback import FALLBACK_SPECS

    dual = set(FALLBACK_SPECS)

    by_extra: dict[str, list] = {}
    fidelities: dict[str, int] = {}
    for name, info in sorted(languages.items()):
        by_extra.setdefault(info["extra"], []).append((name, info))
        key = "grammar or fallback" if name in dual else info["fidelity"]
        fidelities[key] = fidelities.get(key, 0) + 1

    lines: list[str] = []
    lines.append("# Language inventory")
    lines.append("")
    lines.append(
        "Generated from the live language registry by "
        "`python scripts/language_inventory.py`. Do not hand-edit."
    )
    lines.append("")
    lines.append(
        f"The source-code plane covers **{len(languages)} languages and containers**, "
        "in opt-in extras so a minimal install stays minimal."
    )
    lines.append("")
    lines.append("| Fidelity | Languages | What it means |")
    lines.append("| --- | ---: | --- |")
    for fidelity in ("grammar", "composite", "fallback", "grammar or fallback"):
        if fidelity in fidelities:
            lines.append(
                f"| `{fidelity}` | {fidelities[fidelity]} | {FIDELITY_NOTE[fidelity]} |"
            )
    lines.append("")
    lines.append(
        "Whether a given grammar is installed on *your* machine is a property of your "
        "environment, not of this project, so it is not recorded here. Run "
        "`dkg code-languages` for the live answer, which reports the fidelity actually "
        "in force rather than the best case."
    )
    lines.append("")

    ordered = [e for e in EXTRA_ORDER if e in by_extra]
    # Perl XS has no extra at all: no permissive grammar for it exists anywhere
    # to install. It sorts last under its own heading rather than being given a
    # fictitious extra name.
    ordered += [e for e in sorted(by_extra, key=lambda x: (x is None, x or "")) if e not in EXTRA_ORDER]

    for extra in ordered:
        rows = by_extra[extra]
        heading = f"`{extra}`" if extra else "No extra (always the pattern extractor)"
        lines.append(f"## {heading} ({len(rows)})")
        lines.append("")
        if extra in EXTRA_NOTE:
            lines.append(EXTRA_NOTE[extra])
            lines.append("")
        lines.append("| Language | Extensions | Fidelity | How it is read | Licence |")
        lines.append("| --- | --- | --- | --- | --- |")
        for name, info in rows:
            exts = ", ".join(f"`{e}`" for e in info["extensions"]) or "detected by content"
            fidelity = (
                "`grammar` with the extra, `fallback` without"
                if name in dual
                else f"`{info['fidelity']}`"
            )
            lines.append(
                f"| {name} | {exts} | {fidelity} | {info['how']} | {info['licence']} |"
            )
        lines.append("")

    lines.append("## Licence position")
    lines.append("")
    lines.append(
        "Every shipped grammar is permissive. No GPL, LGPL, or AGPL grammar is used "
        "and none is vendored. Notices are in `THIRD_PARTY_NOTICES.md`, and the "
        "bundle audit that establishes the position for the `code-bundle` extra is "
        "in `docs/grammar_bundle_licences.json`."
    )
    lines.append("")
    lines.append("## Accuracy")
    lines.append("")
    lines.append(
        "Parse accuracy is measured per language against two labelled corpora and "
        "published in `docs/BENCHMARKS.md`. A language whose optional grammar is not "
        "installed in the measuring environment is reported not measured, never scored "
        "zero."
    )
    lines.append("")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(languages)} languages)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

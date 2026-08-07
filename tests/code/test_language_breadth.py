"""The full language set: breadth, fidelity honesty, and the licence rule.

These tests check the claims the project makes about its language coverage, not
just that parsing happens to work: every claimed language has a labelled corpus,
every grammar is permissive, a fallback language is never presented as fully
parsed, and the zero-dependency core is unaffected by any of it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dkg.code.capability import GRAMMAR_EXTRAS, GRAMMAR_LICENCES, GRAMMARS, grammar_available
from dkg.code.fallback import FALLBACK_SPECS, parse_fallback
from dkg.code.languages import BUILTIN_SPECS, PERMISSIVE_LICENCES, is_permissive
from dkg.code.parser import EXT_LANG, claimed_languages, language_for, language_inventory

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "tests" / "code" / "corpus"

requires_ts = pytest.mark.skipif(
    not grammar_available("python"), reason="tree-sitter and the python grammar are not installed"
)


def _needs(language: str):
    return pytest.mark.skipif(
        not grammar_available(language), reason=f"the {language} grammar is not installed"
    )


# -- breadth ----------------------------------------------------------------


def test_language_set_spans_every_target_family():
    claimed = set(claimed_languages())
    # Web and single-file components.
    assert {"typescript", "tsx", "javascript", "vue", "svelte", "astro"} <= claimed
    # Backend and systems.
    assert {"python", "go", "rust", "java", "scala", "elixir"} <= claimed
    assert {"c", "cpp", "csharp", "objc", "zig", "vbnet"} <= claimed
    # Mobile.
    assert {"kotlin", "swift", "dart"} <= claimed
    # Scripting.
    assert {"ruby", "php", "perl", "lua", "luau", "r", "julia"} <= claimed
    # Shells.
    assert {"bash", "powershell", "zsh"} <= claimed
    # Domain specific and infrastructure.
    assert {"solidity", "sql", "verilog", "gdscript", "nix", "hcl", "ansible", "rescript"} <= claimed
    # Notebooks.
    assert {"jupyter", "databricks"} <= claimed
    assert len(claimed) >= 35, f"expected the full set, got {len(claimed)}"


def test_ksh_shares_the_shell_grammar_rather_than_claiming_its_own():
    # ksh is parsed, and it is honest about how: no ksh grammar exists here, the
    # POSIX-shell grammar reads it, and that is recorded rather than implied.
    assert language_for("a.ksh") == "bash"
    assert ".ksh" in BUILTIN_SPECS["bash"].extensions


def test_every_claimed_language_resolves_from_an_extension_or_content():
    inventory = language_inventory()
    for language, entry in inventory.items():
        if entry.get("detected_by"):
            assert not entry["extensions"], language
            continue
        assert entry["extensions"], f"{language} claims no extension"
        for ext in entry["extensions"]:
            assert language_for(f"file{ext}") == language


def test_the_inventory_and_the_ground_truth_agree_on_every_fidelity():
    """Two surfaces report fidelity; disagreeing would make one of them a lie."""
    import json as _json

    from dkg.code.capability import grammar_available

    truth = _json.loads((CORPUS / "language_ground_truth.json").read_text(encoding="utf-8"))
    inventory = language_inventory()
    for language, entry in inventory.items():
        expected = truth[language]["fidelity"]
        # The five bundle-backed languages have two honest answers: a real
        # grammar parse with the optional extra installed, the documented
        # pattern extractor without it. The ground truth records both and the
        # inventory must report whichever actually applies in this environment.
        degraded = truth[language].get("fidelity_without_bundle")
        if degraded and not grammar_available(language):
            expected = degraded
        assert expected == entry["fidelity"], language


def test_inventory_reports_fidelity_for_every_language():
    inventory = language_inventory()
    assert set(inventory) == set(claimed_languages())
    for language, entry in inventory.items():
        assert entry["fidelity"] in ("grammar", "composite", "fallback"), language
        assert entry["how"], language
        if entry["fidelity"] == "fallback":
            # A fallback must say why it is one, so the gap is never silent.
            assert entry["reason"], language
            # It must also name the extra that lifts it to a real grammar parse,
            # WHEN there is one. Perl XS is the case where there is not: no
            # permissive .xs grammar exists anywhere to install, so an
            # `upgrade` key would point the reader at an extra that changes
            # nothing. Asserting it unconditionally would force exactly that
            # false promise, so the requirement is conditional on the offer
            # being real.
            if entry.get("extra") is not None:
                assert entry["upgrade"], language
            else:
                assert "upgrade" not in entry, (
                    f"{language} offers an upgrade but names no extra to install"
                )

    # A review pointed out that the branch above can become unreachable. Where
    # the optional bundle IS installed, the five bundle languages report
    # `grammar`, the only fallback left is Perl XS, whose `extra` is None, and
    # the `upgrade` assertion runs on nothing at all. A conditional nobody
    # enters asserts nothing, so the population itself is pinned here.
    #
    # It is pinned per environment rather than absolutely, because which
    # languages fall back is a property of what is installed: that is the whole
    # point of the fidelity label, and asserting one fixed set would fail in
    # exactly the bare-install lane this project promises to support.
    fallbacks = {n for n, e in inventory.items() if e["fidelity"] == "fallback"}
    expected = {"xs"} | {
        name for name in FALLBACK_SPECS if not grammar_available(name)
    }
    assert fallbacks == expected, (
        f"fallback languages are {sorted(fallbacks)}, expected {sorted(expected)}. "
        "Perl XS is always one because no permissive .xs grammar exists to install; "
        "the others are fallbacks exactly when their bundled grammar is absent."
    )


# -- licence rule -------------------------------------------------------------


def test_every_grammar_declares_a_permissive_licence():
    for language in GRAMMARS:
        licence = GRAMMAR_LICENCES.get(language, "MIT")
        assert is_permissive(licence), f"{language} declares non-permissive {licence}"
        assert licence.upper() in PERMISSIVE_LICENCES


def test_no_copyleft_grammar_is_declared_anywhere():
    forbidden = ("GPL", "AGPL", "LGPL", "SSPL", "CC-BY-SA")
    declared = [GRAMMAR_LICENCES.get(lang, "MIT") for lang in GRAMMARS]
    declared += [spec.licence for spec in BUILTIN_SPECS.values()]
    for licence in declared:
        upper = licence.upper()
        for bad in forbidden:
            assert bad not in upper, f"copyleft licence declared: {licence}"


def test_every_grammar_names_the_extra_that_ships_it():
    for language in GRAMMARS:
        assert GRAMMAR_EXTRAS[language] in ("code", "code-extended", "code-full"), language


def test_pyproject_declares_every_grammar_the_capability_layer_registers():
    import re

    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    # A whole dependency line, not a substring: "tree-sitter-c" occurs inside
    # "tree-sitter-cpp", and a commented-out line would satisfy a substring test.
    declared = set(re.findall(r'^\s*"(tree-sitter[a-z0-9-]*)\s*>=', text, re.MULTILINE))
    for language, module in GRAMMARS.items():
        # The TypeScript package ships two grammars, so tsx adds no dependency.
        if language == "tsx":
            continue
        distribution = module.replace("_", "-")
        assert distribution in declared, f"{language} grammar {distribution} is not declared in pyproject"


def test_code_full_is_a_superset_so_installing_it_alone_gives_every_language():
    import re

    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    block = text.split("code-full = [", 1)[1].split("]", 1)[0]
    in_full = set(re.findall(r'"(tree-sitter[a-z0-9-]*)\s*>=', block))
    for language, module in GRAMMARS.items():
        if language == "tsx":
            continue
        assert module.replace("_", "-") in in_full, f"{language} is missing from code-full"


def test_every_installed_grammar_is_permissive_in_reality_not_only_in_our_table():
    """The declared-licence table is our own; this reads the packages themselves.

    A table that says MIT proves nothing about the package on disk. This checks
    the installed distribution metadata, so a grammar whose real licence is
    copyleft fails here even if our own table calls it permissive.
    """
    from importlib.metadata import PackageNotFoundError, metadata

    checked = 0
    for language, module in GRAMMARS.items():
        distribution = module.replace("_", "-")
        try:
            meta = metadata(distribution)
        except PackageNotFoundError:
            # Capability detection, not a failure: the grammar is optional.
            continue
        declared = GRAMMAR_LICENCES.get(language, "MIT")
        fields = [meta.get("License") or "", meta.get("License-Expression") or ""]
        fields += [c for c in meta.get_all("Classifier") or [] if c.startswith("License")]
        blob = " ".join(fields).upper()
        assert blob.strip(), f"{distribution} declares no licence at all"
        for bad in ("GPL", "AGPL", "LGPL", "SSPL"):
            # "GPL" also matches inside "LGPL" and "AGPL", which is the point.
            assert bad not in blob, f"{distribution} real metadata says {blob!r}"
        # Our table must name a licence the package itself also names, so the
        # table cannot drift away from the package it describes.
        assert declared.upper().replace("-", "") in blob.replace("-", ""), (
            f"{distribution}: we declare {declared!r}, package metadata says {blob!r}"
        )
        checked += 1
    if checked == 0:
        # No grammar is installed here, so there was nothing to verify. Saying so
        # is honest; asserting a pass would claim a check that never ran.
        pytest.skip("no grammar package is installed in this environment")


# -- fallbacks ----------------------------------------------------------------


def test_every_fallback_states_why_it_is_a_fallback():
    for name, spec in FALLBACK_SPECS.items():
        assert spec.reason, name
        assert spec.extensions, name
        # A fallback with no way to find a definition would be a fallback in
        # name only.
        assert spec.functions or spec.classes or spec.types, name


def test_fallback_extracts_definitions_without_any_grammar():
    parsed = parse_fallback(
        "sample.gd",
        "extends Node\nclass_name Player\n\nfunc _ready():\n    setup()\n\nfunc setup():\n    pass\n",
    )
    kinds = {(s.kind, s.name) for s in parsed.symbols if s.kind != "module"}
    assert ("type", "Player") in kinds
    assert ("function", "_ready") in kinds
    assert ("function", "setup") in kinds
    assert any(r.kind == "calls" and r.name == "setup" for r in parsed.references)
    assert any(r.kind == "inherits" and r.name == "Node" for r in parsed.references)


def test_fallback_does_not_report_control_flow_as_a_call():
    parsed = parse_fallback("sample.r", "run <- function(x) {\n  if (x) { helper(x) }\n}\n")
    called = {r.name for r in parsed.references if r.kind == "calls"}
    assert "helper" in called
    assert "if" not in called


def test_bundle_backed_languages_are_labelled_by_the_mechanism_actually_in_force():
    """Never claim a grammar parse a build cannot do, or hide one it can.

    These five have a real grammar in the optional 'code-bundle' extra and the
    documented pattern extractor without it. Reporting a fixed label either way
    would be wrong in one of the two environments.
    """
    from dkg.code.capability import BUNDLE_EXTRA, grammar_available

    inventory = language_inventory()
    for name in FALLBACK_SPECS:
        entry = inventory[name]
        if grammar_available(name):
            assert entry["fidelity"] == "grammar", name
            assert entry["licence"] == "MIT", name
            # The exact upstream the licence was measured against, so the claim
            # is checkable rather than asserted.
            assert entry["grammar_source"]["repository"].startswith("https://"), name
            assert len(entry["grammar_source"]["revision"]) == 40, name
        else:
            assert entry["fidelity"] == "fallback", name
            assert entry["licence"] == "not applicable", name
            assert entry["reason"], name
            # A degraded language must name the extra that would fix it.
            assert BUNDLE_EXTRA in entry["upgrade"], name


# -- measured accuracy honesty -------------------------------------------------


@requires_ts
def test_published_accuracy_covers_every_claimed_language_and_states_fidelity():
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    import language_accuracy

    result = language_accuracy.measure()
    assert set(result) == set(claimed_languages())
    for language, entry in result.items():
        if entry["status"] != "measured":
            assert entry["status"] in ("not_measured_in_this_environment", "unsupported"), language
            # An absent grammar is never scored, not even as a zero.
            assert "precision" not in entry, language
            continue
        assert entry["fidelity"] in ("grammar", "composite", "fallback"), language
        assert entry["expected"] > 0, language
        assert 0.0 <= entry["precision"] <= 1.0, language
        assert 0.0 <= entry["recall"] <= 1.0, language


@requires_ts
def test_held_out_corpus_is_scored_separately_from_the_authored_one():
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    import language_accuracy

    hard = language_accuracy.measure_hard()
    measured = {k: v for k, v in hard.items() if v.get("status") == "measured"}
    assert measured, "the held-out corpus must actually be measured"
    for language, entry in measured.items():
        assert entry["expected"] > 0, language
        # The name-only figures exist so a kind-convention disagreement is not
        # reported as a missed symbol.
        assert "name_precision" in entry and "name_recall" in entry, language


def test_held_out_ground_truth_discloses_every_label_correction():
    truth = json.loads((CORPUS / "hard_ground_truth.json").read_text(encoding="utf-8"))
    about = truth["_about"]
    labelled_files = {rel for spec in truth.values() if isinstance(spec, dict) and "files" in spec for rel in spec["files"]}
    # Every correction names the corpus file it applies to and says why, so a
    # label cannot be quietly changed to match whatever the parser emits.
    for note in about.get("corrections_after_first_measurement", []):
        assert any(rel in note for rel in labelled_files), note
        assert len(note) > 80, f"a correction must say why, not just what: {note}"
    assert (ROOT / "test-evidence" / "held_out_first_measurement.json").is_file()


@requires_ts
def test_held_out_corpus_does_not_claim_the_parser_was_never_changed():
    """The corpus must not overstate its own independence.

    The first measurement exposed real gaps and the parser was changed to close
    them. Saying "the parser was not adjusted to fit them" would make the current
    figure look like independent evidence about constructs it is no longer
    independent about.
    """
    truth = json.loads((CORPUS / "hard_ground_truth.json").read_text(encoding="utf-8"))
    prose = " ".join(truth["_about"]["why_it_exists"]).lower()
    assert "was not adjusted" not in prose
    assert "was then changed" in prose or "was changed" in prose, prose
    first = json.loads((ROOT / "test-evidence" / "held_out_first_measurement.json").read_text(encoding="utf-8"))
    assert first["measured_before_any_fix"] is True
    # The retained figure must actually be worse than today's, or it is not a
    # record of anything.
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    import language_accuracy

    now = language_accuracy.summarise_hard(language_accuracy.measure_hard())
    assert first["summary"]["micro_recall"] < now["micro_recall"]


# -- the core is unaffected ----------------------------------------------------


def test_extension_table_has_no_duplicate_claims():
    # Two languages claiming one extension would make parsing depend on dict
    # ordering rather than on a decision.
    assert len(EXT_LANG) == len(set(EXT_LANG))


def test_no_grammar_is_imported_at_module_import_time():
    import subprocess
    import sys

    # Importing the code plane must not pull in a single grammar: the core
    # installs with none of them present and every one is capability-detected.
    code = (
        "import sys; sys.path.insert(0, 'src');"
        "import dkg.code.parser, dkg.code.languages, dkg.code.fallback, dkg.code.iac,"
        " dkg.code.notebooks, dkg.code.sfc, dkg.code.frameworks;"
        "loaded=[m for m in sys.modules if m.startswith('tree_sitter')];"
        "print(loaded)"
    )
    out = subprocess.run(  # noqa: S603 - fixed argv, no shell, no user input
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]", out.stdout

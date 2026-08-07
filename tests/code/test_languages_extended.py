"""The wider built-in language set: TypeScript, Java, Ruby, Rust.

Each language is gated on its own grammar rather than on the code extra as a
whole, so a host with only the starter grammars skips these honestly instead of
failing, and a host with some of the wider set still exercises those.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

try:
    import tree_sitter  # noqa: F401

    _TS = True
except Exception:
    _TS = False

requires_ts = pytest.mark.skipif(not _TS, reason="tree-sitter not installed (the 'code' extra)")

if _TS:
    from dkg.code.capability import (
        GRAMMAR_ACCESSORS,
        GRAMMAR_EXTRAS,
        GRAMMARS,
        available_languages,
        grammar_available,
        probe,
    )
    from dkg.code.languages import BUILTIN_SPECS, is_permissive
    from dkg.code.parser import EXT_LANG, language_for, parse_source

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "tests" / "code" / "corpus"
EXTENDED = ("typescript", "java", "ruby", "rust")


def _needs(language: str):
    return pytest.mark.skipif(
        not (_TS and grammar_available(language)),
        reason=f"grammar for {language} not installed (the 'code-extended' extra)",
    )


def _symbols(relative: str) -> set[tuple[str, str]]:
    parsed = parse_source(CORPUS / relative)
    return {(s.kind, s.name) for s in parsed.symbols if s.kind != "module"}


def _refs(relative: str) -> set[tuple[str, str]]:
    parsed = parse_source(CORPUS / relative)
    return {(r.kind, r.name) for r in parsed.references}


# -- registration and capability detection ----------------------------------


@requires_ts
def test_every_extended_language_is_registered_end_to_end():
    for lang in EXTENDED:
        assert lang in GRAMMARS, lang
        assert GRAMMAR_EXTRAS[lang] == "code-extended", lang
        # Reachable from a file extension, otherwise ingestion would never
        # select it no matter how good the extractor is.
        assert lang in EXT_LANG.values(), lang


@requires_ts
def test_starter_languages_stay_in_the_original_extra():
    for lang in ("python", "javascript", "go"):
        assert GRAMMAR_EXTRAS[lang] == "code"


@requires_ts
def test_extension_mapping_resolves_the_new_languages():
    assert language_for("a.ts") == "typescript"
    assert language_for("a.java") == "java"
    assert language_for("a.rb") == "ruby"
    assert language_for("a.rs") == "rust"
    # TSX is a separate grammar in the same package and is now claimed and
    # measured, so it resolves rather than being deliberately withheld.
    assert language_for("a.tsx") == "tsx"
    # An unknown extension still resolves to nothing rather than guessing.
    assert language_for("a.unknownext") is None


@requires_ts
def test_capability_probe_reports_each_language_and_its_extra():
    p = probe()
    for lang in EXTENDED:
        assert lang in p["languages"]
        assert isinstance(p["languages"][lang], bool)
        assert p["extras"][lang] == "code-extended"
    # available_languages never claims a language whose grammar is absent.
    for lang in available_languages():
        assert grammar_available(lang), lang


@requires_ts
def test_absent_grammar_names_the_right_extra_instead_of_failing_opaquely():
    from dkg.code.capability import get_language
    from dkg.core.errors import UnsupportedFormatError

    with pytest.raises(UnsupportedFormatError) as exc:
        get_language("a-language-with-no-grammar")
    assert "no grammar registered" in str(exc.value)


@requires_ts
def test_builtin_specs_declare_permissive_licences_only():
    from dkg.code.capability import BUNDLE_GRAMMARS, BUNDLE_MODULE

    for name, spec in BUILTIN_SPECS.items():
        assert is_permissive(spec.licence), f"{name} declares {spec.licence}"
        if name in BUNDLE_GRAMMARS:
            # Served by the multi-grammar bundle rather than by a dedicated
            # package, so its module is the bundle and its licence is the one
            # measured at the upstream revision the bundle compiles.
            assert spec.grammar_module == BUNDLE_MODULE
        else:
            assert spec.grammar_module == GRAMMARS[name]


@requires_ts
def test_typescript_uses_a_dedicated_grammar_accessor():
    # The TypeScript package ships two grammars in one module, so the default
    # accessor would be ambiguous. Everything else uses the default.
    assert GRAMMAR_ACCESSORS["typescript"] == "language_typescript"
    for lang in ("java", "ruby", "rust", "python"):
        assert lang not in GRAMMAR_ACCESSORS


# -- per-language extraction ------------------------------------------------


@_needs("typescript")
def test_typescript_extracts_types_classes_methods_and_functions():
    got = _symbols("parse/typescript.ts")
    assert got == {
        ("type", "Shape"),
        ("class", "Rect"),
        ("class", "Square"),
        ("method", "area"),
        ("method", "scale"),
        ("method", "side"),
        ("function", "makeRect"),
        ("function", "tsHelper"),
    }


@_needs("typescript")
def test_typescript_records_class_inheritance():
    # TypeScript nests the base class one level deeper than JavaScript does;
    # without descending into the extends clause this edge is silently lost.
    assert ("inherits", "Rect") in _refs("parse/typescript.ts")


@_needs("typescript")
def test_javascript_extraction_is_unchanged_by_the_typescript_support():
    got = _symbols("parse/javascript.js")
    assert got == {
        ("class", "Shape"),
        ("class", "Circle"),
        ("method", "perimeter"),
        ("method", "radius"),
        ("function", "makeCircle"),
        ("function", "jsHelper"),
    }
    assert ("inherits", "Shape") in _refs("parse/javascript.js")


@_needs("java")
def test_java_extracts_classes_and_methods_with_inheritance():
    got = _symbols("parse/java.java")
    assert got == {
        ("class", "Animal"),
        ("class", "Dog"),
        ("class", "Helpers"),
        ("method", "eat"),
        ("method", "bark"),
        ("method", "makeDog"),
        ("method", "javaHelper"),
    }
    refs = _refs("parse/java.java")
    assert ("inherits", "Animal") in refs
    assert ("calls", "eat") in refs


@_needs("ruby")
def test_ruby_distinguishes_methods_from_top_level_definitions():
    got = _symbols("parse/ruby.rb")
    assert got == {
        ("class", "Animal"),
        ("class", "Dog"),
        ("method", "eat"),
        ("method", "bark"),
        ("function", "make_dog"),
        ("function", "ruby_helper"),
    }
    assert ("inherits", "Animal") in _refs("parse/ruby.rb")


@_needs("rust")
def test_rust_impl_block_is_scope_only_and_yields_no_duplicate_node():
    parsed = parse_source(CORPUS / "parse/rust.rs")
    got = {(s.kind, s.name) for s in parsed.symbols if s.kind != "module"}
    assert got == {
        ("type", "Point"),
        ("method", "norm"),
        ("function", "make_point"),
        ("function", "rust_helper"),
    }
    qualified = [s.qualified for s in parsed.symbols]
    # The impl names the method's owner, so norm belongs to Point ...
    assert any(q.endswith("::Point.norm") for q in qualified)
    # ... and Point itself appears exactly once, from the struct declaration.
    assert sum(1 for q in qualified if q.endswith("::Point")) == 1
    assert len(qualified) == len(set(qualified)), "duplicate qualified name"


# -- measured accuracy ------------------------------------------------------


@requires_ts
def test_ground_truth_covers_every_claimed_language():
    from dkg.code.parser import claimed_languages

    gt = json.loads((CORPUS / "language_ground_truth.json").read_text(encoding="utf-8"))
    labelled = {k for k in gt if not k.startswith("_")}
    # A language must not be claimed without a labelled corpus behind it.
    assert labelled == set(claimed_languages()), "every claimed language needs ground truth"
    for lang, spec in gt.items():
        if lang.startswith("_"):
            continue
        assert spec["files"], lang
        for rel, expected in spec["files"].items():
            assert (CORPUS / "langs" / rel).is_file(), (lang, rel)
            assert expected, (lang, rel)
        # "several files and many constructs each", not one tiny file.
        assert len(spec["files"]) >= 2, lang
        assert sum(len(v) for v in spec["files"].values()) >= 4, lang


@requires_ts
def test_measured_accuracy_is_reported_per_language_and_skips_absent_grammars():
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    import code_accuracy

    from dkg.code.parser import claimed_languages

    result = code_accuracy.measure_parsing()
    assert set(result) == set(claimed_languages())
    for lang, entry in result.items():
        if entry["status"] != "measured":
            # Absent grammar is reported as not measured, never as a zero score.
            assert entry["status"] in ("not_measured_in_this_environment", "unsupported"), lang
            assert "precision" not in entry
            continue
        assert 0.0 <= entry["precision"] <= 1.0
        assert 0.0 <= entry["recall"] <= 1.0
        assert entry["correct"] <= entry["expected"]

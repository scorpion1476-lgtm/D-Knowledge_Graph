"""Source-code plane capability detection.

Reports whether tree-sitter and each grammar are importable so the ingestion
layer can degrade cleanly and tests can skip honestly. All checks are lazy.
"""

from __future__ import annotations

import importlib
from functools import cache
from typing import Any

# Language name to grammar module. Every grammar here is permissive: MIT except
# tree-sitter-elixir and tree-sitter-hcl, which are Apache-2.0. No GPL, LGPL, or
# AGPL grammar is used. Licences are recorded per grammar in GRAMMAR_LICENCES
# and attributed in THIRD_PARTY_NOTICES.md.
GRAMMARS = {
    "python": "tree_sitter_python",
    "javascript": "tree_sitter_javascript",
    "go": "tree_sitter_go",
    "typescript": "tree_sitter_typescript",
    "tsx": "tree_sitter_typescript",
    "java": "tree_sitter_java",
    "ruby": "tree_sitter_ruby",
    "rust": "tree_sitter_rust",
    "c": "tree_sitter_c",
    "cpp": "tree_sitter_cpp",
    "csharp": "tree_sitter_c_sharp",
    "objc": "tree_sitter_objc",
    "zig": "tree_sitter_zig",
    "kotlin": "tree_sitter_kotlin",
    "swift": "tree_sitter_swift",
    "dart": "tree_sitter_dart",
    "php": "tree_sitter_php",
    "lua": "tree_sitter_lua",
    "luau": "tree_sitter_luau",
    "julia": "tree_sitter_julia",
    "scala": "tree_sitter_scala",
    "elixir": "tree_sitter_elixir",
    "bash": "tree_sitter_bash",
    "zsh": "tree_sitter_zsh",
    "powershell": "tree_sitter_powershell",
    "solidity": "tree_sitter_solidity",
    "sql": "tree_sitter_sql",
    "verilog": "tree_sitter_verilog",
    "nix": "tree_sitter_nix",
    "hcl": "tree_sitter_hcl",
    "yaml": "tree_sitter_yaml",
}

# Five languages have no dedicated Tree-sitter package this project can depend
# on: R, GDScript, ReScript, and VB.NET publish none to PyPI at all, and
# tree-sitter-perl publishes no wheel for every platform the project supports.
# A permissively licensed grammar for each exists inside a bundle that compiles
# hundreds of grammars into one shared object.
#
# The bundle is taken, in its own optional extra, on evidence rather than on
# trust. It publishes no per-grammar licence manifest, but it does publish the
# upstream repository and the exact revision compiled in for every grammar,
# which makes the licences auditable from the primary source.
# scripts/audit_grammar_bundle.py resolves all of them and writes
# docs/grammar_bundle_licences.json; the five enabled here are pinned below with
# the licence measured at that revision. Nothing is assumed permissive.
#
# Because the bundle is one shared object holding every grammar, installing the
# extra ships all of them, which is why the whole bundle is audited and not only
# the five. The audit is a committed artifact and a test checks it.
BUNDLE_MODULE = "tree_sitter_language_pack"
BUNDLE_EXTRA = "code-bundle"

# Our language name to the name the bundle uses for it.
BUNDLE_GRAMMARS = {
    "r": "r",
    "gdscript": "gdscript",
    "rescript": "rescript",
    "vbnet": "vb",
    "perl": "perl",
}

# Licence measured at the pinned revision the bundle compiles, with the upstream
# it was read from. See docs/grammar_bundle_licences.json for all 371.
BUNDLE_GRAMMAR_SOURCES = {
    "r": ("MIT", "https://github.com/r-lib/tree-sitter-r", "58a22794466c0fc15b0d3b40531db751593721e8"),
    "gdscript": ("MIT", "https://github.com/PrestonKnopp/tree-sitter-gdscript", "c5c8fa4861b5a4f04a7e60d97587fc3b6cc5639e"),
    "rescript": ("MIT", "https://github.com/rescript-lang/tree-sitter-rescript", "19ed8a8e6bcc844b71c37e9edaffc60c77f74d7c"),
    "vbnet": ("MIT", "https://github.com/CodeAnt-AI/tree-sitter-vb-dotnet", "cfca210ce8fdcb5245bd9cd5c47ce0a21a8488d5"),
    "perl": ("MIT", "https://github.com/tree-sitter-perl/tree-sitter-perl", "0390ac6f4e26f5805c9d7d9b950685436faa6359"),
}

# Declared licence per grammar, so the licence audit and the notices file can be
# checked against one table rather than against prose.
GRAMMAR_LICENCES = {
    "elixir": "Apache-2.0",
    "hcl": "Apache-2.0",
    **{lang: spdx for lang, (spdx, _repo, _rev) in BUNDLE_GRAMMAR_SOURCES.items()},
}

# The extra that ships each grammar. The starter set stays in 'code' so the
# original footprint is unchanged; the wider sets are separate opt-in extras.
# Reported in the error message so a user is told which extra to install rather
# than a generic failure.
_STARTER = ("python", "javascript", "go")
_EXTENDED = ("typescript", "tsx", "java", "ruby", "rust")
GRAMMAR_EXTRAS = {
    lang: ("code" if lang in _STARTER else "code-extended" if lang in _EXTENDED else "code-full")
    for lang in GRAMMARS
}
GRAMMAR_EXTRAS.update({lang: BUNDLE_EXTRA for lang in BUNDLE_GRAMMARS})

# Grammar modules that do not expose the default ``language()`` accessor. The
# TypeScript package ships two grammars in one module, and the PHP package ships
# a full grammar plus a PHP-only one, so the language has to be named explicitly.
GRAMMAR_ACCESSORS = {
    "typescript": "language_typescript",
    "tsx": "language_tsx",
    "php": "language_php",
}


def tree_sitter_available() -> bool:
    try:
        import tree_sitter  # noqa: F401

        return True
    except ImportError:
        return False


def bundle_available() -> bool:
    """Whether the multi-grammar bundle extra is installed."""
    try:
        importlib.import_module(BUNDLE_MODULE)
        return True
    except ImportError:
        return False


def grammar_available(language: str) -> bool:
    if language in BUNDLE_GRAMMARS:
        return bundle_available()
    mod = GRAMMARS.get(language)
    if not mod:
        return False
    try:
        importlib.import_module(mod)
        return True
    except ImportError:
        return False


@cache
def get_bundle_language(language: str) -> Any:
    """Return a tree_sitter.Language served by the grammar bundle.

    Kept separate from ``get_language`` so the bundle is imported only when one
    of the five languages that needs it is actually parsed. A build without the
    extra never touches it, which is what keeps the core zero-dependency.
    """
    from ..core.errors import UnsupportedFormatError

    bundle_name = BUNDLE_GRAMMARS.get(language)
    if not bundle_name:
        raise UnsupportedFormatError(f"{language!r} is not served by the grammar bundle")
    try:
        mod = importlib.import_module(BUNDLE_MODULE)
    except ImportError as e:
        raise UnsupportedFormatError(
            f"code parsing for {language!r} requires the {BUNDLE_EXTRA!r} extra: "
            f"pip install d-knowledge-graph[{BUNDLE_EXTRA}]"
        ) from e
    return mod.get_language(bundle_name)


@cache
def get_language(language: str) -> Any:
    """Return a tree_sitter.Language for the language, or raise if unavailable."""
    from ..core.errors import UnsupportedFormatError

    if language in BUNDLE_GRAMMARS:
        return get_bundle_language(language)
    mod_name = GRAMMARS.get(language)
    if not mod_name:
        raise UnsupportedFormatError(f"no grammar registered for language {language!r}")
    extra = GRAMMAR_EXTRAS.get(language, "code")
    try:
        import tree_sitter

        mod = importlib.import_module(mod_name)
    except ImportError as e:
        raise UnsupportedFormatError(
            f"code parsing for {language!r} requires the {extra!r} extra: "
            f"pip install d-knowledge-graph[{extra}]"
        ) from e
    accessor = GRAMMAR_ACCESSORS.get(language, "language")
    fn = getattr(mod, accessor, None)
    if fn is None:
        raise UnsupportedFormatError(
            f"grammar module {mod_name!r} exposes no {accessor!r} accessor for {language!r}"
        )
    return tree_sitter.Language(fn())


def all_grammar_languages() -> list[str]:
    """Every language with a real grammar, dedicated package or bundle alike."""
    return sorted(set(GRAMMARS) | set(BUNDLE_GRAMMARS))


def available_languages() -> list[str]:
    return [lang for lang in all_grammar_languages() if grammar_available(lang)]


def probe() -> dict:
    langs = {lang: grammar_available(lang) for lang in all_grammar_languages()}
    return {
        "tree_sitter": tree_sitter_available(),
        "languages": langs,
        "extras": dict(GRAMMAR_EXTRAS),
        "bundle": bundle_available(),
        "code_ready": tree_sitter_available() and any(langs.values()),
    }

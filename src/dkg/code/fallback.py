"""Documented lower-fidelity extraction, used when the grammar bundle is absent.

Five languages have no *dedicated* Tree-sitter grammar package this project can
depend on. For R, GDScript, and ReScript no dedicated package is published to
PyPI at all; the VB.NET package publishes a Windows-only wheel and no source
distribution; and tree-sitter-perl, which is MIT, publishes no wheel for every
supported platform and needs the Tree-sitter C headers to build from source.

A permissively licensed grammar for all five exists inside a bundle of roughly
370 grammars published as a single package, and the project now takes it in the
optional ``code-bundle`` extra. An earlier version of this file said the bundle
was declined because it could not be attributed without auditing every grammar
in it. The premise was right and the conclusion was wrong: the bundle publishes
the upstream repository and the exact revision compiled in for every grammar, so
it CAN be audited, and scripts/audit_grammar_bundle.py does audit all of them
into docs/grammar_bundle_licences.json. All 371 resolved permissive with none
copyleft. See THIRD_PARTY_NOTICES.md.

This extractor is therefore no longer the only path for those five: it is the
DEGRADED path, used when the optional extra is not installed, which keeps the
zero-dependency core working over them. Which one ran is reported by the
language inventory rather than assumed, so a build without the extra never
claims grammar fidelity it does not have.

Each language gets a line-oriented pattern extractor here that emits the same
Symbol and Reference shapes the Tree-sitter path emits, so the code graph, blast
radius, execution flow, search, and the CLI and MCP surfaces work over them
unchanged either way.

This is honestly lower fidelity and is labelled that way everywhere it surfaces:

- It matches definitions on a line, so a definition written across several lines,
  or one produced by a macro, is missed.
- It has no scope model beyond indentation or braces, so a nested definition may
  be attributed to the wrong parent.
- It cannot tell code from a string or a comment except by the simple comment
  rules encoded per language, so a definition-shaped line inside a here-document
  can be a false positive.

Every language routed here is reported as fallback level in docs/BENCHMARKS.md
with its own measured precision and recall. A fallback language is never scored
as though it had been fully parsed, and never reported as production ready on
the strength of the grammar path's numbers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .model import FIDELITY_FALLBACK, ParsedFile, Reference, Symbol

# Fidelity label carried into every report and benchmark row for these
# languages, so a fallback result is never confused with a parsed one.
FIDELITY = "fallback"


@dataclass(frozen=True)
class FallbackSpec:
    """Line patterns that locate definitions and references in one language."""

    name: str
    extensions: tuple[str, ...]
    # Why this language is on the fallback path rather than the grammar path.
    reason: str
    classes: tuple[str, ...] = ()
    types: tuple[str, ...] = ()
    functions: tuple[str, ...] = ()
    methods: tuple[str, ...] = ()
    imports: tuple[str, ...] = ()
    inherits: tuple[str, ...] = ()
    calls: tuple[str, ...] = ()
    comment_prefixes: tuple[str, ...] = ("#",)
    # How a class declaration scopes the definitions after it.
    #   indent: the class owns everything indented under it (Python-like).
    #   brace:  the class owns everything until the brace depth drops back.
    #   vb:     the class owns everything until its End Class line.
    #   flat:   a class declaration is not a lexical scope at all, so it never
    #           captures what follows. R is the case: setClass declares a class
    #           and the functions after it are ordinary top-level functions.
    nesting: str = "indent"
    test_prefix: str = "test"
    compiled: dict = field(default_factory=dict, compare=False, repr=False)


_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
# An identifier that must start with a capital, for languages where that is what
# separates a module or type name from a value name.
_UPPER_IDENT = r"[A-Z][A-Za-z0-9_]*"

FALLBACK_SPECS: dict[str, FallbackSpec] = {
    "r": FallbackSpec(
        name="r",
        extensions=(".r", ".rmd"),
        reason=(
            "no dedicated Tree-sitter R package is published to PyPI. A grammar exists inside a "
            "bundle of roughly 370 grammars, which this project declines because it cannot "
            "enumerate and attribute the licence of every grammar such a bundle statically ships"
        ),
        functions=(rf"^\s*(?:`(?P<bt>[^`]+)`|(?P<name>{_IDENT}(?:\.{_IDENT})*))\s*(?:<-|=)\s*function\s*\(",),
        classes=(
            rf"^\s*set(?:Class|RefClass)\s*\(\s*[\"'](?P<name>{_IDENT})[\"']",
            # R6 is the dominant modern object system and declares its class by
            # binding an R6Class call to a name.
            rf"^\s*(?P<name>{_IDENT}(?:\.{_IDENT})*)\s*(?:<-|=)\s*R6Class\s*\(",
        ),
        methods=(
            # setMethod names the class it attaches to in its second argument,
            # which is how an R method finds its owner: R has no lexical class
            # body for it to sit inside.
            rf"^\s*setMethod\s*\(\s*[\"'](?P<name>{_IDENT})[\"']\s*,\s*[\"'](?P<owner>{_IDENT})[\"']",
        ),
        # setGeneric declares a generic function with no body of its own.
        types=(rf"^\s*setGeneric\s*\(\s*[\"'](?P<name>{_IDENT})[\"']",),
        imports=(rf"^\s*(?:library|require)\s*\(\s*[\"']?(?P<name>{_IDENT}(?:\.{_IDENT})*)",),
        calls=(rf"(?P<name>{_IDENT}(?:\.{_IDENT})*)\s*\(",),
        comment_prefixes=("#",),
        nesting="flat",
        test_prefix="test",
    ),
    "gdscript": FallbackSpec(
        name="gdscript",
        extensions=(".gd",),
        reason=(
            "no dedicated Tree-sitter GDScript package is published to PyPI. A grammar exists inside a "
            "bundle of roughly 370 grammars, which this project declines because it cannot enumerate and "
            "attribute the licence of every grammar such a bundle statically ships"
        ),
        classes=(rf"^\s*class\s+(?P<name>{_IDENT})",),
        types=(rf"^\s*class_name\s+(?P<name>{_IDENT})",),
        functions=(rf"^\s*(?:static\s+)?func\s+(?P<name>{_IDENT})\s*\(",),
        imports=(rf"^\s*(?:const|var)\s+{_IDENT}\s*(?::\s*\w+\s*)?=\s*(?:preload|load)\s*\(\s*[\"'](?P<name>[^\"']+)",),
        inherits=(rf"^\s*extends\s+(?P<name>{_IDENT})",),
        calls=(rf"(?P<name>{_IDENT})\s*\(",),
        comment_prefixes=("#",),
        nesting="indent",
        test_prefix="test",
    ),
    "rescript": FallbackSpec(
        name="rescript",
        extensions=(".res", ".resi"),
        reason=(
            "no dedicated Tree-sitter ReScript package is published to PyPI. A grammar exists inside a "
            "bundle of roughly 370 grammars, which this project declines because it cannot enumerate and "
            "attribute the licence of every grammar such a bundle statically ships"
        ),
        types=(rf"^\s*(?:export\s+)?type\s+(?:rec\s+)?(?P<name>{_IDENT})",),
        classes=(rf"^\s*module\s+(?P<name>{_UPPER_IDENT})",),
        functions=(rf"^\s*(?:export\s+)?let\s+(?:rec\s+)?(?P<name>{_IDENT})\s*(?:[:=].*)?=\s*(?:async\s+)?\(",),
        imports=(rf"^\s*open\s+(?P<name>{_IDENT}(?:\.{_IDENT})*)",),
        calls=(rf"(?P<name>{_IDENT})\s*\(",),
        comment_prefixes=("//",),
        nesting="brace",
        test_prefix="test",
    ),
    "vbnet": FallbackSpec(
        name="vbnet",
        extensions=(".vb",),
        reason=(
            "the dedicated Tree-sitter VB.NET package on PyPI ships a Windows-only wheel and no "
            "source distribution, so it cannot be installed on Linux or macOS. A grammar also "
            "exists inside a bundle of roughly 370 grammars, which this project declines because "
            "it cannot enumerate and attribute the licence of every grammar such a bundle "
            "statically ships"
        ),
        classes=(rf"^\s*(?:Public\s+|Private\s+|Friend\s+|Protected\s+|Partial\s+|MustInherit\s+|NotInheritable\s+)*Class\s+(?P<name>{_IDENT})",),
        types=(
            rf"^\s*(?:Public\s+|Private\s+|Friend\s+|Protected\s+)*Interface\s+(?P<name>{_IDENT})",
            rf"^\s*(?:Public\s+|Private\s+|Friend\s+|Protected\s+)*(?:Structure|Enum)\s+(?P<name>{_IDENT})",
            rf"^\s*(?:Public\s+|Private\s+|Friend\s+)*Module\s+(?P<name>{_IDENT})",
        ),
        functions=(
            rf"^\s*(?:Public\s+|Private\s+|Friend\s+|Protected\s+|Shared\s+|Overrides\s+|Overridable\s+|MustOverride\s+)*(?:Function|Sub)\s+(?P<name>{_IDENT})\s*\(",
        ),
        imports=(rf"^\s*Imports\s+(?P<name>{_IDENT}(?:\.{_IDENT})*)",),
        inherits=(rf"^\s*(?:Inherits|Implements)\s+(?P<name>{_IDENT}(?:\.{_IDENT})*)",),
        calls=(rf"(?P<name>{_IDENT})\s*\(",),
        comment_prefixes=("'",),
        nesting="vb",
        test_prefix="test",
    ),
    "perl": FallbackSpec(
        name="perl",
        extensions=(".pl", ".pm", ".t"),
        reason=(
            "tree-sitter-perl is MIT but publishes no wheel for macOS on x86_64, and its source "
            "distribution needs the Tree-sitter C headers, so the dedicated package is not "
            "installable on every platform this project supports. A grammar also exists inside a "
            "bundle of roughly 370 grammars, which this project declines because it cannot "
            "enumerate and attribute the licence of every grammar such a bundle statically ships"
        ),
        classes=(rf"^\s*package\s+(?P<name>{_IDENT}(?:::{_IDENT})*)\s*[;{{]",),
        functions=(rf"^\s*sub\s+(?P<name>{_IDENT})",),
        imports=(rf"^\s*(?:use|require)\s+(?P<name>{_IDENT}(?:::{_IDENT})*)",),
        inherits=(rf"^\s*(?:use\s+parent|use\s+base|our\s+\@ISA\s*=)\s*.*?[\"'(]\s*(?P<name>{_IDENT}(?:::{_IDENT})*)",),
        calls=(rf"(?P<name>{_IDENT})\s*\(",),
        comment_prefixes=("#",),
        nesting="none",
        test_prefix="test",
    ),
}

# Reserved words that look like a call but are control flow, so a fallback does
# not report `if (...)` as a call to a function named "if".
_NOT_CALLS = {
    "if", "for", "while", "switch", "return", "catch", "elif", "else", "unless",
    "until", "foreach", "do", "and", "or", "not", "in", "print", "function",
    "func", "sub", "class", "def", "let", "type", "module", "match", "when",
    "Function", "Sub", "If", "For", "While", "Select", "Case", "Return", "Then",
}


def _compiled(spec: FallbackSpec, key: str) -> list[re.Pattern]:
    cache = spec.compiled.setdefault(key, None)
    if cache is None:
        cache = [re.compile(p) for p in getattr(spec, key)]
        spec.compiled[key] = cache
    return cache


def fallback_spec_for(path: str | Path) -> FallbackSpec | None:
    ext = Path(path).suffix.lower()
    for spec in FALLBACK_SPECS.values():
        if ext in spec.extensions:
            return spec
    return None


def fallback_languages() -> list[str]:
    return sorted(FALLBACK_SPECS)


def _matched_name(pattern: re.Pattern, line: str) -> str | None:
    m = pattern.search(line)
    if m is None:
        return None
    groups = m.groupdict()
    for key in ("name", "bt"):
        val = groups.get(key)
        if val:
            return val
    return None


def _strip_comment(line: str, prefixes: tuple[str, ...]) -> str:
    """Remove a trailing comment. Quote-aware only to the extent of not cutting
    inside a quoted string on the same line."""
    in_single = in_double = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif not in_single and not in_double:
            for prefix in prefixes:
                if line.startswith(prefix, i):
                    return line[:i]
    return line


def parse_fallback(path: str | Path, text: str | None = None, *, spec: FallbackSpec | None = None) -> ParsedFile:
    """Extract symbols and references from one file without a grammar."""
    path = str(path)
    spec = spec or fallback_spec_for(path)
    if spec is None:
        from ..core.errors import UnsupportedFormatError

        raise UnsupportedFormatError(f"no fallback extractor for {path}")
    if text is None:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    pf = ParsedFile(path=path, language=spec.name, fidelity=FIDELITY_FALLBACK)
    module_q = path
    stem = Path(path).stem.lower()
    is_test_file = spec.test_prefix in stem or stem.endswith("_test") or stem.startswith("test")
    pf.symbols.append(Symbol("module", Path(path).name, module_q, 1, max(len(lines), 1), "", None))

    class_ctx: str | None = None
    class_indent = -1
    brace_depth = 0
    class_depth = -1
    func_ctx: str | None = None
    func_indent = -1
    emitted: set[str] = {module_q}

    def add(kind: str, name: str, lineno: int, parent: str | None) -> str:
        """Emit one symbol, qualified under its parent when it has one."""
        q = f"{parent}.{name}" if parent and parent != module_q else f"{path}::{name}"
        pf.symbols.append(Symbol(kind, name, q, lineno, lineno, lines[lineno - 1].strip(), parent or module_q))
        emitted.add(q)
        return q

    for lineno, raw in enumerate(lines, start=1):
        line = _strip_comment(raw, spec.comment_prefixes)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if spec.nesting == "flat":
            # A class declaration in this language opens no block, so nothing
            # after it belongs to it. Without this, every top-level function
            # written below a setClass call became one of its methods.
            class_ctx = None
        elif spec.nesting == "indent":
            if class_ctx is not None and indent <= class_indent and line.strip():
                class_ctx, class_indent = None, -1
            if func_ctx is not None and indent <= func_indent:
                func_ctx, func_indent = None, -1
        elif spec.nesting == "brace":
            if class_ctx is not None and brace_depth <= class_depth:
                class_ctx, class_depth = None, -1
        elif spec.nesting == "vb":
            if re.match(r"^\s*End\s+(Class|Module|Structure|Interface|Enum)\b", line):
                class_ctx = None
            if re.match(r"^\s*End\s+(Function|Sub)\b", line):
                func_ctx = None

        handled = False
        # The name this line defines, so a one-line definition does not record
        # itself as one of its own calls.
        defined: str | None = None
        for pattern in _compiled(spec, "classes"):
            name = _matched_name(pattern, line)
            if name:
                declared = add("class", name, lineno, module_q)
                if spec.nesting != "flat":
                    class_ctx = declared
                    class_indent, class_depth = indent, brace_depth
                func_ctx = None
                handled, defined = True, name
                break
        if not handled:
            for pattern in _compiled(spec, "types"):
                name = _matched_name(pattern, line)
                if name:
                    declared = add("type", name, lineno, module_q)
                    if spec.nesting != "flat":
                        class_ctx = declared
                        class_indent, class_depth = indent, brace_depth
                    handled, defined = True, name
                    break
        if not handled:
            for key in ("functions", "methods"):
                for pattern in _compiled(spec, key):
                    match = pattern.search(line)
                    name = _matched_name(pattern, line)
                    if name:
                        # A pattern may name the owner explicitly, which is how a
                        # language that has no lexical class body attaches a
                        # method to its class.
                        owner = (match.groupdict().get("owner") if match else None) or None
                        owner_q = f"{path}::{owner}" if owner else class_ctx
                        if is_test_file or name.startswith(spec.test_prefix):
                            kind = "test"
                        elif owner_q:
                            kind = "method"
                        else:
                            kind = "function"
                        parent = owner_q if owner_q in emitted else module_q
                        func_ctx = add(kind, name, lineno, parent)
                        func_indent = indent
                        handled, defined = True, name
                        break
                if handled:
                    break
        # Inheritance is checked before imports because the languages here spell
        # it with the same keyword: `use parent 'My::Base'` is both shaped like
        # a Perl import and is in fact an inheritance declaration.
        if not handled:
            for pattern in _compiled(spec, "inherits"):
                name = _matched_name(pattern, line)
                if name:
                    owner = class_ctx if class_ctx in emitted else module_q
                    pf.references.append(Reference(owner, "inherits", name.split("::")[-1].split(".")[-1]))
                    handled = True
                    break
        if not handled:
            for pattern in _compiled(spec, "imports"):
                name = _matched_name(pattern, line)
                if name:
                    pf.references.append(Reference(module_q, "imports", name.split("::")[-1].split("/")[-1]))
                    handled = True
                    break
        # Calls are scanned on every line that is not an import or inheritance,
        # including a line that also holds a definition, because these languages
        # routinely write a whole one-line function body.
        if not handled or defined is not None:
            for pattern in _compiled(spec, "calls"):
                for m in pattern.finditer(line):
                    callee = m.group("name")
                    tail = callee.split("::")[-1].split(".")[-1]
                    if tail in _NOT_CALLS or not tail or tail == defined:
                        continue
                    pf.references.append(Reference(func_ctx or class_ctx or module_q, "calls", tail))

        brace_depth += line.count("{") - line.count("}")

    # Same guarantees the grammar path gives: no anonymous symbols, one node per
    # qualified name, and one reference per (source, kind, target).
    seen_q: set[str] = set()
    kept: list[Symbol] = []
    for s in pf.symbols:
        if s.qualified in seen_q:
            continue
        seen_q.add(s.qualified)
        kept.append(s)
    pf.symbols = kept
    seen_refs: set[tuple[str, str, str]] = set()
    unique: list[Reference] = []
    for r in pf.references:
        ref_key = (r.from_qualified, r.kind, r.name)
        if ref_key not in seen_refs:
            seen_refs.add(ref_key)
            unique.append(r)
    pf.references = unique
    return pf

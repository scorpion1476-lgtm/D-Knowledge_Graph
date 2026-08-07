"""Perl XS extraction at explicitly lower, documented fidelity.

An .xs file is not Perl and not plain C. It is C source with an extra sectioning
layer the ``xsubpp`` preprocessor consumes: ``MODULE``/``PACKAGE``/``PREFIX``
lines that say which Perl package the following functions are bound into, and
XSUB definitions whose header is written across two lines with a distinctive
shape that never appears in C.

No Tree-sitter grammar for it exists in any permissive source available to this
project, including the multi-grammar bundle, none of whose grammars claims the
.xs extension. That has been true throughout and is still true. What changed is
the conclusion drawn from it.

The previous position was to parse nothing and report the extension unsupported.
The reasoning was that handing the file to the C grammar would misattribute the
macro layer and invent symbols, which is correct. But "the C grammar would get
it wrong" is an argument against using the C grammar, not an argument against
extracting anything, and the file's own sectioning is the most regular part of
it. A user pointing the tool at a Perl extension distribution got nothing back
for its most important file.

So this is a pattern extractor, in the same spirit as
``src/dkg/code/fallback.py`` and labelled the same way: ``fallback`` fidelity,
every edge leaving such a file scaled by ``FALLBACK_CONFIDENCE_FACTOR``, and
never reported as though the file had been parsed.

WHAT IT EXTRACTS

* ``MODULE = Foo::Bar  PACKAGE = Foo::Baz  PREFIX = fb_`` becomes a ``class``
  symbol for the package. PACKAGE defaults to MODULE when it is not written,
  which is what xsubpp does.
* An XSUB, whose header is a return type alone on one line followed by
  ``name(args)`` starting in column one, becomes a ``method`` of the package
  currently in force.
* The name recorded is the name **Perl sees**. Under ``PREFIX = fb_`` the XSUB
  ``fb_open`` is registered as ``Foo::Baz::open``, so the symbol is ``open``.
  The line as written is kept in the symbol's signature, so the C-level name is
  not lost.
* A C function defined before the first ``MODULE`` line, which is ordinary
  helper C, becomes a ``function``.
* ``#include "foo.h"`` becomes an ``imports`` reference.
* Calls inside an XSUB body become ``calls`` references from that XSUB.

WHAT IT CANNOT DO, stated rather than discovered

* It does not expand the macro layer. ``dXSARGS``, ``ST(0)``, ``XPUSHs`` and
  friends are read as ordinary text, so a symbol produced entirely by a macro is
  invisible to it.
* It does not evaluate the C preprocessor. A definition inside ``#if 0`` is
  extracted anyway, and one produced by a macro expansion is missed.
* An XSUB header split unusually, or a return type carrying attributes across
  several lines, is missed rather than guessed at.
* It has no type model, so a call is matched by name only, exactly as elsewhere
  on the fallback path.

Those limits are why the fidelity label exists, and they are measured against a
labelled corpus rather than asserted: see ``tests/code/corpus/langs/xs`` and the
per-language table in ``docs/BENCHMARKS.md``.
"""

from __future__ import annotations

import re
from pathlib import Path

from .model import FIDELITY_FALLBACK, ParsedFile, Reference, Symbol

LANGUAGE = "xs"
EXTENSIONS = (".xs",)

#: Why this language is on the pattern path rather than the grammar path.
REASON = (
    "Perl XS is C plus the xsubpp preprocessor's sectioning and macro layer, so "
    "it is neither Perl nor plain C. No permissive Tree-sitter grammar for it "
    "exists in any source available to this project, including the "
    "multi-grammar bundle, which carries none for the .xs extension. It is "
    "therefore read by a documented pattern extractor at fallback fidelity "
    "rather than parsed, and never reported as though it had been parsed."
)

_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"

# MODULE = Name::Space  PACKAGE = Other::Space  PREFIX = pfx_
# PACKAGE and PREFIX are both optional and may appear in either order.
_MODULE_LINE = re.compile(
    rf"^\s*MODULE\s*=\s*(?P<module>{_IDENT}(?:::{_IDENT})*)"
    rf"(?:\s+PACKAGE\s*=\s*(?P<package>{_IDENT}(?:::{_IDENT})*))?"
    rf"(?:\s+PREFIX\s*=\s*(?P<prefix>\S+))?\s*$"
)

# An XSUB return type sits alone on its own line: an optional const, a type
# name, optional pointer stars, and nothing else. `void`, `int`, `SV *`, and
# `Foo::Bar *` are all real XS return types.
_RETURN_TYPE = re.compile(
    r"^(?:const\s+)?(?:unsigned\s+|signed\s+|struct\s+|static\s+)*"
    rf"(?P<type>{_IDENT}(?:::{_IDENT})*)\s*(?P<stars>\**)\s*$"
)

# The XSUB name and its argument list start in column one. That column-one rule
# is what separates an XSUB header from an ordinary indented C call.
_XSUB_NAME = re.compile(rf"^(?P<name>{_IDENT})\s*\((?P<args>[^)]*)\)\s*$")

# An ordinary C function definition on one line, used only above the first
# MODULE line. What separates a definition from a prototype is not the absence
# of a semicolon anywhere on the line, which a one-line body has plenty of, but
# what follows the closing parenthesis: a body opens a brace, a prototype ends.
# Getting that wrong the first way round lost every one-line helper in the
# corpus, so the tail is captured and judged rather than pattern-guessed.
_C_FUNCTION = re.compile(
    rf"^\s*(?:static\s+|inline\s+|extern\s+)*(?:const\s+)?{_IDENT}[\s*]+"
    rf"(?P<name>{_IDENT})\s*\((?P<args>[^)]*)\)(?P<tail>.*)$"
)

# The same definition written in the two-line style XS files favour, with the
# return type alone on its own line and the name in column one. This is the
# identical shape an XSUB header uses, which is precisely why the file's
# position relative to the first MODULE line is what tells them apart.
_C_FUNCTION_NAME = re.compile(rf"^(?P<name>{_IDENT})\s*\((?P<args>[^;]*)\)\s*$")

_INCLUDE = re.compile(r'^\s*#\s*include\s*[<"](?P<name>[^>"]+)[>"]')

_CALL = re.compile(rf"(?P<name>{_IDENT})\s*\(")

# Keywords and XS section markers that look like a call or a definition but are
# neither. Without this, `if (...)` is a call to a function named "if", and the
# CODE: and OUTPUT: markers become symbols.
_NOT_CALLS = {
    "if", "for", "while", "switch", "return", "sizeof", "defined", "else",
    "do", "case", "break", "continue", "goto", "typedef", "struct", "union",
    "enum", "const", "static", "extern", "inline", "void", "int", "char",
    "long", "short", "float", "double", "unsigned", "signed",
}

#: XS section markers. A line that is one of these ends the XSUB header and
#: begins its body; none of them is ever a symbol.
_SECTION_MARKERS = {
    "CODE", "PPCODE", "OUTPUT", "INIT", "CLEANUP", "PREINIT", "POSTCALL",
    "INPUT", "BOOT", "PROTOTYPE", "PROTOTYPES", "ALIAS", "INTERFACE",
    "INTERFACE_MACRO", "SCOPE", "C_ARGS", "OVERLOAD", "FALLBACK", "REQUIRE",
    "VERSIONCHECK", "EXPORT_XSUB_SYMBOLS", "INCLUDE", "INCLUDE_COMMAND",
    "CASE", "ATTRS", "PACKAGE", "MODULE",
}

_SECTION_LINE = re.compile(r"^\s*(?P<marker>[A-Z_]+)\s*:")


def _strip_comment(line: str) -> str:
    """Remove a trailing C line comment, not cutting inside a string literal."""
    in_string = False
    escaped = False
    for i, ch in enumerate(line):
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
        elif ch == '"':
            in_string = not in_string
        elif not in_string and line.startswith("//", i):
            return line[:i]
    return line


def _perl_visible(name: str, prefix: str | None) -> str:
    """The name Perl sees, which is the XSUB name with PREFIX removed.

    Under ``PREFIX = fb_`` the XSUB ``fb_open`` is registered as ``open``. The
    prefix is stripped only when it is actually there; xsubpp does not require
    every XSUB in a section to carry it.
    """
    if prefix and name.startswith(prefix) and len(name) > len(prefix):
        return name[len(prefix) :]
    return name


def parse_xs(path: str | Path, text: str | None = None) -> ParsedFile:
    """Extract symbols and references from one .xs file without a grammar."""
    path = str(path)
    if text is None:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    pf = ParsedFile(path=path, language=LANGUAGE, fidelity=FIDELITY_FALLBACK)
    module_q = path
    pf.symbols.append(
        Symbol("module", Path(path).name, module_q, 1, max(len(lines), 1), "", None)
    )
    emitted: set[str] = {module_q}

    package_q: str | None = None
    prefix: str | None = None
    current_xsub: str | None = None
    # Everything before the first MODULE line is ordinary C, where a function is
    # a helper rather than an XSUB. After it, a bare C-shaped line is part of an
    # XSUB body and must not be read as a definition.
    in_xs_section = False
    in_block_comment = False

    def add(kind: str, name: str, lineno: int, parent: str | None) -> str:
        parent_q = parent or module_q
        qualified = (
            f"{parent_q}.{name}" if parent_q != module_q else f"{path}::{name}"
        )
        pf.symbols.append(
            Symbol(kind, name, qualified, lineno, lineno, lines[lineno - 1].strip(), parent_q)
        )
        emitted.add(qualified)
        return qualified

    index = 0
    while index < len(lines):
        lineno = index + 1
        raw = lines[index]
        index += 1

        # Block comments are stripped first: an XSUB-shaped line inside one is
        # not a definition, and a `/*` opened here can run for many lines.
        line = raw
        if in_block_comment:
            if "*/" in line:
                line = line.split("*/", 1)[1]
                in_block_comment = False
            else:
                continue
        while "/*" in line:
            head, _, tail = line.partition("/*")
            if "*/" in tail:
                line = head + tail.split("*/", 1)[1]
            else:
                line = head
                in_block_comment = True
                break
        line = _strip_comment(line)
        if not line.strip():
            continue

        module_match = _MODULE_LINE.match(line)
        if module_match:
            in_xs_section = True
            current_xsub = None
            package = module_match.group("package") or module_match.group("module")
            prefix = module_match.group("prefix")
            package_q = add("class", package, lineno, module_q)
            continue

        include = _INCLUDE.match(line)
        if include:
            target = include.group("name")
            pf.references.append(
                Reference(module_q, "imports", Path(target).stem)
            )
            continue

        section = _SECTION_LINE.match(line)
        if section and section.group("marker") in _SECTION_MARKERS:
            # A section marker ends the header and opens a body. The XSUB stays
            # in scope so its body's calls are attributed to it.
            continue

        if in_xs_section:
            # An XSUB header: a return type alone, then name(args) in column one.
            return_type = _RETURN_TYPE.match(line)
            if return_type and return_type.group("type") not in _SECTION_MARKERS:
                lookahead = index
                while lookahead < len(lines) and not lines[lookahead].strip():
                    lookahead += 1
                if lookahead < len(lines):
                    name_match = _XSUB_NAME.match(_strip_comment(lines[lookahead]))
                    if name_match:
                        written = name_match.group("name")
                        visible = _perl_visible(written, prefix)
                        parent = package_q if package_q in emitted else module_q
                        current_xsub = add(
                            "method", visible, lookahead + 1, parent
                        )
                        index = lookahead + 1
                        continue
            # Anything else inside an XS section is body text. Its calls belong
            # to the XSUB in force.
            for match in _CALL.finditer(line):
                callee = match.group("name")
                if callee in _NOT_CALLS or callee in _SECTION_MARKERS:
                    continue
                pf.references.append(
                    Reference(current_xsub or package_q or module_q, "calls", callee)
                )
            continue

        # Above the first MODULE line: ordinary C. The two-line form is tried
        # first, because `static int` alone on a line also matches nothing else.
        return_type = _RETURN_TYPE.match(line)
        if return_type and return_type.group("type") not in _SECTION_MARKERS:
            lookahead = index
            while lookahead < len(lines) and not lines[lookahead].strip():
                lookahead += 1
            if lookahead < len(lines):
                name_match = _C_FUNCTION_NAME.match(_strip_comment(lines[lookahead]))
                if name_match and name_match.group("name") not in _NOT_CALLS:
                    current_xsub = add(
                        "function", name_match.group("name"), lookahead + 1, module_q
                    )
                    index = lookahead + 1
                    continue

        c_function = _C_FUNCTION.match(line)
        if c_function and c_function.group("name") not in _NOT_CALLS:
            tail = c_function.group("tail").strip()
            # A definition opens a body, here or on the next non-blank line. A
            # prototype ends at the semicolon and defines nothing.
            if not tail.startswith(";"):
                opens_here = "{" in tail
                if not opens_here and not tail:
                    lookahead = index
                    while lookahead < len(lines) and not lines[lookahead].strip():
                        lookahead += 1
                    opens_here = (
                        lookahead < len(lines) and lines[lookahead].lstrip().startswith("{")
                    )
                if opens_here:
                    current_xsub = add(
                        "function", c_function.group("name"), lineno, module_q
                    )
                    continue
        for match in _CALL.finditer(line):
            callee = match.group("name")
            if callee in _NOT_CALLS:
                continue
            pf.references.append(Reference(current_xsub or module_q, "calls", callee))

    # The same guarantees the grammar path gives: one node per qualified name,
    # one reference per (source, kind, target).
    seen: set[str] = set()
    kept: list[Symbol] = []
    for symbol in pf.symbols:
        if symbol.qualified in seen:
            continue
        seen.add(symbol.qualified)
        kept.append(symbol)
    pf.symbols = kept

    seen_refs: set[tuple[str, str, str]] = set()
    unique: list[Reference] = []
    for reference in pf.references:
        key = (reference.from_qualified, reference.kind, reference.name)
        if key not in seen_refs:
            seen_refs.add(key)
            unique.append(reference)
    pf.references = unique
    return pf

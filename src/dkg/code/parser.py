"""Tree-sitter based source-code parsing. In-process, no network.

Pluggable per language. Extracts module, class, function, method, type, and test
symbols plus intra-file references (calls, imports, inherits) that the graph
layer resolves into edges. Adding a language means registering an extractor here.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..core.errors import IngestError, UnsupportedFormatError
from .capability import get_language, grammar_available, tree_sitter_available
from .fallback import FALLBACK_SPECS, parse_fallback
from .languages import (
    _NAME_NODE_TYPES,
    BUILTIN_SPECS,
    LanguageRegistry,
    LanguageSpec,
    active_registry,
    builtin_spec,
    load_grammar_language,
)
from .model import ParsedFile, Reference, Symbol

# Extensions handled by a bespoke extractor in this module rather than by a
# config-driven LanguageSpec. Everything else is derived from BUILTIN_SPECS
# below, so a language and its extensions are declared in exactly one place.
_BESPOKE_EXT_LANG = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".go": "go",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    # Perl XS has no permissive grammar anywhere, so it is read by a documented
    # pattern extractor at fallback fidelity. See src/dkg/code/xs.py.
    ".xs": "xs",
}


# Formats that are not a single grammar over a whole file: a notebook is JSON
# holding cells, a single-file component wraps a script block in markup, and the
# infrastructure formats have their own extractors. They resolve to their own
# language name here and are dispatched before the grammar path.
_COMPOSITE_EXT_LANG = {
    ".ipynb": "jupyter",
    ".vue": "vue",
    ".svelte": "svelte",
    ".astro": "astro",
    ".tf": "hcl",
    ".tfvars": "hcl",
    ".hcl": "hcl",
    ".nomad": "hcl",
    ".yml": "ansible",
    ".yaml": "ansible",
}


def _build_ext_lang() -> dict[str, str]:
    table = dict(_BESPOKE_EXT_LANG)
    table.update(_COMPOSITE_EXT_LANG)
    for name, builtin in BUILTIN_SPECS.items():
        for ext in builtin.extensions:
            # A bespoke extractor always wins: TypeScript is parsed by the
            # JavaScript extractor even though a spec could describe it.
            table.setdefault(ext, name)
    for name, pattern_spec in FALLBACK_SPECS.items():
        for ext in pattern_spec.extensions:
            table.setdefault(ext, name)
    return table


EXT_LANG = _build_ext_lang()

# Extensions that mean "this import target is a file path, not a dotted module
# name", so the extension is dropped rather than treated as the last segment.
_SOURCE_EXTENSIONS = set(EXT_LANG) | {".hpp", ".hh", ".inc", ".tpl", ".md"}

_MAX_BYTES = 4 * 1024 * 1024


# Languages reached by content detection rather than by extension, so they do
# not appear in EXT_LANG but are claimed and measured all the same.
_CONTENT_DETECTED = ("databricks",)

# How a language is parsed, reported honestly wherever the inventory surfaces.
# 'grammar' means a real Tree-sitter parse; 'fallback' means the documented
# lower-fidelity pattern extractor; 'composite' means the file is unwrapped and
# then parsed by another language's grammar.
_COMPOSITE_LANGUAGES = {
    "jupyter": "notebook code cells, parsed with the kernel language grammar",
    "databricks": "notebook code cells, parsed with the notebook language grammar",
    "vue": "script blocks, parsed with the JavaScript or TypeScript grammar",
    "svelte": "script blocks, parsed with the JavaScript or TypeScript grammar",
    "astro": "frontmatter script, parsed with the TypeScript grammar",
    "hcl": "HCL grammar, with blocks mapped to Terraform addresses",
    "ansible": "YAML grammar, with plays and tasks mapped to symbols",
}


# Formats this build deliberately does NOT parse, each with the reason. Kept in
# the product rather than only in the requirements matrix, because a user
# pointing the tool at one of these deserves to be told why nothing came back
# rather than to conclude the file was empty. A format listed here is never
# counted in the language claim.
NOT_PARSED: dict[str, str] = {}


def not_parsed_reason(path: str | Path) -> str:
    """Why this build does not parse ``path``, or an empty string if it does."""
    return NOT_PARSED.get(Path(str(path)).suffix.lower(), "")


def claimed_languages() -> list[str]:
    """Every language this build claims to parse, whatever the mechanism.

    This is the set a language claim is checked against: a language may not be
    listed as supported anywhere without a labelled corpus and a measurement
    behind it, and this function is what makes that checkable.
    """
    return sorted(set(EXT_LANG.values()) | set(_CONTENT_DETECTED))


def language_inventory() -> dict[str, dict]:
    """Per language: how it is parsed, which extra ships it, and whether it is
    available in this environment. Read-only and cheap; no file is parsed."""
    from .capability import (
        BUNDLE_EXTRA,
        BUNDLE_GRAMMAR_SOURCES,
        BUNDLE_MODULE,
        GRAMMAR_EXTRAS,
        GRAMMAR_LICENCES,
        GRAMMARS,
        grammar_available,
    )

    # Which grammar each composite format actually needs to do its work.
    composite_grammar = {
        "jupyter": "python",
        "databricks": "python",
        "vue": "typescript",
        "svelte": "typescript",
        "astro": "typescript",
        "ansible": "yaml",
    }
    out: dict[str, dict] = {}
    for language in claimed_languages():
        extensions = sorted(ext for ext, lang in EXT_LANG.items() if lang == language)
        if language == "xs":
            # Unconditionally the pattern extractor. Unlike the five below there
            # is no extra that upgrades it, because no permissive grammar for
            # .xs exists anywhere to install, so no "upgrade" key is offered.
            from .xs import REASON as XS_REASON

            out[language] = {
                "fidelity": "fallback",
                "how": "documented pattern extractor",
                "reason": XS_REASON,
                "extensions": extensions,
                "extra": None,
                "available": True,
                "licence": "not applicable",
            }
            continue
        if language in FALLBACK_SPECS:
            # These five now have a real grammar, served by the multi-grammar
            # bundle. Which one actually ran depends on whether the optional
            # extra is installed, so the inventory reports the mechanism in
            # force here rather than a fixed label: claiming grammar fidelity on
            # a build that will fall back would be the dishonest half of this.
            spec = FALLBACK_SPECS[language]
            if grammar_available(language):
                licence, repo, rev = BUNDLE_GRAMMAR_SOURCES[language]
                out[language] = {
                    "fidelity": "grammar",
                    "how": f"{BUNDLE_MODULE} bundled grammar",
                    "extensions": extensions,
                    "extra": BUNDLE_EXTRA,
                    "available": True,
                    "licence": licence,
                    "grammar_source": {"repository": repo, "revision": rev},
                }
            else:
                out[language] = {
                    "fidelity": "fallback",
                    "how": "documented pattern extractor",
                    "reason": spec.reason,
                    "extensions": extensions,
                    "extra": BUNDLE_EXTRA,
                    "available": True,
                    "licence": "not applicable",
                    "upgrade": (
                        f"install the {BUNDLE_EXTRA!r} extra for a real grammar parse "
                        f"of {language}"
                    ),
                }
            continue
        needed = composite_grammar.get(language, language)
        entry = {
            "fidelity": "composite" if language in _COMPOSITE_LANGUAGES else "grammar",
            "how": _COMPOSITE_LANGUAGES.get(language, f"{GRAMMARS.get(needed, 'unknown')} grammar"),
            "extensions": extensions,
            "extra": GRAMMAR_EXTRAS.get(needed),
            "available": grammar_available(needed),
            "licence": GRAMMAR_LICENCES.get(needed, "MIT"),
        }
        if language in _CONTENT_DETECTED:
            entry["detected_by"] = "file content marker, not extension"
        out[language] = entry
    return out


# Interpreter name to language, for an executable script with no extension. The
# name is matched after stripping any version suffix, so python3.12 and python
# both resolve, and after unwrapping /usr/bin/env.
_INTERPRETER_LANGUAGE = {
    "sh": "bash",
    "bash": "bash",
    "ksh": "bash",
    "dash": "bash",
    "zsh": "zsh",
    "python": "python",
    "node": "javascript",
    "nodejs": "javascript",
    "deno": "typescript",
    "bun": "javascript",
    "ruby": "ruby",
    "perl": "perl",
    "lua": "lua",
    "luau": "luau",
    "rscript": "r",
    "r": "r",
    "php": "php",
    "julia": "julia",
    "pwsh": "powershell",
    "powershell": "powershell",
    "elixir": "elixir",
    "scala": "scala",
}
_VERSION_SUFFIX = re.compile(r"[0-9.]+$")
# A shebang line is the first line of the file, so only that much is read.
_SHEBANG_READ_BYTES = 256


def language_from_shebang(first_line: str) -> str | None:
    """The language an interpreter line names, or None if it names none.

    ``#!/usr/bin/env python3`` and ``#!/bin/bash`` both resolve; a first line
    that is not a shebang resolves to nothing rather than being guessed at.
    """
    line = (first_line or "").strip()
    if not line.startswith("#!"):
        return None
    parts = line[2:].strip().split()
    if not parts:
        return None
    command = parts[0].rsplit("/", 1)[-1].lower()
    if command == "env":
        # `env -S python -u` and `env python3` both name the interpreter after
        # any option arguments.
        for token in parts[1:]:
            if token.startswith("-"):
                continue
            command = token.rsplit("/", 1)[-1].lower()
            break
        else:
            return None
    command = _VERSION_SUFFIX.sub("", command) or command
    return _INTERPRETER_LANGUAGE.get(command)


def _shebang_language(path: str | Path, text: str | None) -> str | None:
    """Read only as much of the file as a shebang line can occupy."""
    if text is not None:
        first = text.splitlines()[0] if text else ""
    else:
        try:
            with open(path, "rb") as fh:
                first = fh.read(_SHEBANG_READ_BYTES).decode("utf-8", "replace").splitlines()[0]
        except (OSError, IndexError):
            return None
    return language_from_shebang(first)


def language_for(
    path: str | Path,
    registry: LanguageRegistry | None = None,
    *,
    text: str | None = None,
) -> str | None:
    """The language for a path, by extension first and then by interpreter line.

    An extension always wins: a file named ``deploy.py`` is Python whatever its
    first line says. A file with no known extension, which is how an executable
    script is normally written, falls back to its shebang.
    """
    ext = Path(path).suffix.lower()
    builtin = EXT_LANG.get(ext)
    if builtin is not None:
        return builtin
    registry = registry if registry is not None else active_registry()
    registered = registry.language_for_ext(ext)
    if registered is not None:
        return registered
    if ext and ext not in ("", "."):
        # A file with an extension nobody claims is not a script to sniff; only
        # an extension-less file gets the interpreter-line treatment.
        return None
    return _shebang_language(path, text)


def _text(node: Any, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", "replace")


def _last_name(dotted: str) -> str:
    # "os.path.join" -> "join"; "self.foo" -> "foo"; "A().m" -> "m"; "foo" -> "foo"
    tail = dotted.strip().split(".")[-1]
    return tail.split("(")[0].strip()


def is_parsable(path: str | Path, text: str | None = None) -> bool:
    """True when this file has a parser that will actually produce symbols.

    Extension alone is not enough for the content-detected formats: most YAML in
    a repository is configuration rather than Ansible, and a Databricks notebook
    is an ordinary source extension carrying a marker comment. Ingestion asks
    this before parsing so a plain configuration file is passed over quietly
    instead of being reported as a parse failure.
    """
    from . import iac

    path = str(path)
    lang = language_for(path, text=text)
    if lang is None:
        return False
    if lang == "ansible":
        return iac.looks_like_ansible(path, text)
    return True


def _parse_composite(path: str, lang: str, text: str | None, registry: LanguageRegistry) -> ParsedFile | None:
    """Parse the formats that are not one grammar over a whole file.

    Returns None when the path is not one of those formats, so the caller falls
    through to the ordinary grammar path.
    """
    from . import frameworks, iac, notebooks, sfc

    if frameworks.is_blade_template(path):
        body = text if text is not None else Path(path).read_text(encoding="utf-8", errors="replace")
        return frameworks.enrich_blade_file(path, body)
    if lang == "hcl":
        return iac.parse_hcl(path, text)
    if lang == "ansible":
        return iac.parse_ansible(path, text)
    if lang == "jupyter" or notebooks.is_notebook(path):
        book = notebooks.read_jupyter(path, text)
        return _parse_embedded(path, book.language, book.source, registry, empty_language="jupyter")
    if lang in sfc.SFC_EXTENSIONS.values():
        component = sfc.read_component(path, text)
        return _parse_embedded(path, component.language, component.source, registry, empty_language=lang)
    # Checked before the fallback path because a Databricks notebook uses an
    # ordinary source extension and is only told apart by its marker comment,
    # and one of those extensions (.r) is also a fallback language.
    if notebooks.is_databricks_notebook(path, text):
        book = notebooks.read_databricks(path, text)
        return _parse_embedded(path, book.language, book.source, registry, empty_language="databricks")
    if lang == "xs":
        # Always the pattern extractor: there is no grammar to prefer, on any
        # machine and with any extra installed.
        from .xs import parse_xs

        return parse_xs(path, text)
    if lang in FALLBACK_SPECS and not grammar_available(lang):
        # The documented pattern extractor is the DEGRADED path now, not the
        # only one. When the grammar bundle is installed these five parse with a
        # real grammar and fall through to the generic extractor below; without
        # it they still work, at the lower fidelity the inventory reports.
        return parse_fallback(path, text, spec=FALLBACK_SPECS[lang])
    return None


def _parse_embedded(
    path: str,
    language: str,
    source: str,
    registry: LanguageRegistry,
    *,
    empty_language: str,
) -> ParsedFile:
    """Parse code lifted out of a notebook or a component.

    Line numbers are against the lifted code, not the containing file, which is
    stated wherever these symbols surface rather than left to be discovered.
    """
    if not source.strip():
        return ParsedFile(
            path=path,
            language=empty_language,
            symbols=[Symbol("module", Path(path).name, path, 1, 1, "", None)],
        )
    return parse_source(path, source, language=language, registry=registry)


def parse_source(
    path: str | Path,
    text: str | None = None,
    *,
    language: str | None = None,
    registry: LanguageRegistry | None = None,
) -> ParsedFile:
    path = str(path)
    registry = registry if registry is not None else active_registry()
    lang = language or language_for(path, registry, text=text)
    if lang is None:
        # Name the reason when there is a considered one, rather than reporting
        # the same generic failure a typo would produce.
        reason = not_parsed_reason(path)
        raise UnsupportedFormatError(
            f"no code parser for {path}: {reason}" if reason else f"no code parser for {path}"
        )
    if language is None or lang in FALLBACK_SPECS:
        composite = _parse_composite(path, lang, text, registry)
        if composite is not None:
            return composite
    # A user-registered language wins over a built-in spec of the same name, so
    # a project can override how a shipped language is extracted without a fork.
    spec = registry.get(lang) or builtin_spec(lang)
    is_builtin_spec = registry.get(lang) is None and spec is not None
    if lang not in _EXTRACTORS and spec is None:
        raise UnsupportedFormatError(f"language {lang!r} is not supported")
    if not tree_sitter_available():
        raise UnsupportedFormatError(
            "code parsing requires the 'code' extra: pip install d-knowledge-graph[code]"
        )
    if text is not None:
        src = text.encode("utf-8")
    else:
        raw = Path(path).read_bytes()
        if len(raw) > _MAX_BYTES:
            raise IngestError(f"source file too large: {len(raw)} bytes")
        src = raw
    import tree_sitter

    if lang in _EXTRACTORS:
        ts_lang = get_language(lang)
        parser = tree_sitter.Parser(ts_lang)
        tree = parser.parse(src)
        pf = _EXTRACTORS[lang](path, lang, tree.root_node, src)
    else:
        assert spec is not None
        # A built-in spec resolves its grammar through the capability layer,
        # which knows the per-grammar accessor and names the right extra when
        # the grammar is absent. A user-registered spec loads its own module.
        ts_lang = get_language(lang) if is_builtin_spec else load_grammar_language(spec.grammar_module)
        parser = tree_sitter.Parser(ts_lang)
        tree = parser.parse(src)
        pf = _extract_generic(path, spec, tree.root_node, src)
    # Drop anonymous symbols so they never count as spurious entities, then
    # keep only the first symbol under each qualified name. Some grammars name
    # the same definition twice (a C struct is declared once and named again by
    # a typedef), and two nodes under one qualified name would be two graph
    # entities for one definition.
    seen_q: set[str] = set()
    kept: list[Symbol] = []
    for s in pf.symbols:
        if s.name == "<anon>" or s.qualified in seen_q:
            continue
        seen_q.add(s.qualified)
        kept.append(s)
    pf.symbols = kept
    # Dedup references (some grammars emit overlapping nodes for the same
    # reference); harmless downstream, but keeps persisted metadata compact.
    seen: set[tuple[str, str, str]] = set()
    unique: list[Reference] = []
    for r in pf.references:
        key = (r.from_qualified, r.kind, r.name)
        if key not in seen:
            seen.add(key)
            unique.append(r)
    pf.references = unique
    if lang == "php":
        # Framework-aware edges sit alongside the structural parse: routes, the
        # actions they dispatch to, model relationships, and Blade views. A
        # project that uses no framework simply matches none of them.
        from . import frameworks

        frameworks.enrich_php_file(path, src.decode("utf-8", "replace"), pf)
    # Synthesised entry points: a routed endpoint or a scheduled invocation gets
    # a node of its own, so an execution flow can start at a real entry point
    # rather than at a symbol that happens to be called main. Detection is
    # pattern-based and a lower bound; a file matching nothing costs one scan.
    from . import entrypoints

    entrypoints.enrich(pf, src.decode("utf-8", "replace"))
    return pf


# -- Python -----------------------------------------------------------------


def _extract_python(path: str, lang: str, root: Any, src: bytes) -> ParsedFile:
    pf = ParsedFile(path=path, language=lang)
    module_q = path
    is_test_file = "test" in Path(path).stem.lower()
    pf.symbols.append(Symbol("module", Path(path).name, module_q, 1, root.end_point[0] + 1, "", None))

    def walk(node: Any, class_ctx: str | None, func_ctx: str | None) -> None:
        for child in node.children:
            t = child.type
            if t == "class_definition":
                name_n = child.child_by_field_name("name")
                name = _text(name_n, src) if name_n else "<anon>"
                # Qualified under its container, so two classes that each declare
                # an inner class of the same name stay two definitions.
                q = f"{class_ctx}.{name}" if class_ctx else f"{path}::{name}"
                pf.symbols.append(
                    Symbol("class", name, q, child.start_point[0] + 1, child.end_point[0] + 1, _text(child, src), class_ctx or module_q)
                )
                supers = child.child_by_field_name("superclasses")
                if supers is not None:
                    for sc in supers.children:
                        if sc.type in ("identifier", "attribute"):
                            pf.references.append(Reference(q, "inherits", _last_name(_text(sc, src))))
                walk(child, q, None)
            elif t == "function_definition":
                name_n = child.child_by_field_name("name")
                name = _text(name_n, src) if name_n else "<anon>"
                if class_ctx:
                    kind, q, parent = "method", f"{class_ctx}.{name}", class_ctx
                elif func_ctx:
                    # A nested function belongs to the function that defines it,
                    # so two functions can each declare a helper of one name.
                    kind, q, parent = "function", f"{func_ctx}.{name}", func_ctx
                else:
                    kind, q, parent = "function", f"{path}::{name}", module_q
                if name.startswith("test") or is_test_file:
                    kind = "test"
                pf.symbols.append(
                    Symbol(kind, name, q, child.start_point[0] + 1, child.end_point[0] + 1, _text(child, src), parent)
                )
                walk(child, class_ctx, q)
            elif t == "expression_statement" and (binding := _python_lambda_binding(child, src)) is not None:
                # `normalise = lambda s: s.strip()` defines a function under a
                # name, the same construct JavaScript and Go bind with an arrow
                # function and a function literal, both of which are extracted.
                name, value = binding
                kind = "test" if (name.startswith("test") or is_test_file) else ("method" if class_ctx else "function")
                q = f"{class_ctx}.{name}" if class_ctx else f"{path}::{name}"
                pf.symbols.append(
                    Symbol(kind, name, q, child.start_point[0] + 1, child.end_point[0] + 1, _text(child, src), class_ctx or module_q)
                )
                walk(value, class_ctx, q)
            elif t in ("import_statement", "import_from_statement"):
                for nm in _names_in(child, src, ("dotted_name", "identifier", "aliased_import")):
                    pf.references.append(Reference(module_q, "imports", _last_name(nm)))
            elif t == "call":
                fn = child.child_by_field_name("function")
                if fn is not None:
                    pf.references.append(Reference(func_ctx or module_q, "calls", _last_name(_text(fn, src))))
                walk(child, class_ctx, func_ctx)
            else:
                walk(child, class_ctx, func_ctx)

    walk(root, None, None)
    return pf


def _python_lambda_binding(node: Any, src: bytes) -> tuple[str, Any] | None:
    """(name, lambda node) when this statement binds a lambda to a plain name."""
    assignment = next((c for c in node.children if c.type == "assignment"), None)
    if assignment is None:
        return None
    left = assignment.child_by_field_name("left")
    right = assignment.child_by_field_name("right")
    if left is None or right is None or left.type != "identifier" or right.type != "lambda":
        return None
    return _text(left, src), right


# -- JavaScript -------------------------------------------------------------


def _extract_javascript(path: str, lang: str, root: Any, src: bytes) -> ParsedFile:
    pf = ParsedFile(path=path, language=lang)
    module_q = path
    is_test_file = any(m in Path(path).stem.lower() for m in ("test", "spec"))
    pf.symbols.append(Symbol("module", Path(path).name, module_q, 1, root.end_point[0] + 1, "", None))

    def walk(node: Any, class_ctx: str | None, func_ctx: str | None) -> None:
        for child in node.children:
            t = child.type
            if t in ("class_declaration", "abstract_class_declaration"):
                name_n = child.child_by_field_name("name")
                name = _text(name_n, src) if name_n else "<anon>"
                q = f"{class_ctx}.{name}" if class_ctx else f"{path}::{name}"
                pf.symbols.append(
                    Symbol("class", name, q, child.start_point[0] + 1, child.end_point[0] + 1, _text(child, src), class_ctx or module_q)
                )
                heritage = child.child_by_field_name("superclass")
                if heritage is None:
                    for c in child.children:
                        if c.type == "class_heritage":
                            heritage = c
                            break
                if heritage is not None:
                    # JavaScript puts the base class directly under class_heritage.
                    # TypeScript wraps it in an extends_clause (and can add an
                    # implements_clause alongside), so descend one level into
                    # those wrappers rather than missing the edge entirely.
                    candidates = []
                    for c in heritage.children:
                        if c.type in ("extends_clause", "implements_clause"):
                            candidates.extend(c.children)
                        else:
                            candidates.append(c)
                    for c in candidates:
                        if c.type in ("identifier", "member_expression", "type_identifier"):
                            pf.references.append(Reference(q, "inherits", _last_name(_text(c, src))))
                walk(child, q, None)
            elif t in ("function_declaration", "generator_function_declaration"):
                name_n = child.child_by_field_name("name")
                name = _text(name_n, src) if name_n else "<anon>"
                if name.startswith("test") or is_test_file:
                    kind = "test"
                else:
                    # A function declared inside a container is a member of it.
                    # In JavaScript that never happens; in TypeScript it is how
                    # a namespace declares what it exports.
                    kind = "method" if class_ctx else "function"
                q = f"{class_ctx}.{name}" if class_ctx else f"{path}::{name}"
                pf.symbols.append(
                    Symbol(kind, name, q, child.start_point[0] + 1, child.end_point[0] + 1, _text(child, src), class_ctx or module_q)
                )
                walk(child, class_ctx, q)
            elif t in ("method_definition", "method_signature", "abstract_method_signature"):
                # A signature without a body is a definition of that member: it
                # is what an abstract class or an interface writes, and it is
                # extracted for the same reason a Java interface method is.
                name_n = child.child_by_field_name("name")
                name = _text(name_n, src) if name_n else "<anon>"
                parent = class_ctx or module_q
                q = f"{class_ctx}.{name}" if class_ctx else f"{path}::{name}"
                pf.symbols.append(
                    Symbol("method", name, q, child.start_point[0] + 1, child.end_point[0] + 1, _text(child, src), parent)
                )
                walk(child, class_ctx, q)
            elif t in ("internal_module", "module"):
                # A TypeScript namespace holds definitions, so it becomes a
                # container symbol and what it declares becomes its members.
                name_n = child.child_by_field_name("name")
                name = _symbol_name(_text(name_n, src)) if name_n else "<anon>"
                q = f"{path}::{name}"
                pf.symbols.append(
                    Symbol("class", name, q, child.start_point[0] + 1, child.end_point[0] + 1, _text(child, src), module_q)
                )
                walk(child, q, None)
            elif t in ("lexical_declaration", "variable_declaration"):
                # `const add = (a, b) => a + b` and `var f = function () {}` are
                # how a large share of real JavaScript declares its functions.
                # A binding to anything else is an ordinary variable and is not
                # a symbol, so only function-valued bindings are extracted.
                for declarator in child.children:
                    if declarator.type != "variable_declarator":
                        continue
                    value = declarator.child_by_field_name("value")
                    if value is None or value.type not in ("arrow_function", "function_expression", "function", "generator_function"):
                        continue
                    name_n = declarator.child_by_field_name("name")
                    if name_n is None:
                        continue
                    name = _text(name_n, src)
                    kind = "test" if (name.startswith("test") or is_test_file) else ("method" if class_ctx else "function")
                    parent = class_ctx or module_q
                    q = f"{class_ctx}.{name}" if class_ctx else f"{path}::{name}"
                    pf.symbols.append(
                        Symbol(kind, name, q, declarator.start_point[0] + 1, declarator.end_point[0] + 1, _text(declarator, src), parent)
                    )
                    walk(value, class_ctx, q)
                walk(child, class_ctx, func_ctx)
            elif t == "pair":
                # `{ triple: function (v) { ... } }` declares a function under a
                # key, which is the object-literal spelling of a method.
                value = child.child_by_field_name("value")
                key_n = child.child_by_field_name("key")
                if (
                    value is not None
                    and key_n is not None
                    and value.type in ("arrow_function", "function_expression", "function", "generator_function")
                ):
                    name = _symbol_name(_text(key_n, src))
                    parent = class_ctx or module_q
                    q = f"{class_ctx}.{name}" if class_ctx else f"{path}::{name}"
                    pf.symbols.append(
                        Symbol("method", name, q, child.start_point[0] + 1, child.end_point[0] + 1, _text(child, src), parent)
                    )
                    walk(value, class_ctx, q)
                else:
                    walk(child, class_ctx, func_ctx)
            elif t == "function_signature":
                # `declare function ambient(x: string): void;` declares a
                # function that exists but is implemented elsewhere.
                name_n = child.child_by_field_name("name")
                name = _text(name_n, src) if name_n else "<anon>"
                q = f"{path}::{name}"
                pf.symbols.append(
                    Symbol("function", name, q, child.start_point[0] + 1, child.end_point[0] + 1, _text(child, src), module_q)
                )
            elif t in ("interface_declaration", "type_alias_declaration", "enum_declaration"):
                # TypeScript-only node types. The JavaScript grammar never emits
                # them, so this branch is inert for .js files and the shared
                # extractor stays one code path for both languages. The body is
                # walked, because a member declared without an implementation is
                # still a definition of that member: it is what a Java or a C#
                # interface writes, and those are extracted for the same reason.
                name_n = child.child_by_field_name("name")
                name = _text(name_n, src) if name_n else "<anon>"
                q = f"{path}::{name}"
                pf.symbols.append(
                    Symbol("type", name, q, child.start_point[0] + 1, child.end_point[0] + 1, _text(child, src), module_q)
                )
                walk(child, q, None)
            elif t == "import_statement":
                src_n = child.child_by_field_name("source")
                if src_n is not None:
                    pf.references.append(Reference(module_q, "imports", _text(src_n, src).strip("'\"")))
            elif t == "call_expression":
                fn = child.child_by_field_name("function")
                if fn is not None:
                    pf.references.append(Reference(func_ctx or module_q, "calls", _last_name(_text(fn, src))))
                walk(child, class_ctx, func_ctx)
            else:
                walk(child, class_ctx, func_ctx)

    walk(root, None, None)
    return pf


# -- Go ---------------------------------------------------------------------


def _extract_go(path: str, lang: str, root: Any, src: bytes) -> ParsedFile:
    pf = ParsedFile(path=path, language=lang)
    module_q = path
    is_test_file = Path(path).stem.lower().endswith("_test")
    pf.symbols.append(Symbol("module", Path(path).name, module_q, 1, root.end_point[0] + 1, "", None))

    def walk(node: Any, func_ctx: str | None) -> None:
        for child in node.children:
            t = child.type
            if t == "function_declaration":
                name_n = child.child_by_field_name("name")
                name = _text(name_n, src) if name_n else "<anon>"
                kind = "test" if (name.startswith("Test") or is_test_file) else "function"
                q = f"{path}::{name}"
                pf.symbols.append(
                    Symbol(kind, name, q, child.start_point[0] + 1, child.end_point[0] + 1, _text(child, src), module_q)
                )
                walk(child, q)
            elif t == "method_declaration":
                name_n = child.child_by_field_name("name")
                name = _text(name_n, src) if name_n else "<anon>"
                q = f"{path}::{name}"
                pf.symbols.append(
                    Symbol("method", name, q, child.start_point[0] + 1, child.end_point[0] + 1, _text(child, src), module_q)
                )
                walk(child, q)
            elif t == "type_declaration":
                for spec in child.children:
                    if spec.type == "type_spec":
                        name_n = spec.child_by_field_name("name")
                        name = _text(name_n, src) if name_n else "<anon>"
                        q = f"{path}::{name}"
                        pf.symbols.append(
                            Symbol("type", name, q, spec.start_point[0] + 1, spec.end_point[0] + 1, _text(spec, src), module_q)
                        )
            elif t in ("var_declaration", "const_declaration"):
                # `var Printer = func(s string) { ... }` is how Go declares a
                # function value; a binding to anything else is a variable.
                for spec in _descend(child, ("var_spec", "const_spec")):
                    value = spec.child_by_field_name("value")
                    if value is None or "func_literal" not in {c.type for c in value.children} | {value.type}:
                        continue
                    name_n = spec.child_by_field_name("name")
                    if name_n is None:
                        continue
                    name = _text(name_n, src)
                    kind = "test" if (name.startswith("Test") or is_test_file) else "function"
                    q = f"{path}::{name}"
                    pf.symbols.append(
                        Symbol(kind, name, q, spec.start_point[0] + 1, spec.end_point[0] + 1, _text(spec, src), module_q)
                    )
                    walk(value, q)
            elif t == "import_declaration":
                for nm in _names_in(child, src, ("interpreted_string_literal", "import_spec")):
                    pf.references.append(Reference(module_q, "imports", nm.strip('"').split("/")[-1]))
            elif t == "call_expression":
                fn = child.child_by_field_name("function")
                if fn is not None:
                    pf.references.append(Reference(func_ctx or module_q, "calls", _last_name(_text(fn, src))))
                walk(child, func_ctx)
            else:
                walk(child, func_ctx)

    walk(root, None)
    return pf


def _descend(node: Any, types: tuple[str, ...]) -> list[Any]:
    """Every descendant of one of `types`, in source order."""
    out: list[Any] = []
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type in types:
            out.append(n)
        stack.extend(reversed(n.children))
    return out


def _names_in(node: Any, src: bytes, types: tuple[str, ...]) -> list[str]:
    out: list[str] = []

    def rec(n: Any) -> None:
        if n.type in types:
            out.append(_text(n, src))
        for c in n.children:
            rec(c)

    rec(node)
    return out


# -- Generic, config-driven (custom languages) ------------------------------


_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_\-]*")

# Fields tried, in order, when a spec's declared name field is absent on a node.
# Grammars disagree about which field holds a definition's name (C puts it in a
# declarator, shells in a word), so one spec-level field is not enough.
_NAME_FIELD_FALLBACKS = ("name", "declarator", "function_name", "target", "pattern")

# The same problem for call nodes: a grammar may hold the callee in "function",
# "name", "method", or "command_name" depending on the call form.
_CALL_FIELD_FALLBACKS = ("function", "name", "method", "command_name")


def _first_name_text(
    node: Any,
    src: bytes,
    name_types: tuple[str, ...] = _NAME_NODE_TYPES,
    skip_types: tuple[str, ...] = (),
) -> str:
    """The first identifier-like descendant, in source order.

    The search is depth first and pre-order, which is source order, because the
    name a definition introduces is the first identifier it writes. A breadth
    first search returns whichever identifier happens to sit at the shallowest
    depth, which for Julia is a local variable in the body rather than the
    function name nested one level deeper inside the signature.
    """
    stack = [node]
    while stack:
        n = stack.pop()
        if n is not node and n.type in skip_types:
            continue
        if n.type in name_types:
            return _text(n, src)
        stack.extend(reversed(n.children))
    return _text(node, src)


def _symbol_name(text: str) -> str:
    """Reduce a declarator or qualified name to the identifier it defines.

    Grammars hand back anything from a bare identifier to ``*Shape.area(int a)``
    depending on the language. The identifier being defined is the last one
    before the parameter list, so the text is cut at the first opening bracket
    and the last identifier token in what remains is the name.
    """
    first_line = text.strip().splitlines()[0] if text.strip() else ""
    head = first_line.split("(")[0].split("{")[0].split("[")[0].split("<")[0]
    matches: list[str] = _IDENT_RE.findall(head)
    if matches:
        return matches[-1]
    # Nothing before the first bracket: a C function-pointer typedef writes the
    # name inside brackets, as in `int (*Comparator)(const void *)`. The first
    # identifier anywhere in the declarator is the name it introduces.
    matches = _IDENT_RE.findall(first_line)
    if matches:
        return matches[0]
    return text.strip() or "<anon>"


def _import_name(raw: str) -> str:
    """Reduce an import target to the module name it refers to.

    An import target is either a path (``<stdio.h>``, ``./lib.sh``) or a dotted
    module name (``kotlin.math.sqrt``). A path keeps its last segment with the
    file extension removed, because ``h`` is not the name of anything; a dotted
    name keeps its last segment, because that is the symbol brought into scope.
    """
    cleaned = raw.strip().strip("\"'<>`;() \t")
    for sep in ("/", "\\"):
        if sep in cleaned:
            tail = cleaned.rstrip(sep).split(sep)[-1]
            if tail:
                cleaned = tail
    if "." in cleaned:
        head, _, ext = cleaned.rpartition(".")
        if head and f".{ext.lower()}" in _SOURCE_EXTENSIONS:
            cleaned = head
        else:
            cleaned = cleaned.rstrip(".").split(".")[-1] or cleaned
    return cleaned or raw.strip()


def _extract_generic(path: str, spec: LanguageSpec, root: Any, src: bytes) -> ParsedFile:
    """Extract symbols and references for a config-driven language.

    Driven entirely by the LanguageSpec node-type mapping, this emits the same
    Symbol and Reference shapes the bespoke extractors do, so graph resolution,
    impact, and flow work unchanged for every language that goes through it,
    whether shipped with the project or registered by a user.
    """
    pf = ParsedFile(path=path, language=spec.name)
    module_q = path
    stem = Path(path).stem.lower()
    is_test_file = bool(spec.test_prefix) and (spec.test_prefix in stem or stem.endswith("_test"))
    name_types = spec.name_node_types or _NAME_NODE_TYPES
    import_keywords = {k.lower() for k in spec.import_keywords}
    pf.symbols.append(Symbol("module", Path(path).name, module_q, 1, root.end_point[0] + 1, "", None))

    def name_node(node: Any) -> Any | None:
        n = node.child_by_field_name(spec.name_field)
        if n is not None:
            return n
        for fallback in _NAME_FIELD_FALLBACKS:
            if fallback == spec.name_field:
                continue
            n = node.child_by_field_name(fallback)
            if n is not None:
                return n
        return None

    def sym_name(node: Any) -> str:
        fixed = spec.default_names.get(node.type)
        if fixed:
            return fixed
        n = name_node(node)
        if node.type in spec.raw_name_node_types:
            # Taken verbatim: the name IS the qualified thing being declared, so
            # reducing it to its last identifier would name something else.
            raw = _text(n, src) if n is not None else _text(node, src)
            return raw.strip().strip(";").strip() or "<anon>"
        if n is not None:
            return _symbol_name(_text(n, src))
        return _symbol_name(_first_name_text(node, src, name_types, spec.name_skip_types))

    def owner_of(node: Any) -> str:
        """The type a definition belongs to, when the name node names one."""
        if not spec.owner_field:
            return ""
        n = name_node(node)
        if n is None:
            return ""
        owner = n.child_by_field_name(spec.owner_field)
        return _symbol_name(_text(owner, src)) if owner is not None else ""

    def add_inherits(node: Any, q: str) -> None:
        if spec.inherits_field:
            fnode = node.child_by_field_name(spec.inherits_field)
            if fnode is not None:
                pf.references.append(Reference(q, "inherits", _last_name(_first_name_text(fnode, src, name_types))))
        for child in node.children:
            if child.type in spec.inherits_node_types:
                for base in _names_in(child, src, name_types):
                    pf.references.append(Reference(q, "inherits", _last_name(base)))

    emitted: set[str] = {module_q}
    # The most recent definition emitted anywhere in the walk. A grammar that
    # makes a body the sibling of the signature it implements (Dart) needs this
    # to attribute calls, and the signature is always the definition emitted
    # immediately before its own body.
    last_emitted: list[str] = [module_q]

    def add_symbol(node: Any, category: str, class_ctx: str | None, name: str | None = None) -> tuple[str, str | None]:
        """Emit one symbol and return (qualified name, new class context)."""
        sym = name if name is not None else sym_name(node)
        start, end = node.start_point[0] + 1, node.end_point[0] + 1
        if category in ("class", "type"):
            # Qualify under the enclosing container. Without this, two classes
            # that each declare an inner class of the same name produce one
            # qualified name, and the deduplication below silently drops the
            # second definition.
            q = f"{class_ctx}.{sym}" if class_ctx else f"{path}::{sym}"
            pf.symbols.append(Symbol(category, sym, q, start, end, _text(node, src), class_ctx or module_q))
            emitted.add(q)
            last_emitted[0] = q
            add_inherits(node, q)
            return q, q
        owner = owner_of(node)
        ctx = f"{path}::{owner}" if owner else class_ctx
        if category == "test" or (spec.test_prefix and sym.startswith(spec.test_prefix)) or is_test_file:
            kind = "test"
        elif ctx:
            kind = "method"
        else:
            kind = "function"
        if ctx:
            # The owning type is only a parent if it was itself emitted as a
            # symbol. Lua declares a table and then attaches functions to it, so
            # the qualified name records the owner while the parent stays the
            # module rather than pointing at a node that does not exist.
            q = f"{ctx}.{sym}"
            parent = ctx if ctx in emitted else module_q
        else:
            q, parent = f"{path}::{sym}", module_q
        pf.symbols.append(Symbol(kind, sym, q, start, end, _text(node, src), parent))
        emitted.add(q)
        last_emitted[0] = q
        return q, class_ctx

    def add_import(node: Any, exclude: Any | None = None) -> None:
        if spec.import_name_field:
            n = node.child_by_field_name(spec.import_name_field)
            if n is not None:
                pf.references.append(Reference(module_q, "imports", _import_name(_text(n, src))))
                return
        target = _first_literal(node, src, exclude)
        if target:
            pf.references.append(Reference(module_q, "imports", _import_name(target)))

    def handle_call(child: Any, prev: Any | None, class_ctx: str | None, func_ctx: str | None) -> None:
        if spec.call_require_child and not _has_descendant(child, spec.call_require_child):
            return
        callee_node = None
        if spec.call_prev_sibling:
            callee_node = prev
        else:
            for field_name in (spec.call_name_field, *_CALL_FIELD_FALLBACKS):
                callee_node = child.child_by_field_name(field_name)
                if callee_node is not None:
                    break
        raw = _text(callee_node, src) if callee_node is not None else _first_name_text(child, src, name_types)
        callee = _symbol_name(raw)
        if callee.lower() in import_keywords:
            add_import(child, exclude=callee_node)
            return
        pf.references.append(Reference(func_ctx or class_ctx or module_q, "calls", _last_name(callee)))

    def handle_keyword(child: Any, class_ctx: str | None, func_ctx: str | None) -> bool:
        """Elixir-style definitions: the call target keyword sets the category."""
        target = child.child_by_field_name(spec.keyword_target_field)
        keyword = _text(target, src).strip() if target is not None else ""
        if not keyword:
            return False
        args = child.child_by_field_name(spec.keyword_name_field)
        if args is None:
            args = next((c for c in child.children if c.type == spec.keyword_name_field), None)
        if keyword in spec.keyword_imports:
            if args is not None:
                pf.references.append(Reference(module_q, "imports", _import_name(_text(args, src))))
            return True
        category = spec.keyword_symbols.get(keyword)
        if category is None:
            return False
        sym = _symbol_name(_text(args, src)) if args is not None else "<anon>"
        owner_ctx = class_ctx
        owner_index = spec.keyword_owner_arg.get(keyword)
        if owner_index is not None and args is not None:
            owner = _keyword_arg_name(args, src, owner_index)
            if owner:
                owner_ctx = f"{path}::{owner}"
        q, new_ctx = add_symbol(child, category, owner_ctx, name=sym)
        # The argument list repeats the definition being introduced, so walking
        # it would record the definition as calling itself.
        rest = [c for c in child.children if args is None or c.id != args.id]
        walk_children(rest, new_ctx, None if category in ("class", "type") else q)
        return True

    def walk(node: Any, class_ctx: str | None, func_ctx: str | None) -> None:
        walk_children(node.children, class_ctx, func_ctx)

    def walk_children(children: list[Any], class_ctx: str | None, func_ctx: str | None) -> None:
        prev: Any | None = None
        # The definition a signature names and the block that implements it are
        # siblings in some grammars (Dart), so the last definition seen supplies
        # the calling context for a following body.
        last_def: str | None = None
        for child in children:
            if child.type in spec.skip_node_types:
                continue
            if child.is_named and child.type in spec.scope_following_siblings:
                # A declaration that owns what comes after it rather than what
                # is inside it (Perl's package). It becomes a symbol and then
                # the owning context for the remaining siblings.
                last_def, class_ctx = add_symbol(child, "class", class_ctx)
                func_ctx = None
                prev = child
                continue
            if not child.is_named:
                # An anonymous token can carry the same type name as the
                # construct it introduces: Ruby's `class` keyword is a node of
                # type "class", exactly like the class declaration around it.
                # Only named nodes are definitions, and a keyword token has no
                # children worth descending into.
                continue
            category = spec.node_category(child.type)
            required = spec.symbol_require_child.get(child.type)
            if required is not None and not any(c.type in required for c in child.children):
                # Named but bodyless: this node refers to a definition rather
                # than being one, so it is walked but never emitted.
                walk(child, class_ctx, func_ctx)
                prev = child
                continue
            if child.type in spec.keyword_node_types and handle_keyword(child, class_ctx, func_ctx):
                pass
            elif child.type in spec.scope_node_types:
                # Scope-only: names the enclosing type for the definitions
                # inside it without becoming a symbol itself, so no second node
                # appears under a qualified name the type already owns.
                scope_n = child.child_by_field_name(spec.scope_name_field)
                scope_name = _last_name(_text(scope_n, src)) if scope_n is not None else ""
                walk(child, f"{path}::{scope_name}" if scope_name else class_ctx, None)
            elif child.type in spec.test_node_types:
                last_def, _ = add_symbol(child, "test", class_ctx, name=_test_name(child, src, spec, name_types))
                walk(child, class_ctx, last_def)
            elif category in ("class", "type"):
                last_def, new_ctx = add_symbol(child, category, class_ctx)
                walk(child, new_ctx, None)
            elif category in ("function", "method", "test"):
                last_def, _ = add_symbol(child, category, class_ctx)
                walk(child, class_ctx, last_def)
            elif child.type in spec.binding_node_types and (bound := _bound_category(child, spec)) is not None:
                last_def, new_ctx = add_symbol(child, bound, class_ctx)
                # The first named child of a first-child binding is the
                # definition itself (Julia repeats the call being defined), so
                # walking it would record the definition as calling itself.
                rest = _named_tail(child) if spec.binding_first_child else child.children
                walk_children(rest, new_ctx if bound in ("class", "type") else class_ctx, last_def)
            elif child.type in spec.import_node_types:
                add_import(child)
            elif child.type in spec.body_node_types:
                walk(child, class_ctx, last_def or last_emitted[0] or func_ctx)
            elif child.type in spec.call_node_types:
                handle_call(child, prev, class_ctx, func_ctx)
                walk(child, class_ctx, func_ctx)
            else:
                walk(child, class_ctx, func_ctx)
            if child.is_named:
                prev = child

    walk(root, None, None)
    return pf


def _named_tail(node: Any) -> list[Any]:
    """Children after the first named one, in source order."""
    out: list[Any] = []
    dropped = False
    for child in node.children:
        if not dropped and child.is_named:
            dropped = True
            continue
        out.append(child)
    return out


def _test_name(node: Any, src: bytes, spec: LanguageSpec, name_types: tuple[str, ...]) -> str:
    """Name for a node that is a test by node type rather than by naming.

    Zig writes ``test "adds two" { ... }``, so the test's name is a string
    literal and not an identifier anywhere in the declaration.
    """
    for child in node.children:
        if "string" in child.type:
            text = _text(child, src).strip().strip("\"'")
            if text:
                return text
    return _symbol_name(_first_name_text(node, src, name_types))


def _keyword_arg_name(args: Any, src: bytes, index: int) -> str:
    """Name carried by the argument at ``index`` of a keyword definition.

    Grammars wrap each argument in its own node and put separators between them,
    so the positional argument is the nth NAMED child that is not a separator.
    """
    positional = [c for c in args.children if c.is_named and c.type not in ("comma",)]
    if index >= len(positional):
        return ""
    return _symbol_name(_text(positional[index], src))


def _bound_category(node: Any, spec: LanguageSpec) -> str | None:
    """Category implied by what a binding node binds, or None if it binds none."""
    named = [c for c in node.children if c.is_named]
    candidates = named[:1] if spec.binding_first_child else named
    for child in candidates:
        category = spec.binding_value_types.get(child.type)
        if category is not None:
            return category
    return None


def _has_descendant(node: Any, types: tuple[str, ...]) -> bool:
    queue = list(node.children)
    while queue:
        n = queue.pop(0)
        if n.type in types:
            return True
        queue.extend(n.children)
    return False


def _first_literal(node: Any, src: bytes, exclude: Any | None) -> str:
    """First string or name descendant, used to read a call-style import target.

    ``exclude`` is the callee node, which is skipped so that the callee name of
    ``source ./lib.sh`` is not mistaken for the file being sourced.
    """
    queue = list(node.children)
    while queue:
        n = queue.pop(0)
        if exclude is not None and n.id == exclude.id:
            continue
        if "string" in n.type or n.type in _NAME_NODE_TYPES or n.type in ("spath", "path", "command_argument", "generic_token"):
            text = _text(n, src).strip()
            if text:
                return text
        queue.extend(n.children)
    return ""


_EXTRACTORS: dict[str, Callable[[str, str, Any, bytes], ParsedFile]] = {
    "python": _extract_python,
    "javascript": _extract_javascript,
    "go": _extract_go,
    # TypeScript's grammar emits the same class, function, method, call, and
    # import node types as JavaScript, so it shares the extractor rather than
    # duplicating it. Sharing also gives TypeScript class inheritance, which the
    # generic config-driven extractor cannot read because the extends clause is
    # an unnamed child rather than a named field.
    "typescript": _extract_javascript,
    # TSX is a separate grammar in the same package, but its node types for
    # definitions are the TypeScript ones plus JSX expression nodes, so the same
    # extractor reads it and JSX elements are simply not definitions.
    "tsx": _extract_javascript,
}

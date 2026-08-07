"""Custom-language registry for the source-code plane.

A project-owned JSON configuration file lets a user register additional
Tree-sitter parser languages without forking the project. The file is named
``dkg.languages.json`` (the project's own format and name). Each entry names an
importable Tree-sitter grammar module and a licence, plus a mapping from symbol
categories to grammar node types that a single generic extractor uses to build
the same code-graph symbols and references the built-in extractors emit.

Only permissive grammars are supported. Every entry must declare a ``licence``;
the loader records it and warns when it is not on the permissive allow-list. The
grammar itself is user-provided and is never a runtime dependency of the
platform, so it is capability-detected at parse time and degrades cleanly when
absent.
"""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.errors import ValidationError

# Permissive licence identifiers accepted for a user-registered grammar. This is
# advisory (the loader records the declared licence and flags a non-permissive
# one); it does not download or inspect the grammar.
PERMISSIVE_LICENCES = {
    "MIT",
    "BSD",
    "BSD-2-CLAUSE",
    "BSD-3-CLAUSE",
    "APACHE-2.0",
    "APACHE2",
    "ISC",
    "HPND",
    "UNLICENSE",
    "0BSD",
    "PSF-2.0",
    "PUBLIC-DOMAIN",
}

# The default config filename, looked up in the DKG home. The project's own name;
# not the reference tool's distinctive configuration filename.
DEFAULT_CONFIG_NAME = "dkg.languages.json"

_SYMBOL_CATEGORIES = ("class", "function", "method", "type", "test", "module")
# Node types that carry a usable identifier when reading a symbol or an
# inheritance target. Grammars disagree on what they call an identifier, so the
# set is deliberately broad; it is only consulted when a spec does not name the
# field that holds the name, and a spec may override it with name_node_types.
_NAME_NODE_TYPES = (
    "identifier",
    "constant",
    "type_identifier",
    "scoped_type_identifier",
    "scope_resolution",
    "name",
    "simple_identifier",
    "simple_name",
    "function_name",
    "command_name",
    "field_identifier",
    "namespace_identifier",
    "package_identifier",
    "variable_name",
    "module_name",
    "alias",
    "word",
)


def is_permissive(licence: str) -> bool:
    return (licence or "").strip().upper() in PERMISSIVE_LICENCES


@dataclass
class LanguageSpec:
    """A single registered language."""

    name: str
    grammar_module: str
    licence: str
    extensions: tuple[str, ...]
    symbols: dict[str, tuple[str, ...]]
    name_field: str = "name"
    # Overrides the default identifier-like node-type set used when a name has
    # to be found by search rather than read from a named field.
    name_node_types: tuple[str, ...] = ()
    # Subtrees not descended into when searching for a name. A return type is
    # written before the name in Objective-C and PowerShell, so without this the
    # type's own identifier would be taken as the definition's name.
    name_skip_types: tuple[str, ...] = ()
    # A field on the *name* node that names the type owning the definition. Lua
    # is the motivating case: `function Shape.area()` puts the whole
    # `Shape.area` in the name field, with the owner in that node's `table`
    # field, so the definition becomes a method of Shape rather than a
    # free function whose name happens to contain a dot.
    owner_field: str = ""
    call_node_types: tuple[str, ...] = ()
    call_name_field: str = "method"
    # Some grammars have no call node at all: the callee and the argument list
    # are siblings. Dart is the motivating case, where `helper()` parses as
    # identifier followed by a `selector` holding the argument part. Setting
    # call_prev_sibling reads the callee from the preceding named sibling, and
    # call_require_child keeps the rule from firing on selectors that are field
    # accesses rather than calls.
    call_prev_sibling: bool = False
    call_require_child: tuple[str, ...] = ()
    # Callee names that mean "this is an import, not a call". Shell is the
    # motivating case: `source ./lib.sh` is an ordinary command node.
    import_keywords: tuple[str, ...] = ()
    inherits_field: str = ""
    # Inheritance carried by an unnamed child wrapper rather than a field, for
    # example PHP's base_clause, C#'s base_list, or Solidity's
    # inheritance_specifier. Every identifier-like descendant becomes an edge.
    inherits_node_types: tuple[str, ...] = ()
    import_node_types: tuple[str, ...] = ()
    import_name_field: str = ""
    # A binding whose category is decided by the value it is bound to, for
    # example Zig's `const Point = struct { ... }` or Julia's short-form
    # `helper(p) = p.x`. binding_first_child restricts the match to the first
    # named child, which is what separates a Julia short-form function from an
    # ordinary assignment of a call result to a variable.
    binding_node_types: tuple[str, ...] = ()
    binding_value_types: dict[str, str] = field(default_factory=dict)
    binding_first_child: bool = False
    # Grammars that express every definition as a generic call whose target is a
    # keyword (Elixir: defmodule, def, defp). The keyword decides the category
    # and the name comes from the keyword_name_field.
    keyword_node_types: tuple[str, ...] = ()
    keyword_symbols: dict[str, str] = field(default_factory=dict)
    keyword_imports: tuple[str, ...] = ()
    keyword_name_field: str = "arguments"
    # The field holding the keyword itself. Elixir calls it "target"; R's call
    # node calls the same thing "function", and R needs this path because
    # setClass and setMethod are ordinary calls rather than declarations.
    keyword_target_field: str = "target"
    # For a keyword definition, which argument names the type that owns it.
    # R's ``setMethod("show", "Account", ...)`` is the case: the method's owner
    # is its second argument, so without this the method would be recorded as a
    # free function and the class it belongs to would be lost.
    keyword_owner_arg: dict[str, int] = field(default_factory=dict)
    # Node types whose name text is used verbatim instead of being reduced to
    # its last identifier. A Perl package is the motivating case: the definition
    # is named ``Geometry::Shapes``, and reducing that to ``Shapes`` would name
    # a package that does not exist.
    raw_name_node_types: tuple[str, ...] = ()
    # A declaration that scopes the definitions that FOLLOW it as siblings,
    # rather than the ones nested inside it. Perl's ``package`` is the case: it
    # is a statement, not a block, so every sub after it belongs to it even
    # though none of them is its child. The node is emitted as a symbol and
    # becomes the owning context for the rest of the sibling list.
    scope_following_siblings: tuple[str, ...] = ()
    # Subtrees never walked. Julia repeats the function name inside the
    # signature as a call expression, which would otherwise emit a self-call.
    skip_node_types: tuple[str, ...] = ()
    # Blocks that implement the definition named by the preceding sibling. Dart
    # is the motivating case: a method signature and its body are siblings, so
    # without this every call in the body is attributed to the enclosing class
    # rather than to the method that makes it.
    body_node_types: tuple[str, ...] = ()
    # Node types that are always a test marker regardless of naming, for example
    # Zig's `test "name" { ... }` block.
    test_node_types: tuple[str, ...] = ()
    # Definitions that have no name of their own, mapped to the name to use. A
    # Solidity constructor is the motivating case: without a fixed name the
    # first identifier in its body would be taken as its name.
    default_names: dict[str, str] = field(default_factory=dict)
    # A node type that only counts as a definition when it carries one of these
    # children. C is the motivating case: `struct Node *next;` inside a struct
    # is a field whose type is named, not a second definition of that type, and
    # only the one with a body is the definition.
    symbol_require_child: dict[str, tuple[str, ...]] = field(default_factory=dict)
    test_prefix: str = "test"
    # Node types that supply a naming scope for the definitions inside them but
    # are not themselves a symbol. Rust's impl block is the motivating case: it
    # says which type a function belongs to, but the type is already declared by
    # its struct, so emitting a symbol for the impl too would put two nodes in
    # the graph under one qualified name. Treating it as scope-only gives
    # ``file.rs::Point.norm`` with no duplicate node.
    scope_node_types: tuple[str, ...] = ()
    scope_name_field: str = "name"

    def node_category(self, node_type: str) -> str | None:
        for category, types in self.symbols.items():
            if node_type in types:
                return category
        return None

    def grammar_available(self) -> bool:
        try:
            importlib.import_module(self.grammar_module)
            return True
        except ImportError:
            return False


@dataclass
class LanguageRegistry:
    """The active set of user-registered languages."""

    specs: dict[str, LanguageSpec] = field(default_factory=dict)
    _by_ext: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for spec in self.specs.values():
            for ext in spec.extensions:
                self._by_ext[ext.lower()] = spec.name

    def get(self, name: str) -> LanguageSpec | None:
        return self.specs.get(name)

    def language_for_ext(self, ext: str) -> str | None:
        return self._by_ext.get(ext.lower())

    def extensions(self) -> set[str]:
        return set(self._by_ext.keys())

    def is_empty(self) -> bool:
        return not self.specs

    def available_names(self) -> list[str]:
        return [name for name, spec in self.specs.items() if spec.grammar_available()]


def _require(obj: dict, key: str, typ: type, path: str) -> Any:
    if key not in obj:
        raise ValidationError(f"{path}: missing required key {key!r}")
    val = obj[key]
    if not isinstance(val, typ) or (typ is not bool and isinstance(val, bool)):
        raise ValidationError(f"{path}: key {key!r} must be {typ.__name__}")
    return val


def _section(obj: dict, key: str, path: str) -> dict:
    """Return an optional object-valued config section, validated."""
    val = obj.get(key) or {}
    if val and not isinstance(val, dict):
        raise ValidationError(f"{path}.{key}: must be an object")
    return val


def _spec_from_obj(obj: dict, path: str) -> LanguageSpec:
    if not isinstance(obj, dict):
        raise ValidationError(f"{path}: language entry must be an object")
    name = _require(obj, "name", str, path).strip()
    grammar_module = _require(obj, "grammar_module", str, path).strip()
    licence = _require(obj, "licence", str, path).strip()
    if not name or not grammar_module or not licence:
        raise ValidationError(f"{path}: name, grammar_module, and licence must be non-empty")
    exts_raw = _require(obj, "extensions", list, path)
    extensions = tuple(str(e).lower() for e in exts_raw if str(e).strip())
    if not extensions:
        raise ValidationError(f"{path}: at least one extension is required")
    symbols_raw = _require(obj, "symbols", dict, path)
    symbols: dict[str, tuple[str, ...]] = {}
    for category, types in symbols_raw.items():
        if category not in _SYMBOL_CATEGORIES:
            raise ValidationError(
                f"{path}.symbols: unknown category {category!r}; allowed: {_SYMBOL_CATEGORIES}"
            )
        if not isinstance(types, list) or not all(isinstance(t, str) for t in types):
            raise ValidationError(f"{path}.symbols.{category}: must be a list of node-type strings")
        symbols[category] = tuple(types)
    if not symbols:
        raise ValidationError(f"{path}.symbols: at least one symbol category is required")
    calls = _section(obj, "calls", path)
    inherits = _section(obj, "inherits", path)
    scope = _section(obj, "scope", path)
    imports = _section(obj, "imports", path)
    bindings = _section(obj, "bindings", path)
    keywords = _section(obj, "keywords", path)
    binding_values = bindings.get("values") or {}
    if binding_values and not isinstance(binding_values, dict):
        raise ValidationError(f"{path}.bindings.values: must be an object of node type to category")
    for node_type, category in binding_values.items():
        if category not in _SYMBOL_CATEGORIES:
            raise ValidationError(
                f"{path}.bindings.values.{node_type}: unknown category {category!r}; allowed: {_SYMBOL_CATEGORIES}"
            )
    keyword_symbols = keywords.get("symbols") or {}
    if keyword_symbols and not isinstance(keyword_symbols, dict):
        raise ValidationError(f"{path}.keywords.symbols: must be an object of keyword to category")
    for kw, category in keyword_symbols.items():
        if category not in _SYMBOL_CATEGORIES:
            raise ValidationError(
                f"{path}.keywords.symbols.{kw}: unknown category {category!r}; allowed: {_SYMBOL_CATEGORIES}"
            )
    return LanguageSpec(
        name=name,
        grammar_module=grammar_module,
        licence=licence,
        extensions=extensions,
        symbols=symbols,
        name_field=str(obj.get("name_field", "name")),
        name_node_types=tuple(str(t) for t in obj.get("name_node_types", [])),
        name_skip_types=tuple(str(t) for t in obj.get("name_skip_types", [])),
        owner_field=str(obj.get("owner_field", "")),
        call_node_types=tuple(str(t) for t in calls.get("node_types", [])),
        call_name_field=str(calls.get("name_field", "method")),
        call_prev_sibling=bool(calls.get("prev_sibling", False)),
        call_require_child=tuple(str(t) for t in calls.get("require_child", [])),
        import_keywords=tuple(str(t) for t in calls.get("import_keywords", [])),
        inherits_field=str(inherits.get("field", "")),
        inherits_node_types=tuple(str(t) for t in inherits.get("node_types", [])),
        import_node_types=tuple(str(t) for t in imports.get("node_types", [])),
        import_name_field=str(imports.get("name_field", "")),
        binding_node_types=tuple(str(t) for t in bindings.get("node_types", [])),
        binding_value_types={str(k): str(v) for k, v in binding_values.items()},
        binding_first_child=bool(bindings.get("first_child", False)),
        keyword_node_types=tuple(str(t) for t in keywords.get("node_types", [])),
        keyword_symbols={str(k): str(v) for k, v in keyword_symbols.items()},
        keyword_imports=tuple(str(t) for t in keywords.get("imports", [])),
        keyword_name_field=str(keywords.get("name_field", "arguments")),
        skip_node_types=tuple(str(t) for t in obj.get("skip_node_types", [])),
        body_node_types=tuple(str(t) for t in obj.get("body_node_types", [])),
        test_node_types=tuple(str(t) for t in obj.get("test_node_types", [])),
        default_names={str(k): str(v) for k, v in (obj.get("default_names") or {}).items()},
        symbol_require_child={
            str(k): tuple(str(t) for t in v)
            for k, v in (obj.get("symbol_require_child") or {}).items()
        },
        test_prefix=str(obj.get("test_prefix", "test")),
        scope_node_types=tuple(str(t) for t in scope.get("node_types", [])),
        scope_name_field=str(scope.get("name_field", "name")),
    )


def parse_config(obj: Any) -> tuple[LanguageRegistry, list[str]]:
    """Validate a parsed config object and return (registry, warnings)."""
    if not isinstance(obj, dict):
        raise ValidationError("$: language config must be a JSON object")
    langs = obj.get("languages")
    if not isinstance(langs, list) or not langs:
        raise ValidationError("$.languages: must be a non-empty array of language entries")
    specs: dict[str, LanguageSpec] = {}
    warnings: list[str] = []
    for i, entry in enumerate(langs):
        spec = _spec_from_obj(entry, f"$.languages[{i}]")
        if spec.name in specs:
            raise ValidationError(f"$.languages[{i}]: duplicate language name {spec.name!r}")
        if not is_permissive(spec.licence):
            warnings.append(
                f"language {spec.name!r} declares licence {spec.licence!r}, which is not on the "
                f"permissive allow-list; only permissive grammars (MIT, BSD, Apache-2.0, ISC) are supported"
            )
        specs[spec.name] = spec
    return LanguageRegistry(specs=specs), warnings


def load_registry(path: str | Path) -> tuple[LanguageRegistry, list[str]]:
    p = Path(path)
    if not p.exists():
        raise ValidationError(f"language config not found: {p}")
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValidationError(f"language config {p} is not valid JSON: {e}") from e
    return parse_config(obj)


def default_config_path(home: str | Path) -> Path:
    return Path(home) / DEFAULT_CONFIG_NAME


def load_grammar_language(grammar_module: str) -> Any:
    """Return a tree_sitter.Language for a user-registered grammar module."""
    from ..core.errors import UnsupportedFormatError

    try:
        import tree_sitter

        mod = importlib.import_module(grammar_module)
    except ImportError as e:
        raise UnsupportedFormatError(
            f"custom-language grammar {grammar_module!r} is not installed: "
            f"pip install {grammar_module.replace('_', '-')}"
        ) from e
    return tree_sitter.Language(mod.language())


# -- Built-in languages driven by the generic extractor ---------------------
#
# Java, Ruby, and Rust are supported natively through exactly the same
# config-driven extractor the custom-language mechanism uses, rather than three
# more bespoke extractors. That keeps one code path under test and means a
# project registering its own grammar exercises the same machinery the shipped
# languages do. Their grammars are permissive (MIT) and live in the optional
# 'code-extended' extra, so the zero-dependency core is unchanged and each
# language is capability-detected at parse time.
#
# TypeScript is deliberately NOT here: its grammar emits the same node types as
# JavaScript, so it reuses the JavaScript extractor and gains class inheritance,
# which the generic extractor cannot read because TypeScript puts the extends
# clause in an unnamed child rather than a named field.
BUILTIN_SPECS: dict[str, LanguageSpec] = {
    "java": LanguageSpec(
        name="java",
        grammar_module="tree_sitter_java",
        licence="MIT",
        extensions=(".java",),
        symbols={
            "class": ("class_declaration", "enum_declaration", "record_declaration"),
            "type": ("interface_declaration",),
            "method": ("method_declaration", "constructor_declaration", "compact_constructor_declaration"),
        },
        name_field="name",
        call_node_types=("method_invocation",),
        call_name_field="name",
        inherits_field="superclass",
        test_prefix="test",
    ),
    "ruby": LanguageSpec(
        name="ruby",
        grammar_module="tree_sitter_ruby",
        licence="MIT",
        extensions=(".rb",),
        symbols={
            "class": ("class", "module"),
            "method": ("method", "singleton_method"),
        },
        name_field="name",
        call_node_types=("call",),
        call_name_field="method",
        inherits_field="superclass",
        test_prefix="test",
    ),
    "rust": LanguageSpec(
        name="rust",
        grammar_module="tree_sitter_rust",
        licence="MIT",
        extensions=(".rs",),
        symbols={
            "type": ("struct_item", "enum_item", "trait_item", "union_item"),
            # A trait method declared without a body is a definition of that
            # member, exactly as an interface method is in Java or C#.
            "function": ("function_item", "function_signature_item"),
        },
        name_field="name",
        call_node_types=("call_expression",),
        call_name_field="function",
        inherits_field="",
        test_prefix="test",
        # An impl block names the type it extends in a 'type' field. It is scope
        # only, so a function inside it becomes a method of the already-declared
        # struct instead of a second node under the same qualified name.
        scope_node_types=("impl_item",),
        scope_name_field="type",
    ),
    # -- Systems and C family ------------------------------------------------
    "c": LanguageSpec(
        name="c",
        grammar_module="tree_sitter_c",
        licence="MIT",
        extensions=(".c", ".h"),
        symbols={
            "type": ("struct_specifier", "union_specifier", "enum_specifier", "type_definition"),
            "function": ("function_definition",),
        },
        # A specifier without a body names an existing type rather than
        # defining one, which is what `struct Node *next;` inside a struct is.
        symbol_require_child={
            "struct_specifier": ("field_declaration_list",),
            "union_specifier": ("field_declaration_list",),
            "enum_specifier": ("enumerator_list",),
            "class_specifier": ("field_declaration_list",),
        },
        call_node_types=("call_expression",),
        call_name_field="function",
        import_node_types=("preproc_include",),
        import_name_field="path",
    ),
    "cpp": LanguageSpec(
        name="cpp",
        grammar_module="tree_sitter_cpp",
        licence="MIT",
        extensions=(".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx"),
        symbols={
            "class": ("class_specifier", "struct_specifier"),
            "type": ("enum_specifier", "union_specifier"),
            "function": ("function_definition",),
        },
        # A specifier without a body names an existing type rather than
        # defining one, which is what `struct Node *next;` inside a struct is.
        symbol_require_child={
            "struct_specifier": ("field_declaration_list",),
            "union_specifier": ("field_declaration_list",),
            "enum_specifier": ("enumerator_list",),
            "class_specifier": ("field_declaration_list",),
        },
        inherits_node_types=("base_class_clause",),
        call_node_types=("call_expression",),
        call_name_field="function",
        import_node_types=("preproc_include",),
        import_name_field="path",
    ),
    "csharp": LanguageSpec(
        name="csharp",
        grammar_module="tree_sitter_c_sharp",
        licence="MIT",
        extensions=(".cs",),
        symbols={
            "class": ("class_declaration", "struct_declaration", "record_declaration"),
            "type": ("interface_declaration", "enum_declaration"),
            "method": ("method_declaration", "constructor_declaration"),
        },
        inherits_node_types=("base_list",),
        call_node_types=("invocation_expression",),
        call_name_field="function",
        import_node_types=("using_directive",),
    ),
    "objc": LanguageSpec(
        name="objc",
        grammar_module="tree_sitter_objc",
        licence="MIT",
        # .h is claimed by C: an Objective-C header and a C header share the
        # extension and only the content tells them apart, so the C grammar
        # keeps it and only the unambiguous implementation extensions are here.
        extensions=(".m", ".mm"),
        symbols={
            "class": ("class_interface", "class_implementation"),
            "type": ("protocol_declaration",),
            "method": ("method_definition",),
        },
        # `- (NSString *)describe` writes the return type before the selector.
        name_skip_types=("method_type",),
        inherits_field="superclass",
        call_node_types=("message_expression", "call_expression"),
        call_name_field="method",
        import_node_types=("preproc_include",),
        import_name_field="path",
    ),
    "zig": LanguageSpec(
        name="zig",
        grammar_module="tree_sitter_zig",
        licence="MIT",
        extensions=(".zig",),
        symbols={"function": ("function_declaration",)},
        binding_node_types=("variable_declaration",),
        binding_value_types={
            "struct_declaration": "type",
            "enum_declaration": "type",
            "union_declaration": "type",
            "error_set_declaration": "type",
        },
        call_node_types=("call_expression", "builtin_function"),
        # The leading sigil is not part of the identifier the call reader
        # returns, so the keyword is recorded without it.
        import_keywords=("import",),
        test_node_types=("test_declaration",),
    ),
    # -- Mobile ---------------------------------------------------------------
    "kotlin": LanguageSpec(
        name="kotlin",
        grammar_module="tree_sitter_kotlin",
        licence="MIT",
        extensions=(".kt", ".kts"),
        symbols={
            "class": ("class_declaration", "object_declaration"),
            "function": ("function_declaration",),
        },
        inherits_node_types=("delegation_specifiers",),
        call_node_types=("call_expression",),
        import_node_types=("import",),
    ),
    "swift": LanguageSpec(
        name="swift",
        grammar_module="tree_sitter_swift",
        licence="MIT",
        extensions=(".swift",),
        symbols={
            # The grammar folds class, struct, and enum into one node type and
            # distinguishes them with a declaration_kind field, so all three are
            # reported as classes rather than claiming a distinction the parse
            # does not carry into the symbol kind.
            "class": ("class_declaration",),
            "type": ("protocol_declaration", "typealias_declaration", "associatedtype_declaration"),
            "function": ("function_declaration", "protocol_function_declaration"),
            "method": (
                "init_declaration",
                "deinit_declaration",
                "subscript_declaration",
                "protocol_property_declaration",
            ),
        },
        # These have no name node of their own; a subscript has no name at all
        # in the language, so it is recorded under the keyword that declares it.
        default_names={
            "init_declaration": "init",
            "deinit_declaration": "deinit",
            "subscript_declaration": "subscript",
        },
        inherits_node_types=("inheritance_specifier",),
        call_node_types=("call_expression",),
        import_node_types=("import_declaration",),
    ),
    "dart": LanguageSpec(
        name="dart",
        grammar_module="tree_sitter_dart",
        licence="MIT",
        extensions=(".dart",),
        symbols={
            "class": ("class_definition", "mixin_declaration", "extension_declaration"),
            "type": ("enum_declaration",),
            "function": ("function_signature",),
            "method": ("constructor_signature",),
        },
        inherits_field="superclass",
        # Dart's grammar has no call node: a call is an identifier followed by a
        # selector holding the argument part, so the callee is read from the
        # preceding sibling and only selectors with arguments count.
        call_node_types=("selector",),
        call_prev_sibling=True,
        call_require_child=("argument_part",),
        body_node_types=("function_body",),
        import_node_types=("library_import",),
    ),
    # -- Scripting ------------------------------------------------------------
    "php": LanguageSpec(
        name="php",
        grammar_module="tree_sitter_php",
        licence="MIT",
        extensions=(".php", ".php4", ".php5", ".phtml"),
        symbols={
            "class": ("class_declaration", "trait_declaration"),
            "type": ("interface_declaration", "enum_declaration"),
            "method": ("method_declaration",),
            "function": ("function_definition",),
        },
        inherits_node_types=("base_clause", "class_interface_clause"),
        # `$handler = fn ($x) => ...` and `$h = function () {}` bind a function
        # to a name, the same construct JavaScript, Go, and Python bind and all
        # of which are extracted.
        binding_node_types=("assignment_expression",),
        binding_value_types={"arrow_function": "function", "anonymous_function": "function"},
        call_node_types=(
            "function_call_expression",
            "member_call_expression",
            "scoped_call_expression",
            "object_creation_expression",
        ),
        call_name_field="name",
        import_node_types=("namespace_use_declaration",),
    ),
    "lua": LanguageSpec(
        name="lua",
        grammar_module="tree_sitter_lua",
        licence="MIT",
        extensions=(".lua",),
        symbols={"function": ("function_declaration",)},
        owner_field="table",
        call_node_types=("function_call",),
        call_name_field="name",
        import_keywords=("require",),
    ),
    "luau": LanguageSpec(
        name="luau",
        grammar_module="tree_sitter_luau",
        licence="MIT",
        extensions=(".luau",),
        symbols={"function": ("function_declaration",), "type": ("type_definition",)},
        owner_field="table",
        call_node_types=("function_call",),
        call_name_field="name",
        import_keywords=("require",),
    ),
    "julia": LanguageSpec(
        name="julia",
        grammar_module="tree_sitter_julia",
        licence="MIT",
        extensions=(".jl",),
        symbols={
            # A Julia module is a namespace, so it is emitted as a type symbol
            # and the definitions inside it become its methods.
            "type": ("struct_definition", "abstract_definition", "primitive_definition", "module_definition"),
            "function": ("function_definition",),
        },
        # The signature repeats the function name as a call expression, which
        # would otherwise be recorded as the function calling itself.
        skip_node_types=("signature",),
        # Short-form definitions such as `helper(p) = p.x` are assignments whose
        # first named child is the call being defined.
        binding_node_types=("assignment",),
        binding_value_types={"call_expression": "function"},
        binding_first_child=True,
        call_node_types=("call_expression",),
        import_node_types=("using_statement", "import_statement"),
    ),
    "scala": LanguageSpec(
        name="scala",
        grammar_module="tree_sitter_scala",
        licence="MIT",
        extensions=(".scala", ".sc"),
        symbols={
            "class": ("class_definition", "object_definition", "trait_definition"),
            "type": ("type_definition", "enum_definition"),
            "function": ("function_definition", "function_declaration"),
        },
        inherits_node_types=("extends_clause",),
        call_node_types=("call_expression",),
        call_name_field="function",
        import_node_types=("import_declaration",),
    ),
    "elixir": LanguageSpec(
        name="elixir",
        grammar_module="tree_sitter_elixir",
        licence="Apache-2.0",
        extensions=(".ex", ".exs"),
        # Every Elixir definition is a call whose target is a keyword, so the
        # keyword decides the category rather than the node type.
        symbols={},
        keyword_node_types=("call",),
        keyword_symbols={
            "defmodule": "class",
            "defprotocol": "type",
            # defstruct is deliberately absent: it defines the enclosing
            # module's struct and takes field names rather than a name of its
            # own, so treating it as a named definition would emit a type named
            # after the struct's first field.
            "def": "method",
            "defp": "method",
            "defmacro": "method",
            "defmacrop": "method",
            "test": "test",
        },
        keyword_imports=("import", "alias", "require", "use"),
        keyword_name_field="arguments",
        call_node_types=("call",),
    ),
    # -- Shells ---------------------------------------------------------------
    "bash": LanguageSpec(
        name="bash",
        grammar_module="tree_sitter_bash",
        licence="MIT",
        # ksh is close enough to POSIX shell that the bash grammar parses it;
        # that is recorded as a shared grammar, not as a ksh grammar.
        extensions=(".sh", ".bash", ".ksh"),
        symbols={"function": ("function_definition",)},
        call_node_types=("command",),
        call_name_field="name",
        import_keywords=("source", "."),
    ),
    "zsh": LanguageSpec(
        name="zsh",
        grammar_module="tree_sitter_zsh",
        licence="MIT",
        extensions=(".zsh",),
        symbols={"function": ("function_definition",)},
        call_node_types=("command",),
        call_name_field="name",
        import_keywords=("source", "."),
    ),
    "powershell": LanguageSpec(
        name="powershell",
        grammar_module="tree_sitter_powershell",
        licence="MIT",
        extensions=(".ps1", ".psm1", ".psd1"),
        symbols={
            "class": ("class_statement",),
            "function": ("function_statement",),
            "method": ("class_method_definition",),
        },
        # `[double] Area()` writes the return type before the method name.
        name_skip_types=("type_literal",),
        call_node_types=("command",),
        call_name_field="command_name",
        import_keywords=("import-module", "using"),
    ),
    # -- Domain specific ------------------------------------------------------
    "solidity": LanguageSpec(
        name="solidity",
        grammar_module="tree_sitter_solidity",
        licence="MIT",
        extensions=(".sol",),
        symbols={
            "class": ("contract_declaration", "library_declaration"),
            "type": (
                "interface_declaration",
                "struct_declaration",
                "enum_declaration",
                "user_defined_type_definition",
                "error_declaration",
                "event_definition",
            ),
            "function": (
                "function_definition",
                "constructor_definition",
                "modifier_definition",
                "fallback_receive_definition",
            ),
        },
        inherits_node_types=("inheritance_specifier",),
        # A constructor, receive, or fallback definition has no name of its own;
        # without a fixed name the first identifier in its body would be used.
        default_names={"constructor_definition": "constructor"},
        call_node_types=("call_expression",),
        call_name_field="function",
        import_node_types=("import_directive",),
        import_name_field="source",
    ),
    "sql": LanguageSpec(
        name="sql",
        grammar_module="tree_sitter_sql",
        licence="MIT",
        extensions=(".sql",),
        symbols={
            "type": ("create_table", "create_view", "create_type", "create_index"),
            "function": ("create_function", "create_procedure"),
        },
    ),
    "verilog": LanguageSpec(
        name="verilog",
        grammar_module="tree_sitter_verilog",
        licence="MIT",
        extensions=(".v", ".sv", ".svh", ".vh"),
        symbols={
            "class": ("module_declaration", "class_declaration", "interface_declaration"),
            "function": ("function_declaration", "task_declaration"),
        },
    ),
    "nix": LanguageSpec(
        name="nix",
        grammar_module="tree_sitter_nix",
        licence="MIT",
        extensions=(".nix",),
        symbols={},
        binding_node_types=("binding",),
        binding_value_types={"function_expression": "function", "attrset_expression": "type"},
        call_node_types=("apply_expression",),
        call_name_field="function",
        import_keywords=("import",),
    ),
    # -- Bundle-backed grammars ----------------------------------------------
    #
    # These five have no dedicated Tree-sitter package this project can depend
    # on, so their grammar comes from the multi-grammar bundle in the optional
    # 'code-bundle' extra. Each licence below was measured at the exact upstream
    # revision the bundle compiles; see capability.BUNDLE_GRAMMAR_SOURCES for the
    # repository and revision, and docs/grammar_bundle_licences.json for the
    # audit of every grammar the bundle ships. They are ordinary config-driven
    # specs in every other respect, so they exercise the same one extractor.
    #
    # Without the extra installed each of these degrades to the documented
    # pattern extractor in fallback.py rather than disappearing, and the
    # inventory reports which of the two actually ran.
    "r": LanguageSpec(
        name="r",
        grammar_module="tree_sitter_language_pack",
        licence="MIT",
        extensions=(".r", ".R"),
        # R has no function declaration: a function is a value bound by `<-`,
        # which is a binary operator like any other, so the binding is what
        # decides whether an assignment defines a function.
        symbols={},
        binding_node_types=("binary_operator",),
        binding_value_types={"function_definition": "function"},
        # S4 and reference classes are declared by calling setClass or
        # setMethod, so the called name decides the category. R's call node
        # names the callee "function" rather than "target".
        keyword_node_types=("call",),
        keyword_target_field="function",
        keyword_symbols={
            "setClass": "class",
            "setRefClass": "class",
            "R6Class": "class",
            "setGeneric": "function",
            "setMethod": "method",
            "setValidity": "method",
        },
        # setMethod("show", "Account", ...) declares a method OF Account, named
        # by its second argument.
        keyword_owner_arg={"setMethod": 1, "setValidity": 1},
        keyword_imports=("library", "require", "requireNamespace", "source"),
        keyword_name_field="arguments",
        call_node_types=("call",),
        call_name_field="function",
    ),
    "gdscript": LanguageSpec(
        name="gdscript",
        grammar_module="tree_sitter_language_pack",
        licence="MIT",
        extensions=(".gd",),
        symbols={
            # class_name declares the type the whole file defines, and a nested
            # `class` block declares an inner one.
            "type": ("class_name_statement",),
            "class": ("class_definition",),
            "function": ("function_definition",),
        },
        name_field="name",
        inherits_node_types=("extends_statement",),
        call_node_types=("call",),
    ),
    "rescript": LanguageSpec(
        name="rescript",
        grammar_module="tree_sitter_language_pack",
        licence="MIT",
        extensions=(".res", ".resi"),
        symbols={
            "function": ("let_declaration",),
            "type": ("type_declaration",),
            "class": ("module_declaration",),
        },
        # The name sits one level down, inside the binding node, and ReScript
        # names its identifier nodes by what they bind rather than calling them
        # all "identifier".
        name_node_types=("value_identifier", "type_identifier", "module_identifier"),
        call_node_types=("call_expression",),
        call_name_field="function",
        import_node_types=("open_statement", "include_statement"),
    ),
    "vbnet": LanguageSpec(
        name="vbnet",
        grammar_module="tree_sitter_language_pack",
        licence="MIT",
        extensions=(".vb",),
        symbols={
            "class": ("class_block",),
            # A module, interface, structure, and enum are all types rather than
            # instantiable classes.
            "type": ("module_block", "interface_block", "structure_block", "enum_block"),
            "method": ("method_declaration",),
        },
        name_field="name",
        call_node_types=("invocation",),
        call_name_field="target",
        import_node_types=("imports_statement",),
        import_name_field="namespace",
    ),
    "perl": LanguageSpec(
        name="perl",
        grammar_module="tree_sitter_language_pack",
        licence="MIT",
        extensions=(".pl", ".pm", ".t"),
        symbols={"function": ("subroutine_declaration_statement",)},
        name_field="name",
        # `package Foo::Bar;` is a statement, not a block, so it owns the subs
        # that FOLLOW it rather than any it contains.
        scope_following_siblings=("package_statement",),
        # The package name is the whole `Foo::Bar`, not its last segment.
        raw_name_node_types=("package_statement",),
        call_node_types=("function_call_expression", "method_call_expression", "ambiguous_function_call_expression"),
        call_name_field="function",
        import_node_types=("use_statement",),
        import_name_field="module",
    ),
}


def builtin_spec(language: str) -> LanguageSpec | None:
    return BUILTIN_SPECS.get(language)


# The active registry consulted by parse_source/language_for when a caller does
# not pass one explicitly. Empty by default, so built-in behaviour is unchanged.
_ACTIVE = LanguageRegistry()


def active_registry() -> LanguageRegistry:
    return _ACTIVE


def set_active_registry(registry: LanguageRegistry) -> None:
    global _ACTIVE
    _ACTIVE = registry

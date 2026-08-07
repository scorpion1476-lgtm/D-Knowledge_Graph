"""Original intra-procedural dataflow: def-use type inference and optional taint.

This is written from the general technique over the project's own Tree-sitter AST;
it does not depend on any static-analysis engine. Two capabilities:

- Local type inference. Within a function body it tracks the reaching definition
  of each local variable in source order. An assignment of a constructor
  (``x = ClassName(...)`` in Python, ``x = new ClassName(...)`` in JavaScript)
  gives the variable that class type, so a later ``x.method()`` resolves to that
  class's method. This resolves the same ambiguity a language server does, but
  statically and with no server, so it is a resolution lever for the fallback
  path and for languages without a server here.
- Optional source-to-sink taint. A value from a configured source that reaches a
  configured sink within a function is reported as an advisory security signal.

The analysis is intra-procedural and flow-sensitive on straight-line code; across
branches it is an honest approximation (last reaching definition wins).
"""

from __future__ import annotations

from typing import Any

from .capability import get_language
from .model import ParsedFile

# Advisory taint configuration (intentionally small and general).
_TAINT_SOURCES = {"input", "getenv", "get", "recv", "read", "args", "form", "argv"}
_TAINT_SINKS = {"eval", "exec", "system", "popen", "execute", "executescript", "compile"}


def _text(node: Any) -> str:
    return node.text.decode("utf-8", "replace") if node is not None and node.text is not None else ""


def _methods_by_class(parsed_files: list[ParsedFile]) -> dict[str, dict[str, str]]:
    """Map a class short name to {method_name: method_qualified} across the repo."""
    out: dict[str, dict[str, str]] = {}
    for pf in parsed_files:
        for s in pf.symbols:
            if s.kind == "method" and s.parent:
                class_short = s.parent.split("::")[-1].split(".")[-1]
                out.setdefault(class_short, {})[s.name] = s.qualified
    return out


def _class_names(parsed_files: list[ParsedFile]) -> set[str]:
    return {s.name for pf in parsed_files for s in pf.symbols if s.kind in ("class", "type")}


def _constructor_class(node: Any, language: str, classes: set[str]) -> str | None:
    """Return the class name a right-hand side constructs, or None."""
    if language == "python" and node.type == "call":
        fn = node.child_by_field_name("function")
        if fn is not None and fn.type == "identifier" and _text(fn) in classes:
            return _text(fn)
    if language == "javascript" and node.type == "new_expression":
        ctor = node.child_by_field_name("constructor")
        if ctor is not None and ctor.type == "identifier" and _text(ctor) in classes:
            return _text(ctor)
    return None


def _enclosing_function(symbols: list, line1: int, path: str) -> str:
    best, best_start = path, -1
    for s in symbols:
        if s.kind in ("function", "method", "test") and s.start_line <= line1 <= s.end_line and s.start_line > best_start:
            best_start, best = s.start_line, s.qualified
    return best


def dataflow_resolutions(parsed_files: list[ParsedFile], texts: dict[str, str]) -> dict[tuple[str, str], str]:
    """Resolve method calls by intra-procedural local type inference. No server.

    Returns (caller_qualified, method_name) -> method_qualified for method calls
    on a local variable whose type was inferred from a constructor assignment.
    """
    import tree_sitter

    classes = _class_names(parsed_files)
    methods = _methods_by_class(parsed_files)
    resolutions: dict[tuple[str, str], str] = {}

    for pf in parsed_files:
        if pf.path not in texts or pf.language not in ("python", "javascript"):
            continue
        src = texts[pf.path].encode("utf-8")
        root = tree_sitter.Parser(get_language(pf.language)).parse(src).root_node
        # Types are tracked per function scope in source order.
        _walk_types(root, pf.language, classes, methods, pf.symbols, pf.path, resolutions)
    return resolutions


def _assignment_target_and_value(node: Any, language: str) -> tuple[str | None, Any]:
    if language == "python" and node.type == "assignment":
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        if left is not None and left.type == "identifier":
            return _text(left), right
    if language == "javascript" and node.type == "variable_declarator":
        name = node.child_by_field_name("name")
        value = node.child_by_field_name("value")
        if name is not None and name.type == "identifier":
            return _text(name), value
    if language == "javascript" and node.type == "assignment_expression":
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        if left is not None and left.type == "identifier":
            return _text(left), right
    return None, None


def _method_call(node: Any, language: str) -> tuple[str | None, str | None]:
    """Return (receiver_var, method_name) for a method call, else (None, None)."""
    if language == "python" and node.type == "call":
        fn = node.child_by_field_name("function")
        if fn is not None and fn.type == "attribute":
            obj = fn.child_by_field_name("object")
            attr = fn.child_by_field_name("attribute")
            if obj is not None and obj.type == "identifier" and attr is not None:
                return _text(obj), _text(attr)
    if language == "javascript" and node.type == "call_expression":
        fn = node.child_by_field_name("function")
        if fn is not None and fn.type == "member_expression":
            obj = fn.child_by_field_name("object")
            prop = fn.child_by_field_name("property")
            if obj is not None and obj.type == "identifier" and prop is not None:
                return _text(obj), _text(prop)
    return None, None


def _walk_types(root, language, classes, methods, symbols, path, resolutions) -> None:
    # A depth-first source-order walk that maintains variable types for the
    # current function scope and resolves method calls against them.
    def visit(node: Any, var_types: dict[str, str]) -> None:
        target, value = _assignment_target_and_value(node, language)
        if target is not None:
            cls = _constructor_class(value, language, classes) if value is not None else None
            if cls is not None:
                var_types[target] = cls
            elif value is not None and value.type == "identifier" and _text(value) in var_types:
                var_types[target] = var_types[_text(value)]
            else:
                var_types.pop(target, None)
        recv, method_name = _method_call(node, language)
        if recv is not None and recv in var_types:
            cls = var_types[recv]
            callee = methods.get(cls, {}).get(method_name or "")
            if callee is not None:
                caller = _enclosing_function(symbols, node.start_point[0] + 1, path)
                if caller != callee:
                    resolutions[(caller, method_name)] = callee
        # A nested function starts a fresh variable scope.
        starts_scope = node.type in ("function_definition", "method_definition", "function_declaration")
        child_scope = {} if starts_scope else var_types
        for c in node.children:
            visit(c, child_scope)

    visit(root, {})


def taint_findings(parsed_files: list[ParsedFile], texts: dict[str, str]) -> list[dict]:
    """Advisory intra-procedural source-to-sink taint (optional security signal).

    A local variable assigned from a source-like call that later flows into a
    sink-like call within the same function is reported. Advisory and
    over-approximate; not an authoritative vulnerability finding.
    """
    import tree_sitter

    findings: list[dict] = []
    for pf in parsed_files:
        if pf.path not in texts or pf.language != "python":
            continue
        src = texts[pf.path].encode("utf-8")
        root = tree_sitter.Parser(get_language(pf.language)).parse(src).root_node
        _walk_taint(root, pf.symbols, pf.path, findings)
    return findings


def _call_name(node: Any) -> str | None:
    if node.type == "call":
        fn = node.child_by_field_name("function")
        if fn is None:
            return None
        if fn.type == "identifier":
            return _text(fn)
        if fn.type == "attribute":
            attr = fn.child_by_field_name("attribute")
            return _text(attr) if attr is not None else None
    return None


def _walk_taint(root, symbols, path, findings) -> None:
    def visit(node: Any, tainted: set[str]) -> None:
        target, value = _assignment_target_and_value(node, "python")
        if target is not None and value is not None:
            name = _call_name(value) if value.type == "call" else None
            if name in _TAINT_SOURCES:
                tainted.add(target)
            else:
                tainted.discard(target)
        if node.type == "call":
            sink = _call_name(node)
            if sink in _TAINT_SINKS:
                args = node.child_by_field_name("arguments")
                used = {_text(n) for n in _identifiers(args)} if args is not None else set()
                if used & tainted:
                    line1 = node.start_point[0] + 1
                    findings.append({
                        "path": path,
                        "line": line1,
                        "sink": sink,
                        "function": _enclosing_function(symbols, line1, path),
                        "tainted_args": sorted(used & tainted),
                        "note": "advisory, over-approximate intra-procedural taint",
                    })
        starts_scope = node.type in ("function_definition", "method_definition")
        child = set() if starts_scope else tainted
        for c in node.children:
            visit(c, child)

    visit(root, set())


def _identifiers(node: Any) -> list[Any]:
    out: list[Any] = []

    def rec(n: Any) -> None:
        if n.type == "identifier":
            out.append(n)
        for c in n.children:
            rec(c)

    if node is not None:
        rec(node)
    return out

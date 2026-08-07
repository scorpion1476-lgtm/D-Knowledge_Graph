"""Type-aware reference resolution via language servers.

For a parsed file whose language has a server available, each call site is located
in the source, the server is asked for the definition at that position, and the
returned location is mapped back to a parsed symbol. The result is a single
resolved callee per (caller, name), which the graph layer uses to replace the
ambiguous name-match fan-out with one high-confidence edge. References the server
cannot resolve fall back to the structural analysis.

The servers are external processes over stdio (see lsp.py); this module never
imports a server as a library. When no server is available the resolution map is
empty and the graph stays structural.
"""

from __future__ import annotations

import contextlib
import tempfile
from pathlib import Path
from typing import Any

from .lsp import LspClient, resolution_available, server_command, server_init_options
from .model import ParsedFile

_SETTLE_SECONDS = 1.2


def resolve_all(
    parsed_files: list[ParsedFile],
    texts: dict[str, str],
    *,
    use_lsp: bool = True,
    use_dataflow: bool = True,
) -> dict[tuple[str, str], str]:
    """Combined resolution: language servers first, then dataflow overrides them.

    The language server resolves cross-file and richer cases broadly. Local
    intra-procedural dataflow is flow-sensitive and precise for a local
    variable's last assignment (for example a reassignment ``x = A(); x = B();
    x.m()`` is B.m), which a server over untyped source can get wrong, so a
    dataflow resolution takes precedence where it produced one. Cases dataflow
    cannot resolve keep the server's answer. The result feeds the graph as
    type-resolved edges.
    """
    resolutions: dict[tuple[str, str], str] = {}
    if use_lsp:
        resolutions.update(resolve_parsed_files(parsed_files, texts))
    if use_dataflow:
        from .dataflow import dataflow_resolutions

        resolutions.update(dataflow_resolutions(parsed_files, texts))
    return resolutions


def _callee_sites(root: Any, language: str) -> list[tuple[str, int, int]]:
    """Return (callee_name, line0, char0) for every call site in the tree."""
    sites: list[tuple[str, int, int]] = []

    def name_node(fn: Any) -> Any:
        if fn is None:
            return None
        t = fn.type
        if t == "identifier":
            return fn
        if t == "attribute":  # python x.speak
            return fn.child_by_field_name("attribute")
        if t == "member_expression":  # js x.speak
            return fn.child_by_field_name("property")
        if t == "selector_expression":  # go x.Speak
            return fn.child_by_field_name("field")
        return None

    call_types = {"python": "call", "javascript": "call_expression", "go": "call_expression"}
    target = call_types.get(language)

    def walk(node: Any) -> None:
        if node.type == target:
            nn = name_node(node.child_by_field_name("function"))
            if nn is not None and nn.type in ("identifier", "property_identifier", "field_identifier"):
                text = nn.text.decode("utf-8", "replace") if nn.text is not None else ""
                if text:
                    sites.append((text, nn.start_point[0], nn.start_point[1]))
        for c in node.children:
            walk(c)

    if target:
        walk(root)
    return sites


def _enclosing_caller(symbols: list, line1: int, path: str) -> str:
    """The innermost function/method/test symbol containing a 1-based line."""
    best = None
    best_start = -1
    for s in symbols:
        if s.kind in ("function", "method", "test") and s.start_line <= line1 <= s.end_line:
            if s.start_line > best_start:
                best_start = s.start_line
                best = s.qualified
    return best if best is not None else path


def _symbol_at(symbols_by_path: dict[str, list], path: str, def_line1: int) -> str | None:
    """The defined symbol whose definition starts on a 1-based line."""
    candidates = [
        s for s in symbols_by_path.get(path, [])
        if s.start_line == def_line1 and s.kind in ("function", "method", "class", "type", "test")
    ]
    if not candidates:
        return None
    # Prefer the most specific (deepest qualified) on a shared line.
    candidates.sort(key=lambda s: len(s.qualified), reverse=True)
    return str(candidates[0].qualified)


def resolve_parsed_files(parsed_files: list[ParsedFile], texts: dict[str, str]) -> dict[tuple[str, str], str]:
    """Resolve call references across the parsed files using language servers.

    Returns a mapping (caller_qualified, callee_name) -> callee_qualified for the
    references a server resolved to a known in-repo symbol. Empty when no server
    is available. Files are written to a temporary workspace so cross-file
    imports resolve; the workspace is removed and servers are shut down after use.
    """
    import time

    import tree_sitter

    from .capability import get_language

    by_language: dict[str, list[ParsedFile]] = {}
    for pf in parsed_files:
        if pf.path in texts and resolution_available(pf.language):
            by_language.setdefault(pf.language, []).append(pf)

    resolutions: dict[tuple[str, str], str] = {}
    symbols_by_path = {pf.path: pf.symbols for pf in parsed_files}

    for language, files in by_language.items():
        command = server_command(language)
        if command is None:
            continue
        with tempfile.TemporaryDirectory(prefix="dkg-lsp-") as td:
            workspace = Path(td)
            uri_to_path: dict[str, str] = {}
            for pf in files:
                dest = workspace / pf.path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(texts[pf.path], encoding="utf-8")
                uri_to_path[dest.resolve().as_uri()] = pf.path

            # A server failure must not break ingestion; on any error this
            # language keeps whatever resolutions it produced and the rest stays
            # structural.
            with contextlib.suppress(Exception):
                with LspClient(
                    command,
                    workspace.resolve().as_uri(),
                    init_options=server_init_options(language),
                ) as client:
                    ts_lang = get_language(language)
                    parser = tree_sitter.Parser(ts_lang)
                    for pf in files:
                        uri = (workspace / pf.path).resolve().as_uri()
                        client.did_open(uri, language, texts[pf.path])
                    time.sleep(_SETTLE_SECONDS)
                    for pf in files:
                        uri = (workspace / pf.path).resolve().as_uri()
                        root = parser.parse(texts[pf.path].encode("utf-8")).root_node
                        for name, line0, char0 in _callee_sites(root, language):
                            caller = _enclosing_caller(pf.symbols, line0 + 1, pf.path)
                            defs: list[dict] = []
                            with contextlib.suppress(Exception):
                                defs = client.definition(uri, line0, char0)
                            callee = _first_known_callee(defs, uri_to_path, symbols_by_path)
                            if callee is not None and callee != caller:
                                resolutions[(caller, name)] = callee

    return resolutions


def _first_known_callee(defs: list[dict], uri_to_path: dict[str, str], symbols_by_path: dict[str, list]) -> str | None:
    for d in defs:
        uri = d.get("uri") or d.get("targetUri")
        rng = d.get("range") or d.get("targetSelectionRange") or d.get("targetRange")
        if not uri or not rng:
            continue
        path = uri_to_path.get(uri)
        if path is None:
            # Normalise (file URIs can differ by trailing slash or case).
            for known_uri, known_path in uri_to_path.items():
                if known_uri.rstrip("/") == uri.rstrip("/"):
                    path = known_path
                    break
        if path is None:
            continue
        def_line1 = int(rng["start"]["line"]) + 1
        sym = _symbol_at(symbols_by_path, path, def_line1)
        if sym is not None:
            return sym
    return None

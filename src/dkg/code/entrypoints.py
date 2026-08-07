"""Synthesised entry-point nodes: routed endpoints and scheduled invocations.

An execution flow has to start somewhere, and until now the starting point was a
guess. The heuristic was a symbol named ``main`` or a test, which misses every
web service (nothing in the source calls a request handler; the framework does,
from a URL) and every scheduled job (nothing calls it either; a clock does).

This module gives those a node of their own. A routed endpoint becomes a
``code:route`` node and a scheduled or event-driven invocation becomes a
``code:entrypoint`` node, each linked to the code it dispatches to by
``routes_to`` or ``dispatches``. That is deliberately not ``calls``: the parser
saw no call, and recording one would claim evidence that does not exist.

DETECTION IS PATTERN-BASED, and the patterns are declared below rather than
inferred. That has two consequences worth stating plainly. A route registered
through a variable, built by string concatenation, or produced by a framework
convention this module does not know is NOT detected, so the entry-point set is
a lower bound, not a census. And a pattern can match text that is not a
registration, so a detected entry point is a candidate like every other edge in
this plane.

The frameworks recognised are named in ``SUPPORTED`` so a reader can tell an
absent entry point from an unsupported framework. Adding one means adding a
pattern, not another code path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .model import Reference, Symbol

# Node kinds this module synthesises.
KIND_ROUTE = "route"
KIND_ENTRYPOINT = "entrypoint"

# Edge kinds. A route reaches its handler; a schedule or event invokes one.
EDGE_ROUTES_TO = "routes_to"
EDGE_DISPATCHES = "dispatches"


@dataclass(frozen=True)
class EntryPattern:
    """One recognised registration form."""

    framework: str
    languages: tuple[str, ...]
    kind: str  # KIND_ROUTE or KIND_ENTRYPOINT
    edge: str
    regex: re.Pattern[str]
    # How to name the node from the match groups.
    label: str


def _p(
    framework: str,
    languages: tuple[str, ...],
    kind: str,
    edge: str,
    pattern: str,
    label: str,
) -> EntryPattern:
    return EntryPattern(framework, languages, kind, edge, re.compile(pattern), label)


# Every recognised form. Each names a framework, the languages it applies to,
# the node kind it produces, and how the node is labelled.
PATTERNS: tuple[EntryPattern, ...] = (
    # -- Python routes: the decorator form, `@app.get("/x")` or
    # `@router.post("/x")`, which the common Python web frameworks share.
    _p(
        "python-decorator-routes",
        ("python",),
        KIND_ROUTE,
        EDGE_ROUTES_TO,
        r"@(?P<app>[A-Za-z_][A-Za-z0-9_.]*)\.(?P<verb>route|get|post|put|patch|delete)"
        r"\(\s*[\"'](?P<uri>[^\"']*)[\"'][^)]*\)\s*(?:\n\s*@[^\n]*)*\n\s*"
        r"(?:async\s+)?def\s+(?P<handler>[A-Za-z_][A-Za-z0-9_]*)",
        "{verb} {uri}",
    ),
    # -- Python routes: Django URLconf entries.
    _p(
        "django",
        ("python",),
        KIND_ROUTE,
        EDGE_ROUTES_TO,
        r"\b(?P<verb>path|re_path)\(\s*[\"'](?P<uri>[^\"']*)[\"']\s*,\s*"
        r"(?P<handler>[A-Za-z_][A-Za-z0-9_.]*)",
        "{verb} {uri}",
    ),
    # -- Python scheduled and event-driven work: Celery tasks and beat jobs.
    _p(
        "celery",
        ("python",),
        KIND_ENTRYPOINT,
        EDGE_DISPATCHES,
        r"@(?:shared_task|[A-Za-z_][A-Za-z0-9_.]*\.task)\b[^\n]*\n(?:\s*@[^\n]*\n)*\s*"
        r"(?:async\s+)?def\s+(?P<handler>[A-Za-z_][A-Za-z0-9_]*)",
        "task {handler}",
    ),
    _p(
        "apscheduler",
        ("python",),
        KIND_ENTRYPOINT,
        EDGE_DISPATCHES,
        r"@(?P<app>[A-Za-z_][A-Za-z0-9_.]*)\.scheduled_job\([^)]*\)\s*\n\s*"
        r"(?:async\s+)?def\s+(?P<handler>[A-Za-z_][A-Za-z0-9_]*)",
        "scheduled {handler}",
    ),
    # -- JavaScript and TypeScript routes: Express and its routers.
    _p(
        "express",
        ("javascript", "typescript", "tsx"),
        KIND_ROUTE,
        EDGE_ROUTES_TO,
        r"\b(?P<app>[A-Za-z_$][A-Za-z0-9_$]*)\.(?P<verb>get|post|put|patch|delete|all)"
        r"\(\s*[\"'`](?P<uri>[^\"'`]*)[\"'`]\s*,\s*(?:[^,)]+,\s*)*"
        r"(?P<handler>[A-Za-z_$][A-Za-z0-9_$.]*)\s*\)",
        "{verb} {uri}",
    ),
    # -- JavaScript scheduled work: node-cron and similar schedule calls.
    _p(
        "node-cron",
        ("javascript", "typescript", "tsx"),
        KIND_ENTRYPOINT,
        EDGE_DISPATCHES,
        r"\b(?:cron|scheduler)\.schedule\(\s*[\"'`](?P<uri>[^\"'`]*)[\"'`]\s*,\s*"
        r"(?P<handler>[A-Za-z_$][A-Za-z0-9_$.]*)",
        "cron {uri}",
    ),
    # -- Go routes: net/http and the mux objects that share its shape.
    _p(
        "net/http",
        ("go",),
        KIND_ROUTE,
        EDGE_ROUTES_TO,
        r"\b(?P<app>[A-Za-z_][A-Za-z0-9_.]*)\.(?P<verb>HandleFunc|Handle)"
        r"\(\s*[\"`](?P<uri>[^\"`]*)[\"`]\s*,\s*(?P<handler>[A-Za-z_][A-Za-z0-9_.]*)",
        "{verb} {uri}",
    ),
    # -- PHP scheduled work: the Laravel console schedule.
    _p(
        "laravel-schedule",
        ("php",),
        KIND_ENTRYPOINT,
        EDGE_DISPATCHES,
        r"\$schedule\s*->\s*(?:job|call)\(\s*(?:new\s+)?(?P<handler>[A-Za-z_\\][A-Za-z0-9_\\]*)",
        "scheduled {handler}",
    ),
)

SUPPORTED = tuple(sorted({p.framework for p in PATTERNS}))

# A single file must not be able to produce an unbounded number of nodes.
MAX_ENTRY_POINTS_PER_FILE = 500


def _last_segment(name: str) -> str:
    """The bare handler name from a dotted, namespaced, or member reference."""
    for separator in (".", "\\", "::"):
        name = name.rsplit(separator, 1)[-1]
    return name


def detect(path: str, text: str, language: str) -> tuple[list[Symbol], list[Reference]]:
    """Entry-point nodes and their dispatch edges for one file.

    Returns symbols to add and references to resolve. A file matching nothing
    returns two empty lists, which is the common case and costs one regex scan
    per applicable pattern.
    """
    symbols: list[Symbol] = []
    references: list[Reference] = []
    seen: set[str] = set()
    for pattern in PATTERNS:
        if language not in pattern.languages:
            continue
        for match in pattern.regex.finditer(text):
            if len(symbols) >= MAX_ENTRY_POINTS_PER_FILE:
                return symbols, references
            groups = {k: (v or "") for k, v in match.groupdict().items()}
            handler = _last_segment(groups.get("handler", "").strip())
            if not handler:
                continue
            label = pattern.label.format(
                verb=groups.get("verb", "").upper(),
                uri=groups.get("uri", "/"),
                handler=handler,
            ).strip()
            qualified = f"{path}::{pattern.kind}:{label}"
            if qualified in seen:
                continue
            seen.add(qualified)
            line = text.count("\n", 0, match.start()) + 1
            symbols.append(
                Symbol(
                    kind=pattern.kind,
                    name=label,
                    qualified=qualified,
                    start_line=line,
                    end_line=line,
                    text="",
                    parent=path,
                )
            )
            references.append(Reference(qualified, pattern.edge, handler))
    return symbols, references


def enrich(parsed, text: str) -> None:
    """Add entry-point nodes to an already-parsed file, in place.

    Called after the structural parse so the synthesised nodes sit alongside the
    real ones in the same shared tables. Nothing here replaces a parsed symbol.
    """
    symbols, references = detect(parsed.path, text, parsed.language)
    if not symbols:
        return
    existing = {s.qualified for s in parsed.symbols}
    parsed.symbols.extend(s for s in symbols if s.qualified not in existing)
    known = {(r.from_qualified, r.kind, r.name) for r in parsed.references}
    parsed.references.extend(
        r for r in references if (r.from_qualified, r.kind, r.name) not in known
    )


def report() -> dict:
    """What this build can and cannot see, for the ingest result."""
    return {
        "frameworks": list(SUPPORTED),
        "kinds": [KIND_ROUTE, KIND_ENTRYPOINT],
        "why": (
            "entry points are detected by declared patterns, so the set is a "
            "LOWER BOUND rather than a census: a route registered through a "
            "variable, built by concatenation, or produced by a framework "
            "convention not listed here is not detected. A detected entry point "
            "is a candidate like every other edge in this plane."
        ),
    }

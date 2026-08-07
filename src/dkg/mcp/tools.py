"""Tool implementations for the MCP surface.

The default registry is read-only: it exposes only query and read tools and
never mutates the database. Write tools are out of scope for the current
server and are intentionally not registered here.
"""

from __future__ import annotations

from ..core.db import Database
from ..core.errors import ValidationError
from ..evidence.ledger import claim_evidence
from ..graph.community import communities_from_db
from ..graph.query import neighbourhood
from ..search.fts import fts_search
from ..search.hybrid import hybrid_search
from ..search.keyword import facet_by_source, keyword_search
from .protocol import ToolRegistry, ToolSpec


def _detect_communities(db: Database, *, detector: str, resolution: float) -> dict:
    """Dispatch community detection: both passes, or one detector alone.

    Ariadne ships with the platform under the same licence as everything else.
    When it is requested but cannot be imported, this falls back to Mnemosyne and
    records that honestly rather than failing, so the read-only surface keeps
    working.
    """
    if detector == "both":
        from ..graph.community import communities_combined

        return communities_combined(db, resolution=resolution)
    if detector == "ariadne":
        try:
            from ..ariadne import detect_communities_ariadne
        except Exception as e:  # refinement detector could not be imported
            result = communities_from_db(db, resolution=resolution)
            result["requested_detector"] = "ariadne"
            result["fallback"] = f"ariadne unavailable: {e!r}; used mnemosyne"
            return result
        return detect_communities_ariadne(db, resolution=resolution)
    return communities_from_db(db, resolution=resolution)


def _code_symbols(args: dict) -> dict:
    from ..code.parser import parse_source

    path = args.get("path")
    text = args.get("text")
    language = args.get("language")
    if not path and text is None:
        raise ValidationError("dkg.code.symbols requires 'path' or 'text'")
    parsed = parse_source(path or "inline", text=text, language=language)
    return {
        "path": parsed.path,
        "language": parsed.language,
        "symbols": [
            {"kind": s.kind, "name": s.name, "qualified": s.qualified, "start_line": s.start_line, "end_line": s.end_line}
            for s in parsed.symbols
        ],
    }


def _code_languages() -> dict:
    from ..code.parser import NOT_PARSED, language_inventory

    inventory = language_inventory()
    return {
        "languages": inventory,
        "total": len(inventory),
        "available": sorted(k for k, v in inventory.items() if v["available"]),
        # Reported alongside what IS parsed, so a format this build declines is
        # visible here rather than only discoverable by getting no symbols back.
        "not_parsed": dict(NOT_PARSED),
        "why": (
            "Fidelity says how a language is read. A 'grammar' language is parsed by a real "
            "Tree-sitter grammar; a 'composite' one is unwrapped and then parsed by another "
            "language's grammar; a 'fallback' language is extracted by a documented pattern "
            "extractor at a stated lower fidelity. Five languages report 'grammar' only when "
            "the optional 'code-bundle' extra is installed and 'fallback' without it, and this "
            "reports whichever is actually in force. Measured accuracy per language is in "
            "docs/BENCHMARKS.md."
        ),
    }


def _code_search(db: Database, args: dict) -> dict:
    from ..code.search import code_search

    return code_search(db, str(args["query"]), limit=int(args.get("limit", 10)))


def _code_impact(db: Database, args: dict) -> dict:
    from ..code.impact import blast_radius, blast_radius_for_file

    depth = int(args.get("depth", 3))
    max_nodes = int(args.get("max_nodes", 500))
    if args.get("file"):
        return blast_radius_for_file(db, str(args["file"]), depth=depth, max_nodes=max_nodes)
    if args.get("entity"):
        return blast_radius(db, str(args["entity"]), depth=depth, max_nodes=max_nodes)
    raise ValidationError("dkg.code.impact requires 'entity' or 'file'")


def _code_flow(db: Database, args: dict) -> dict:
    from ..code.flow import execution_flow

    return execution_flow(
        db,
        str(args["entity"]),
        depth=int(args.get("depth", 5)),
        max_nodes=int(args.get("max_nodes", 500)),
    )


def _code_hubs(db: Database, args: dict) -> dict:
    from ..code.centrality import hubs_and_bridges

    return hubs_and_bridges(
        db, limit=int(args.get("limit", 20)), max_nodes=int(args.get("max_nodes", 20000))
    )


def _code_coupling(db: Database, args: dict) -> dict:
    from ..code.coupling import unexpected_coupling

    return unexpected_coupling(
        db,
        limit=int(args.get("limit", 20)),
        resolution=float(args.get("resolution", 1.0)),
        max_nodes=int(args.get("max_nodes", 20000)),
    )


def _code_gaps(db: Database, args: dict) -> dict:
    from ..code.gaps import knowledge_gaps

    return knowledge_gaps(
        db,
        limit=int(args.get("limit", 20)),
        resolution=float(args.get("resolution", 1.0)),
        max_nodes=int(args.get("max_nodes", 20000)),
    )


def _code_questions(db: Database, args: dict) -> dict:
    from ..code.review import review_questions

    return review_questions(
        db,
        limit=int(args.get("limit", 20)),
        per_category=int(args.get("per_category", 5)),
        resolution=float(args.get("resolution", 1.0)),
        max_nodes=int(args.get("max_nodes", 20000)),
    )


def _code_architecture(db: Database, args: dict) -> dict:
    from ..code.architecture import architecture_map, render_markdown

    result = architecture_map(
        db,
        limit=int(args.get("limit", 40)),
        resolution=float(args.get("resolution", 1.0)),
        max_nodes=int(args.get("max_nodes", 20000)),
    )
    if args.get("format") == "markdown":
        # The Markdown rendering carries a Mermaid diagram, which is far easier
        # for a reading agent to reason about than the raw component tables.
        return {"markdown": render_markdown(result), "totals": result["totals"]}
    return result


def _graph_diff(db: Database, args: dict, snapshot_root) -> dict:
    """Compare two previously written snapshots.

    Reads two files and touches no database. Read-only with respect to the graph
    is not on its own enough here: the MCP surface is the trust boundary against
    an agent that may be acting on injected content, and a tool that reads any
    caller-named path would be a general filesystem read primitive behind that
    boundary. Both paths are therefore confined to the snapshot root and the read
    is size-capped. The CLI keeps the unconfined form, because there the user is
    choosing the path themselves.
    """
    from ..code.diff import diff_snapshots, load_snapshot

    before = args.get("before")
    after = args.get("after")
    if not before or not after:
        raise ValidationError("dkg.graph.diff requires 'before' and 'after' snapshot paths")
    return diff_snapshots(
        load_snapshot(str(before), root=snapshot_root),
        load_snapshot(str(after), root=snapshot_root),
    )


def build_read_registry(
    db: Database, *, snapshot_root=None, code_root=None, allowlist: list[str] | None = None
) -> ToolRegistry:
    """Build the read-only tool registry.

    ``snapshot_root`` confines the snapshot-diff tool to one directory. It
    defaults to the directory holding the database, which is where
    ``dkg graph-snapshot`` naturally writes, so the tool stays useful without
    becoming an arbitrary-file read behind the MCP trust boundary.

    ``code_root`` confines the tools that read source text (the rename preview)
    to the repository the graph was built from. It defaults to the parent of the
    DKG home, which is the repository root for a project-local home. Same
    reasoning as ``snapshot_root``: a caller-named path behind this boundary has
    to be confined, not merely read-only.

    ``allowlist`` restricts the served set to the named tools. It is applied
    last, after every tool is registered, so the allowlist is checked against
    the real registry and an unknown name is a reported error rather than a
    silently missing tool.
    """
    from pathlib import Path

    if snapshot_root is None:
        db_path = getattr(db, "path", None)
        snapshot_root = Path(db_path).resolve().parent if db_path else Path.cwd().resolve()
    if code_root is None:
        code_root = Path(snapshot_root).resolve().parent
    reg = ToolRegistry()

    reg.register(
        ToolSpec(
            name="dkg.status",
            description="Return database counts and app version.",
            input_schema={"type": "object", "properties": {}},
            handler=lambda _args: {
                "documents": db.fetchone("SELECT COUNT(*) AS n FROM documents;")["n"],
                "chunks": db.fetchone("SELECT COUNT(*) AS n FROM chunks;")["n"],
                "entities": db.fetchone("SELECT COUNT(*) AS n FROM entities;")["n"],
                "claims": db.fetchone("SELECT COUNT(*) AS n FROM claims;")["n"],
            },
        )
    )
    reg.register(
        ToolSpec(
            name="dkg.search",
            description="Hybrid search over chunks.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "required": ["query"],
            },
            handler=lambda args: {
                "results": hybrid_search(
                    db,
                    str(args["query"]),
                    limit=int(args.get("limit", 10)),
                    # The read-only surface must never write. Building the vector
                    # store on first use is a write, so it is off here and the
                    # search degrades to keyword-plus-FTS rather than indexing.
                    # Build it deliberately with `dkg reindex`.
                    auto_index=False,
                ),
                "why": (
                    "Read-only: the vector index is never built on demand here, "
                    "because that would write. If it has not been built with "
                    "`dkg reindex`, this degrades to keyword and FTS ranking."
                ),
            },
        )
    )
    reg.register(
        ToolSpec(
            name="dkg.search.keyword",
            description="Keyword search over chunks.",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
                "required": ["query"],
            },
            handler=lambda args: {
                "results": keyword_search(db, str(args["query"]), limit=int(args.get("limit", 10)))
            },
        )
    )
    reg.register(
        ToolSpec(
            name="dkg.search.fts",
            description="FTS5 search over chunks.",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
                "required": ["query"],
            },
            handler=lambda args: {
                "results": fts_search(db, str(args["query"]), limit=int(args.get("limit", 10)))
            },
        )
    )
    reg.register(
        ToolSpec(
            name="dkg.graph.neighbourhood",
            description="Return the graph neighbourhood around an entity.",
            input_schema={
                "type": "object",
                "properties": {
                    "entity": {"type": "string"},
                    "depth": {"type": "integer", "minimum": 0, "maximum": 5},
                    "max_nodes": {"type": "integer", "minimum": 1, "maximum": 1000},
                },
                "required": ["entity"],
            },
            handler=lambda args: neighbourhood(
                db,
                str(args["entity"]),
                depth=int(args.get("depth", 2)),
                max_nodes=int(args.get("max_nodes", 100)),
            ),
        )
    )
    reg.register(
        ToolSpec(
            name="dkg.graph.community",
            description=(
                "Detect communities over the entity graph by modularity "
                "optimization. The default runs BOTH detectors: a Mnemosyne base "
                "pass and an Ariadne refinement pass, returning whichever partition "
                "scores higher modularity and reporting both. Pass detector="
                "'mnemosyne' or 'ariadne' to run one alone. Read-only; advisory."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "detector": {"type": "string", "enum": ["both", "mnemosyne", "ariadne"]},
                    "resolution": {"type": "number", "minimum": 0.1, "maximum": 10.0},
                },
            },
            handler=lambda args: _detect_communities(
                db,
                detector=str(args.get("detector", "both")),
                resolution=float(args.get("resolution", 1.0)),
            ),
        )
    )
    reg.register(
        ToolSpec(
            name="dkg.evidence.claim",
            description="Fetch evidence for a claim by ID.",
            input_schema={
                "type": "object",
                "properties": {"claim_id": {"type": "string"}},
                "required": ["claim_id"],
            },
            handler=lambda args: claim_evidence(db, str(args["claim_id"])),
        )
    )
    reg.register(
        ToolSpec(
            name="dkg.facets.source",
            description="List sources with per-source chunk counts.",
            input_schema={"type": "object", "properties": {}},
            handler=lambda _args: {"sources": facet_by_source(db)},
        )
    )
    reg.register(
        ToolSpec(
            name="dkg.code.symbols",
            description="Parse a source file and return its code symbols (read-only, no DB write).",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "text": {"type": "string"},
                    "language": {"type": "string"},
                },
            },
            handler=_code_symbols,
        )
    )
    reg.register(
        ToolSpec(
            name="dkg.code.languages",
            description=(
                "List every language the source-code plane parses, how each one is read "
                "(grammar, composite, or documented fallback), and whether its grammar is "
                "available in this environment. Read-only; parses nothing."
            ),
            input_schema={"type": "object", "properties": {}},
            handler=lambda _args: _code_languages(),
        )
    )
    reg.register(
        ToolSpec(
            name="dkg.code.search",
            description="Search code symbols and code text.",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
                "required": ["query"],
            },
            handler=lambda args: _code_search(db, args),
        )
    )
    reg.register(
        ToolSpec(
            name="dkg.code.impact",
            description="Structural blast-radius for a code entity or file. Over-approximate; refinements deferred.",
            input_schema={
                "type": "object",
                "properties": {
                    "entity": {"type": "string"},
                    "file": {"type": "string"},
                    "depth": {"type": "integer", "minimum": 1, "maximum": 10},
                    "max_nodes": {"type": "integer", "minimum": 1, "maximum": 5000},
                    "context_savings": _savings,
                    "verify_savings": _verify_savings,
                },
            },
            handler=_saving(lambda args: _code_impact(db, args), code_root),
        )
    )
    reg.register(
        ToolSpec(
            name="dkg.code.flow",
            description="Structural execution-flow trace (forward call chains) from a code entity. Over-approximate; refinements deferred.",
            input_schema={
                "type": "object",
                "properties": {
                    "entity": {"type": "string"},
                    "depth": {"type": "integer", "minimum": 1, "maximum": 20},
                    "max_nodes": {"type": "integer", "minimum": 1, "maximum": 5000},
                },
                "required": ["entity"],
            },
            handler=lambda args: _code_flow(db, args),
        )
    )
    def _budgeted(handler):
        """Wrap a handler so a caller-supplied token_budget bounds its payload.

        Applied at the registry edge so every analysis tool honours the budget
        identically and no handler can forget to. Totals inside the payload are
        never rewritten, so a trimmed result stays visibly incomplete.
        """
        from ..context.pack import apply_budget

        def wrapped(args: dict) -> dict:
            raw = args.get("token_budget")
            budget = int(raw) if isinstance(raw, (int, float)) and int(raw) > 0 else None
            result = handler(args)
            return apply_budget(result, budget=budget) if isinstance(result, dict) else result

        return wrapped

    _token_budget = {
        "type": "integer",
        "minimum": 100,
        "maximum": 1000000,
        "description": "bound the payload to roughly this many tokens by trimming ranked lists",
    }
    _limit = {"type": "integer", "minimum": 1, "maximum": 500}
    _resolution = {"type": "number", "minimum": 0.1, "maximum": 10.0}
    _max_nodes = {"type": "integer", "minimum": 1, "maximum": 200000}
    _verbosity = {
        "type": "string",
        "enum": ["compact", "full"],
        "description": (
            "compact returns the ranked result without per-item evidence and "
            "explanation blocks; full returns everything. Compact never drops a "
            "result, only detail about it, and always keeps the caveat."
        ),
    }
    reg.register(
        ToolSpec(
            name="dkg.code.hubs",
            description=(
                "Most connected code symbols and architectural chokepoints "
                "(betweenness, degree, articulation points, bridge edges). "
                "Read-only; structural and advisory."
            ),
            input_schema={
                "type": "object",
                "properties": {"limit": _limit, "max_nodes": _max_nodes, "token_budget": _token_budget},
            },
            handler=_budgeted(lambda args: _code_hubs(db, args)),
        )
    )
    reg.register(
        ToolSpec(
            name="dkg.code.coupling",
            description=(
                "Score code edges that are surprising given the surrounding "
                "structure: crossing a community, crossing a language, or "
                "linking a peripheral symbol to a hub. Read-only; advisory "
                "heuristic and over-approximate."
            ),
            input_schema={
                "type": "object",
                "properties": {"limit": _limit, "resolution": _resolution, "max_nodes": _max_nodes, "token_budget": _token_budget},
            },
            handler=_budgeted(lambda args: _code_coupling(db, args)),
        )
    )
    reg.register(
        ToolSpec(
            name="dkg.code.gaps",
            description=(
                "Knowledge gaps in the code graph: isolated symbols, heavily "
                "called symbols with no test edge, and thin communities. "
                "Read-only; structural and advisory."
            ),
            input_schema={
                "type": "object",
                "properties": {"limit": _limit, "resolution": _resolution, "max_nodes": _max_nodes, "token_budget": _token_budget},
            },
            handler=_budgeted(lambda args: _code_gaps(db, args)),
        )
    )
    reg.register(
        ToolSpec(
            name="dkg.code.questions",
            description=(
                "Suggested review questions generated from the graph analysis, "
                "each carrying the evidence that prompted it. Deterministic "
                "templates, no model call. Read-only; questions are prompts for "
                "a reviewer, not findings."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "limit": _limit,
                    "per_category": {"type": "integer", "minimum": 1, "maximum": 100},
                    "resolution": _resolution,
                    "max_nodes": _max_nodes,
                    "token_budget": _token_budget,
                    "context_savings": _savings,
                    "verify_savings": _verify_savings,
                },
            },
            handler=_budgeted(_saving(lambda args: _code_questions(db, args), code_root)),
        )
    )
    reg.register(
        ToolSpec(
            name="dkg.code.architecture",
            description=(
                "Component-level architecture overview with coupling warnings "
                "(dependency cycles, high fan-in and fan-out, low cohesion, "
                "cross-language edges). Set format='markdown' for a rendered "
                "overview with a Mermaid diagram. Read-only; structural and advisory."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "format": {"type": "string", "enum": ["json", "markdown"]},
                    "limit": _limit,
                    "resolution": _resolution,
                    "max_nodes": _max_nodes,
                    "token_budget": _token_budget,
                    "context_savings": _savings,
                    "verify_savings": _verify_savings,
                },
            },
            handler=_budgeted(_saving(lambda args: _code_architecture(db, args), code_root)),
        )
    )
    reg.register(
        ToolSpec(
            name="dkg.graph.diff",
            description=(
                "Compare two code-graph snapshots written by 'dkg graph-snapshot': "
                "added and removed nodes and edges, changed edge confidence, and "
                "community membership changes. Reads the two snapshot files only "
                "and does not touch the database. Both paths are confined to the "
                "snapshot directory and the read is size-capped."
            ),
            input_schema={
                "type": "object",
                "properties": {"before": {"type": "string"}, "after": {"type": "string"}},
                "required": ["before", "after"],
            },
            handler=lambda args: _graph_diff(db, args, snapshot_root),
        )
    )
    _register_directed(reg, db, _budgeted, _token_budget, _verbosity)
    _register_analysis(reg, db, _budgeted, _token_budget, _limit, _resolution, _max_nodes, _verbosity)
    _register_orientation(reg, db, _verbosity, _token_budget)
    reg.register(
        ToolSpec(
            name="dkg.code.change",
            description=(
                "Structural summary of the repository this server is confined "
                "to, plus the advisory blast-radius of the files changed since a "
                "base ref. Over-approximate, like the edges it walks. Carries an "
                "estimated context-savings record. Read-only."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "base": {"type": "string"},
                    "depth": {"type": "integer", "minimum": 1, "maximum": 10},
                    "max_nodes": {"type": "integer", "minimum": 1, "maximum": 5000},
                    "context_savings": _savings,
                    "verify_savings": _verify_savings,
                },
            },
            handler=_saving(lambda args: _code_change(db, args, code_root), code_root),
        )
    )
    _register_inspection(reg, db, _limit, _max_nodes, _verbosity, code_root)
    _register_catalogue(reg, db, _limit, _verbosity)
    if allowlist:
        unknown = reg.restrict(allowlist)
        if unknown:
            raise ValidationError(
                "tool allowlist names tools that do not exist: " + ", ".join(unknown)
            )
    return reg


# -- verbosity ----------------------------------------------------------------

# Keys carrying per-item evidence or explanation. Compact verbosity drops these
# and nothing else, so a compact answer has the same RESULTS as a full one and
# only less detail about each. Dropping results to save tokens is what
# token_budget does, and the two are deliberately different levers.
_DETAIL_KEYS = ("evidence", "components", "excerpt", "members", "why_this", "path_detail")


def _apply_verbosity(payload: dict, verbosity: str) -> dict:
    """Strip per-item detail for a compact response, keeping every result.

    The top-level ``why`` is never dropped: it carries the over-approximation
    caveat, and a caveat that disappears when a caller asks for a smaller
    response is a caveat that does not work.
    """
    if verbosity != "compact" or not isinstance(payload, dict):
        return payload

    def strip(value):
        if isinstance(value, dict):
            return {k: strip(v) for k, v in value.items() if k not in _DETAIL_KEYS}
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value

    out = {k: (strip(v) if k != "why" else v) for k, v in payload.items()}
    out["verbosity"] = "compact"
    out["verbosity_note"] = (
        "per-item detail was omitted; every result is still present, and "
        f"the omitted keys are {list(_DETAIL_KEYS)}"
    )
    return out


def _verbose(handler):
    """Wrap a handler so every analysis tool honours verbosity identically."""

    def wrapped(args: dict) -> dict:
        result = handler(args)
        level = str(args.get("verbosity", "full"))
        return _apply_verbosity(result, level) if isinstance(result, dict) else result

    return wrapped


# -- context savings ----------------------------------------------------------

# Schemas for the two savings arguments, declared once so the four surfaces that
# carry a savings record cannot drift apart on what they accept.
_savings = {
    "type": "boolean",
    "description": "attach the estimated context-savings record (default true)",
}
_verify_savings = {
    "type": "boolean",
    "description": (
        "opt in to a real-tokenizer cross-check of the estimate, publishing the "
        "calibration error. Off by default: loading a tokenizer per answer is "
        "not worth it."
    ),
}


def _saving(handler, code_root):
    """Wrap a handler so its result carries an estimated savings record.

    The baseline is measured against the repository this server is confined to,
    never a caller-named path, for the same reason every other file read here is
    confined: the surface is the trust boundary.
    """
    from ..context.savings import attach_savings

    def wrapped(args: dict) -> dict:
        result: dict = handler(args)
        if not isinstance(result, dict):
            # A handler that returned something else is passed through
            # unchanged; there is nothing to attach a record to.
            return result
        return attach_savings(
            result,
            repo_root=code_root,
            verify=bool(args.get("verify_savings", False)),
            enabled=bool(args.get("context_savings", True)),
        )

    return wrapped


def _code_change(db: Database, args: dict, code_root) -> dict:
    """The change report for the confined repository, at a caller-named base ref."""
    from ..code.report import build_report

    return build_report(
        db,
        code_root,
        base=str(args["base"]) if args.get("base") else None,
        depth=int(args.get("depth", 3)),
        max_nodes=int(args.get("max_nodes", 500)),
    )


# -- named directed relationship queries --------------------------------------

# Each names the edge and the direction it follows. One tool per direction
# rather than one tool with a direction flag, because the QUESTION is different
# in each case: "who calls this" and "what does this call" are not one question
# with a parameter, and a caller that has to pass a direction has to know the
# edge model before it can ask anything.
_DIRECTED = (
    (
        "dkg.code.callers",
        "callers",
        "Symbols that CALL the named symbol (follows code:calls backwards).",
    ),
    (
        "dkg.code.callees",
        "callees",
        "Symbols the named symbol CALLS (follows code:calls forwards).",
    ),
    (
        "dkg.code.neighbours",
        "neighbours",
        "Symbols related to the named one in either direction, across calls, "
        "imports, and inheritance.",
    ),
)


def _register_directed(reg, db, budgeted, token_budget_schema, verbosity_schema) -> None:
    from ..context.slices import answer_slices

    def make(relation):
        def handler(args: dict) -> dict:
            return answer_slices(
                db,
                str(args["symbol"]),
                relation=relation,
                depth=int(args.get("depth", 1)),
                detail=str(args.get("detail", "signature")),
                token_budget=(
                    int(args["token_budget"]) if args.get("token_budget") else None
                ),
                max_nodes=int(args.get("max_nodes", 500)),
            )

        return handler

    for name, relation, description in _DIRECTED:
        reg.register(
            ToolSpec(
                name=name,
                description=description
                + " Returns answer-shaped node-level slices: one entry per SYMBOL, "
                "reduced to its declaration plus the lines bearing on the query, "
                "never whole files. Read-only; structural and over-approximate.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string"},
                        "depth": {"type": "integer", "minimum": 1, "maximum": 10},
                        "detail": {"type": "string", "enum": ["signature", "focused", "full"]},
                        "max_nodes": {"type": "integer", "minimum": 1, "maximum": 5000},
                        "token_budget": token_budget_schema,
                        "verbosity": verbosity_schema,
                    },
                    "required": ["symbol"],
                },
                handler=_verbose(make(relation)),
            )
        )

    # Inheritance and test coverage follow different predicates, so they are
    # their own queries rather than a relation parameter on the three above.
    def _edge_query(predicate: str, reverse: bool):
        def handler(args: dict) -> dict:
            return _directed_edges(
                db, str(args["symbol"]), predicate=predicate, reverse=reverse,
                limit=int(args.get("limit", 100)),
            )

        return handler

    for name, predicate, reverse, description in (
        (
            "dkg.code.implementations",
            "code:inherits",
            True,
            "Types that INHERIT FROM the named type (follows code:inherits backwards).",
        ),
        (
            "dkg.code.base_types",
            "code:inherits",
            False,
            "Types the named type INHERITS FROM (follows code:inherits forwards).",
        ),
        (
            "dkg.code.importers",
            "code:imports",
            True,
            "Modules that IMPORT the named module (follows code:imports backwards).",
        ),
        (
            "dkg.code.tests_for",
            "code:tested_by",
            False,
            "Tests that exercise the named symbol (follows code:tested_by forwards).",
        ),
    ):
        reg.register(
            ToolSpec(
                name=name,
                description=description
                + " Each result carries its three-tier edge confidence. Read-only.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
                        "verbosity": verbosity_schema,
                    },
                    "required": ["symbol"],
                },
                handler=_verbose(_edge_query(predicate, reverse)),
            )
        )

    # The framework vocabulary gets its own query for the same reason: a route
    # is not a call, and answering "what serves this endpoint" by filtering
    # calls would mean guessing which calls are really routes.
    def _framework_query(args: dict) -> dict:
        from ..code.model import FRAMEWORK_PREDICATES

        wanted = str(args.get("relation", "")).strip()
        if wanted and wanted not in FRAMEWORK_PREDICATES:
            raise ValidationError(
                f"unknown framework relation {wanted!r}; "
                f"expected one of {list(FRAMEWORK_PREDICATES)}"
            )
        relations = (wanted,) if wanted else FRAMEWORK_PREDICATES
        out: dict = {"symbol": args.get("symbol"), "relations": {}}
        for relation in relations:
            out["relations"][relation] = _directed_edges(
                db,
                str(args["symbol"]),
                predicate=f"code:{relation}",
                reverse=bool(args.get("reverse", False)),
                limit=int(args.get("limit", 100)),
            )["edges"]
        from ..code.model import PREDICATE_EXPLANATIONS

        out["vocabulary"] = {r: PREDICATE_EXPLANATIONS[r] for r in relations}
        out["why"] = (
            "Framework relations are recorded under their own predicates rather "
            "than flattened into calls and imports, because a route does not call "
            "its handler in anything the parser saw. Still structural and "
            "over-approximate: a route registered through a variable, or a "
            "template named at runtime, is not seen at all."
        )
        return out

    reg.register(
        ToolSpec(
            name="dkg.code.framework",
            description=(
                "Framework relations for a symbol: routes_to, renders, relates_to, "
                "configures, and dispatches. Read-only; structural and advisory."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "relation": {"type": "string"},
                    "reverse": {"type": "boolean"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
                    "verbosity": verbosity_schema,
                },
                "required": ["symbol"],
            },
            handler=_verbose(_framework_query),
        )
    )


def _directed_edges(
    db: Database, symbol: str, *, predicate: str, reverse: bool, limit: int
) -> dict:
    """Edges of one predicate in one direction, with their confidence tiers."""
    from ..code.model import PREDICATE_EXPLANATIONS, confidence_record

    row = db.fetchone(
        "SELECT entity_id, canonical FROM entities WHERE tenant_id='local' "
        "AND kind LIKE 'code:%' AND (entity_id=? OR canonical=?) LIMIT 1;",
        (symbol, symbol),
    )
    if row is None:
        return {
            "symbol": symbol,
            "found": False,
            "edges": [],
            "why": "no code entity with that name; nothing was guessed",
        }
    limit = max(1, min(int(limit), 1000))
    if reverse:
        sql = (
            "SELECT e.canonical, e.kind, r.weight FROM relationships r "
            "JOIN entities e ON e.entity_id = r.subject_id "
            "WHERE r.tenant_id='local' AND r.object_id=? AND r.predicate=? "
            "ORDER BY e.canonical LIMIT ?;"
        )
    else:
        sql = (
            "SELECT e.canonical, e.kind, r.weight FROM relationships r "
            "JOIN entities e ON e.entity_id = r.object_id "
            "WHERE r.tenant_id='local' AND r.subject_id=? AND r.predicate=? "
            "ORDER BY e.canonical LIMIT ?;"
        )
    rows = db.fetchall(sql, (row["entity_id"], predicate, limit + 1))
    truncated = len(rows) > limit
    edges = [
        {
            "canonical": r["canonical"],
            "kind": r["kind"],
            "confidence": confidence_record(r["weight"]),
        }
        for r in rows[:limit]
    ]
    bare = predicate.removeprefix("code:")
    return {
        "symbol": row["canonical"],
        "found": True,
        "predicate": predicate,
        "direction": "incoming" if reverse else "outgoing",
        "edges": edges,
        "totals": {"returned": len(edges), "truncated": truncated, "limit": limit},
        "why": (
            f"{PREDICATE_EXPLANATIONS.get(bare, bare)}. Edges are structural and "
            "name-based, so this over-approximates: a listed edge may not hold at "
            "runtime. Each carries its confidence tier."
        ),
    }


# -- analysis -----------------------------------------------------------------


def _register_analysis(
    reg, db, budgeted, token_budget_schema, limit_schema, resolution_schema,
    max_nodes_schema, verbosity_schema,
) -> None:
    from ..code.criticality import flow_criticality, traverse
    from ..context.slices import answer_slices

    reg.register(
        ToolSpec(
            name="dkg.code.slices",
            description=(
                "Answer-shaped node-level slices for a structural question: one "
                "entry per SYMBOL, reduced to its declaration plus the lines "
                "bearing on the seed, ranked and packed into a token budget. "
                "Returns code, but never a whole file. Read-only."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "relation": {
                        "type": "string",
                        "enum": ["impact", "callers", "callees", "flow", "neighbours"],
                    },
                    "depth": {"type": "integer", "minimum": 1, "maximum": 10},
                    "detail": {"type": "string", "enum": ["signature", "focused", "full"]},
                    "max_nodes": {"type": "integer", "minimum": 1, "maximum": 5000},
                    "token_budget": token_budget_schema,
                    "verbosity": verbosity_schema,
                },
                "required": ["symbol"],
            },
            handler=_verbose(
                lambda args: answer_slices(
                    db,
                    str(args["symbol"]),
                    relation=str(args.get("relation", "impact")),
                    depth=int(args.get("depth", 3)),
                    detail=str(args.get("detail", "focused")),
                    token_budget=int(args["token_budget"]) if args.get("token_budget") else None,
                    max_nodes=int(args.get("max_nodes", 500)),
                )
            ),
        )
    )
    reg.register(
        ToolSpec(
            name="dkg.code.traverse",
            description=(
                "Free-form graph traversal from any node, breadth-first or "
                "depth-first, bounded by BOTH a depth limit and a token budget. "
                "Reports which bound bit, because a cap on one dimension is not a "
                "bound. Read-only."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "order": {"type": "string", "enum": ["breadth", "depth"]},
                    "direction": {"type": "string", "enum": ["out", "in", "both"]},
                    "depth": {"type": "integer", "minimum": 1, "maximum": 20},
                    "max_nodes": {"type": "integer", "minimum": 1, "maximum": 20000},
                    "token_budget": token_budget_schema,
                    "verbosity": verbosity_schema,
                },
                "required": ["symbol"],
            },
            handler=_verbose(
                lambda args: traverse(
                    db,
                    str(args["symbol"]),
                    order=str(args.get("order", "breadth")),
                    direction=str(args.get("direction", "out")),
                    depth=int(args.get("depth", 3)),
                    token_budget=int(args["token_budget"]) if args.get("token_budget") else 2000,
                    max_nodes=int(args.get("max_nodes", 1000)),
                )
            ),
        )
    )
    reg.register(
        ToolSpec(
            name="dkg.code.criticality",
            description=(
                "Score every execution flow from an entry point by weighted "
                "criticality: depth, peak fan-in, files touched, mean edge "
                "confidence, and a bonus for being untested. Every weight and "
                "component is reported next to the total. Read-only; advisory."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "entry": {"type": "string"},
                    "depth": {"type": "integer", "minimum": 1, "maximum": 20},
                    "max_paths": {"type": "integer", "minimum": 1, "maximum": 500},
                    "max_nodes": max_nodes_schema,
                    "verbosity": verbosity_schema,
                },
                "required": ["entry"],
            },
            handler=_verbose(
                lambda args: flow_criticality(
                    db,
                    str(args["entry"]),
                    depth=int(args.get("depth", 6)),
                    max_paths=int(args.get("max_paths", 50)),
                    max_nodes=int(args.get("max_nodes", 2000)),
                )
            ),
        )
    )
    reg.register(
        ToolSpec(
            name="dkg.graph.community.split",
            description=(
                "Detect communities, then split any that hold more than a "
                "documented share of the graph by re-detecting inside them. A "
                "split is kept only when it measurably improves modularity; a "
                "rejected one is reported with its numbers. Community indices are "
                "arbitrary per-run labels, so never compare them across runs. "
                "Read-only."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "resolution": resolution_schema,
                    "oversize_share": {"type": "number", "minimum": 0.01, "maximum": 1.0},
                    "verbosity": verbosity_schema,
                },
            },
            handler=_verbose(lambda args: _split_communities(db, args)),
        )
    )
    reg.register(
        ToolSpec(
            name="dkg.code.review_context",
            description=(
                "Everything a reviewer needs about one symbol in a single call: "
                "what it is, who calls it, what it calls, whether anything tests "
                "it, its edge-confidence mix, and the review questions the graph "
                "would raise about it. Read-only; advisory, and the questions are "
                "prompts for a human rather than findings."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "depth": {"type": "integer", "minimum": 1, "maximum": 5},
                    "token_budget": token_budget_schema,
                    "verbosity": verbosity_schema,
                },
                "required": ["symbol"],
            },
            handler=_verbose(lambda args: _review_context(db, args)),
        )
    )
    reg.register(
        ToolSpec(
            name="dkg.code.impact_radius",
            description=(
                "Blast radius for a symbol or file with each impacted symbol "
                "ranked by a documented weighted score (distance, edge "
                "confidence, and fan-in), rather than returned as a flat set. "
                "Read-only; structural and over-approximate."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "depth": {"type": "integer", "minimum": 1, "maximum": 10},
                    "limit": limit_schema,
                    "max_nodes": {"type": "integer", "minimum": 1, "maximum": 5000},
                    "token_budget": token_budget_schema,
                    "verbosity": verbosity_schema,
                },
                "required": ["symbol"],
            },
            handler=_verbose(lambda args: _impact_radius(db, args)),
        )
    )
    reg.register(
        ToolSpec(
            name="dkg.code.confidence",
            description=(
                "The three-tier confidence profile of the code graph: how many "
                "edges are extracted, inferred, or ambiguous, per predicate, with "
                "what each tier means. Tells a caller how much of an answer over "
                "this graph rests on a guess. Read-only."
            ),
            input_schema={"type": "object", "properties": {"verbosity": verbosity_schema}},
            handler=_verbose(lambda _args: _confidence_profile(db)),
        )
    )


def _split_communities(db: Database, args: dict) -> dict:
    from ..graph.split import DEFAULT_OVERSIZE_SHARE, split_communities_from_db

    return split_communities_from_db(
        db,
        resolution=float(args.get("resolution", 1.0)),
        oversize_share=float(args.get("oversize_share", DEFAULT_OVERSIZE_SHARE)),
    )


def _review_context(db: Database, args: dict) -> dict:
    """One symbol, assembled from the queries a reviewer would run by hand."""
    from ..context.slices import answer_slices

    symbol = str(args["symbol"])
    depth = int(args.get("depth", 1))
    budget = int(args["token_budget"]) if args.get("token_budget") else 1500
    callers = answer_slices(
        db, symbol, relation="callers", depth=depth, detail="signature", token_budget=budget
    )
    if not callers.get("found"):
        return {
            "symbol": symbol,
            "found": False,
            "why": "no code entity with that name; nothing was guessed",
        }
    callees = answer_slices(
        db, symbol, relation="callees", depth=depth, detail="signature", token_budget=budget
    )
    tests = _directed_edges(db, symbol, predicate="code:tested_by", reverse=False, limit=50)
    return {
        "symbol": callers["seed"],
        "found": True,
        "callers": [s["canonical"] for s in callers["slices"] if s["distance"] > 0],
        "callees": [s["canonical"] for s in callees["slices"] if s["distance"] > 0],
        "tests": [e["canonical"] for e in tests["edges"]],
        "tested": bool(tests["edges"]),
        "definition": next(
            (s["excerpt"] for s in callers["slices"] if s["distance"] == 0), ""
        ),
        "questions": _review_questions_for(callers, callees, tests),
        "why": (
            "Assembled from the same read-only queries a reviewer would run by "
            "hand. The questions are prompts for a human, not findings. The "
            "underlying edges are structural and name-based, so the caller and "
            "callee sets over-approximate."
        ),
    }


def _review_questions_for(callers: dict, callees: dict, tests: dict) -> list[str]:
    """Deterministic template questions. No model call, no judgement claimed."""
    questions: list[str] = []
    caller_count = sum(1 for s in callers["slices"] if s["distance"] > 0)
    if not tests["edges"]:
        questions.append(
            "Nothing in the graph tests this symbol. Is that deliberate, or is the "
            "coverage edge simply not visible to a structural parse?"
        )
    if caller_count >= 5:
        questions.append(
            f"{caller_count} symbols reach this one. Is the contract stable enough "
            "for that many callers, and is a change here safe to make in one step?"
        )
    if caller_count == 0:
        questions.append(
            "No caller is visible in the graph. Is this an entry point, dead code, "
            "or reached by a mechanism a structural parse cannot see?"
        )
    ambiguous = [
        s for s in callers["slices"] if s["distance"] > 0 and s.get("confidence", 1.0) < 0.7
    ]
    if ambiguous:
        questions.append(
            f"{len(ambiguous)} of the incoming edges are ambiguous name matches. "
            "Are those callers real?"
        )
    return questions


def _impact_radius(db: Database, args: dict) -> dict:
    """Blast radius, ranked rather than flat.

    The weights are named constants reported in the result, and the ranking is
    deterministic, so a reader can disagree with the weighting instead of having
    to accept a single opaque number.
    """
    from ..context.slices import answer_slices

    W_DISTANCE, W_CONFIDENCE, W_FANIN = 0.5, 0.3, 0.2
    symbol = str(args["symbol"])
    result = answer_slices(
        db,
        symbol,
        relation="impact",
        depth=int(args.get("depth", 3)),
        detail="signature",
        token_budget=int(args["token_budget"]) if args.get("token_budget") else None,
        max_nodes=int(args.get("max_nodes", 500)),
    )
    if not result.get("found"):
        return {
            "symbol": symbol,
            "found": False,
            "why": "no code entity with that name; nothing was guessed",
        }
    impacted = [s for s in result["slices"] if s["distance"] > 0]
    fanins = sorted(float(s.get("elided_lines", 0)) for s in impacted)
    ranked = []
    for s in impacted:
        proximity = 1.0 / (1.0 + s["distance"])
        components = {
            "proximity": round(W_DISTANCE * proximity, 4),
            "edge_confidence": round(W_CONFIDENCE * float(s["confidence"]), 4),
            "size": round(
                W_FANIN * (_rank(float(s.get("elided_lines", 0)), fanins)), 4
            ),
        }
        ranked.append(
            {
                "canonical": s["canonical"],
                "kind": s["kind"],
                "distance": s["distance"],
                "score": round(sum(components.values()), 4),
                "components": components,
            }
        )
    ranked.sort(key=lambda r: (-r["score"], r["canonical"]))
    limit = max(1, min(int(args.get("limit", 50)), 500))
    return {
        "symbol": result["seed"],
        "found": True,
        "impacted": ranked[:limit],
        "totals": {
            "impacted": len(ranked),
            "returned": min(limit, len(ranked)),
            "truncated": len(ranked) > limit,
        },
        "weights": {"proximity": W_DISTANCE, "edge_confidence": W_CONFIDENCE, "size": W_FANIN},
        "why": (
            "Ranked by a weighted score whose components are reported next to the "
            "total, so the weighting can be disagreed with. Structural and "
            "over-approximate: a listed symbol may not truly be affected."
        ),
    }


def _rank(value: float, ordered: list[float]) -> float:
    if not ordered:
        return 0.0
    return sum(1 for v in ordered if v <= value) / len(ordered)


def _confidence_profile(db: Database) -> dict:
    from ..code.model import (
        CONFIDENCE_TIERS,
        PREDICATE_EXPLANATIONS,
        TIER_EXPLANATIONS,
        confidence_tier,
    )

    rows = db.fetchall(
        "SELECT predicate, weight, COUNT(*) AS n FROM relationships "
        "WHERE tenant_id='local' AND predicate LIKE 'code:%' "
        "GROUP BY predicate, weight ORDER BY predicate;"
    )
    per_predicate: dict[str, dict[str, int]] = {}
    totals = dict.fromkeys(CONFIDENCE_TIERS, 0)
    for r in rows:
        tier = confidence_tier(r["weight"])
        bucket = per_predicate.setdefault(r["predicate"], dict.fromkeys(CONFIDENCE_TIERS, 0))
        bucket[tier] += int(r["n"])
        totals[tier] += int(r["n"])
    total = sum(totals.values())
    return {
        "totals": totals,
        "edges": total,
        "share": {
            tier: (round(count / total, 4) if total else 0.0) for tier, count in totals.items()
        },
        "per_predicate": {
            predicate: {
                "counts": counts,
                "means": PREDICATE_EXPLANATIONS.get(predicate.removeprefix("code:"), ""),
            }
            for predicate, counts in sorted(per_predicate.items())
        },
        "tiers": TIER_EXPLANATIONS,
        "why": (
            "How much of this graph rests on a guess. An ambiguous edge is a "
            "candidate, not a fact, so an answer that leans on many of them "
            "over-approximates more than one that does not."
        ),
    }


# -- orientation, documentation, repositories, prompts, memory ----------------

# Reusable prompt templates for the recurring review workflows. Deterministic
# text, no model call: these are prompts a caller can run, not answers.
PROMPT_TEMPLATES = {
    "change-review": {
        "title": "Review a change set",
        "description": "Walk a diff through the graph before approving it.",
        "template": (
            "For each changed file, call dkg.code.impact_radius on the symbols it "
            "defines. Rank by score. For the top results call "
            "dkg.code.review_context and answer the questions it raises. Treat "
            "every result as advisory: the edges are structural and "
            "over-approximate, so confirm anything that would change your decision."
        ),
    },
    "architecture-map": {
        "title": "Map the architecture",
        "description": "Build a component-level picture with its coupling warnings.",
        "template": (
            "Call dkg.code.architecture with format='markdown' for the component "
            "map and its Mermaid diagram, then dkg.code.coupling for the edges "
            "that are surprising given the surrounding structure, then "
            "dkg.graph.community.split to check no community is so large it says "
            "nothing. Community indices are per-run labels; do not compare them "
            "across runs."
        ),
    },
    "guided-onboarding": {
        "title": "Orient in an unfamiliar repository",
        "description": "Find the entry points and the load-bearing symbols first.",
        "template": (
            "Call dkg.orient for the graph's shape and its highest-value entry "
            "points. For each entry point call dkg.code.criticality to see which "
            "flows matter, then dkg.code.slices with detail='signature' to read "
            "the shape of each without pulling whole files."
        ),
    },
    "risk-triage": {
        "title": "Triage risk before a release",
        "description": "Find what is both heavily depended on and untested.",
        "template": (
            "Call dkg.code.gaps for heavily called symbols with no test edge and "
            "dkg.code.hubs for the chokepoints. Cross-reference: a symbol in both "
            "lists is where a change is most likely to be both risky and "
            "unnoticed. Then dkg.code.confidence, to see how much of the graph "
            "these answers rested on a guess."
        ),
    },
}


def _register_orientation(reg, db, verbosity_schema, token_budget_schema) -> None:
    reg.register(
        ToolSpec(
            name="dkg.orient",
            description=(
                "A compact orientation for an unfamiliar graph: its shape, the "
                "highest-value entry points, the languages present, and the "
                "suggested next calls. One small call instead of six. Read-only."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                    "verbosity": verbosity_schema,
                },
            },
            handler=_verbose(lambda args: _orient(db, int(args.get("limit", 10)))),
        )
    )
    reg.register(
        ToolSpec(
            name="dkg.prompts.list",
            description=(
                "List the reusable prompt templates for the recurring review "
                "workflows. Deterministic text; these are prompts to run, not "
                "answers. Read-only."
            ),
            input_schema={"type": "object", "properties": {}},
            handler=lambda _args: {
                "prompts": [
                    {"name": name, "title": spec["title"], "description": spec["description"]}
                    for name, spec in sorted(PROMPT_TEMPLATES.items())
                ],
                "total": len(PROMPT_TEMPLATES),
            },
        )
    )
    reg.register(
        ToolSpec(
            name="dkg.prompts.get",
            description="Fetch one reusable prompt template by name. Read-only.",
            input_schema={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            handler=lambda args: _prompt(str(args["name"])),
        )
    )
    reg.register(
        ToolSpec(
            name="dkg.docs.section",
            description=(
                "Fetch a named section of the shipped documentation. Confined to "
                "the packaged docs directory and size-capped, because a tool that "
                "opened a caller-named path would be a filesystem read primitive "
                "behind the MCP trust boundary. Read-only."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "document": {"type": "string"},
                    "section": {"type": "string"},
                },
                "required": ["document"],
            },
            handler=lambda args: _docs_section(
                str(args["document"]), str(args.get("section", ""))
            ),
        )
    )
    reg.register(
        ToolSpec(
            name="dkg.repos.list",
            description=(
                "List every registered repository with its per-repository status. "
                "Read-only; reads the registry file and never writes it."
            ),
            input_schema={"type": "object", "properties": {}},
            handler=lambda _args: _repos_list(),
        )
    )
    reg.register(
        ToolSpec(
            name="dkg.repos.search",
            description=(
                "Search across EVERY registered repository, returning per-"
                "repository attribution and honouring the same bounds and token "
                "budget as the single-repository search. Each repository's "
                "database is opened without the migration runner, so searching "
                "never writes to one; a repository that cannot be searched is "
                "reported with its reason rather than dropped. Read-only."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                    "per_repo_limit": {"type": "integer", "minimum": 1, "maximum": 200},
                    "token_budget": token_budget_schema,
                },
                "required": ["query"],
            },
            handler=lambda args: _repos_search(args),
        )
    )
    reg.register(
        ToolSpec(
            name="dkg.memory.list",
            description=(
                "List the recorded answers held in the memory loop. Each is a "
                "document written when a question was answered, carrying the time "
                "and graph revision it came from. A recorded answer is not a live "
                "one. Read-only."
            ),
            input_schema={"type": "object", "properties": {}},
            handler=lambda _args: _memory_list(),
        )
    )


def _count(db: Database, sql: str) -> int:
    """A COUNT(*) that reports zero rather than indexing a row that is not there."""
    row = db.fetchone(sql)
    return int(row["n"]) if row is not None else 0


def _orient(db: Database, limit: int) -> dict:
    from ..code.parser import language_inventory

    counts = {
        "documents": _count(db, "SELECT COUNT(*) AS n FROM documents;"),
        "entities": _count(db, "SELECT COUNT(*) AS n FROM entities;"),
        "code_entities": _count(
            db, "SELECT COUNT(*) AS n FROM entities WHERE kind LIKE 'code:%';"
        ),
        "code_edges": _count(
            db, "SELECT COUNT(*) AS n FROM relationships WHERE predicate LIKE 'code:%';"
        ),
    }
    # An entry point is a symbol nothing calls that itself calls something: the
    # top of a chain. Structural, so it also catches genuinely dead code, and
    # that is said rather than hidden.
    entry_rows = db.fetchall(
        "SELECT e.canonical, e.kind, COUNT(r.relationship_id) AS outgoing "
        "FROM entities e JOIN relationships r ON r.subject_id = e.entity_id "
        "WHERE e.tenant_id='local' AND e.kind LIKE 'code:%' AND r.predicate='code:calls' "
        "AND e.entity_id NOT IN ("
        "  SELECT object_id FROM relationships WHERE tenant_id='local' AND predicate='code:calls'"
        ") GROUP BY e.canonical, e.kind ORDER BY outgoing DESC, e.canonical LIMIT ?;",
        (max(1, min(limit, 50)),),
    )
    inventory = language_inventory()
    return {
        "counts": counts,
        "entry_points": [
            {"canonical": r["canonical"], "kind": r["kind"], "calls_out": int(r["outgoing"])}
            for r in entry_rows
        ],
        "languages_available": sorted(k for k, v in inventory.items() if v["available"]),
        "suggested_next": [
            "dkg.code.criticality on an entry point, to see which flows matter",
            "dkg.code.architecture with format='markdown', for the component map",
            "dkg.code.gaps, for heavily used symbols with no test edge",
            "dkg.code.confidence, to see how much of this graph rests on a guess",
        ],
        "why": (
            "An entry point here is a symbol nothing calls that itself calls "
            "something. That is structural, so it also catches dead code and "
            "anything reached by a mechanism the parser cannot see, such as "
            "reflection or a framework dispatch. Advisory."
        ),
    }


def _prompt(name: str) -> dict:
    spec = PROMPT_TEMPLATES.get(name)
    if spec is None:
        raise ValidationError(
            f"unknown prompt {name!r}; known: {sorted(PROMPT_TEMPLATES)}"
        )
    return {"name": name, **spec}


# Documentation is served from the packaged docs directory only, and each read
# is size-capped. A caller-named path that escaped this root would be a general
# filesystem read behind the MCP trust boundary.
_DOCS_MAX_BYTES = 256 * 1024


def _docs_root():
    from pathlib import Path

    return (Path(__file__).resolve().parents[3] / "docs").resolve()


def _docs_section(document: str, section: str) -> dict:
    from pathlib import Path

    root = _docs_root()
    if not root.exists():
        return {"found": False, "why": "no packaged documentation directory in this install"}
    name = Path(document).name  # any directory part is discarded, not interpreted
    if not name.endswith(".md"):
        name += ".md"
    target = (root / name).resolve()
    # Resolved, then checked against the root: a symlink or a traversal that
    # pointed outside would otherwise pass a string check and fail an audit.
    if root not in target.parents or not target.is_file():
        return {
            "found": False,
            "available": sorted(p.name for p in root.glob("*.md")),
            "why": "that document is not in the packaged documentation directory",
        }
    if target.stat().st_size > _DOCS_MAX_BYTES:
        return {"found": False, "why": f"document exceeds the {_DOCS_MAX_BYTES} byte cap"}
    text = target.read_text(encoding="utf-8", errors="replace")
    if not section:
        return {"found": True, "document": name, "section": None, "text": text}
    wanted = section.strip().casefold().lstrip("# ")
    lines = text.splitlines()
    out: list[str] = []
    level = 0
    for line in lines:
        if line.startswith("#"):
            heading = line.lstrip("#").strip().casefold()
            if out:
                if len(line) - len(line.lstrip("#")) <= level:
                    break
            elif heading == wanted:
                level = len(line) - len(line.lstrip("#"))
                out.append(line)
                continue
        if out:
            out.append(line)
    if not out:
        return {
            "found": False,
            "document": name,
            "available_sections": [
                line.lstrip("#").strip() for line in lines if line.startswith("#")
            ],
            "why": "no section with that heading",
        }
    return {"found": True, "document": name, "section": section, "text": "\n".join(out)}


def _repos_list() -> dict:
    from ..core.config import load_config
    from ..watch.registry import Registry

    cfg = load_config()
    registry = Registry.in_home(cfg.home)
    repos = []
    for entry in registry.list():
        from pathlib import Path

        path = Path(entry.path)
        repos.append(
            {
                "name": entry.name,
                "path": entry.path,
                "exists": path.exists(),
                "is_git": (path / ".git").exists(),
            }
        )
    return {
        "repos": repos,
        "total": len(repos),
        "why": "Read from the registry file. Presence on disk is checked, not assumed.",
    }


def _memory_list() -> dict:
    from ..context.memory import list_answers
    from ..core.config import load_config

    cfg = load_config()
    answers = list_answers(cfg.home)
    return {
        "answers": [{"file": p.name, "bytes": p.stat().st_size} for p in answers],
        "total": len(answers),
        "why": (
            "Each is a recorded answer, not a live one: it was true of the graph at "
            "the revision it names. Re-run the query rather than relying on it."
        ),
    }


def _register_inspection(reg, db, limit_schema, max_nodes_schema, verbosity_schema, code_root) -> None:
    """Read-only inspection tools: dead code, oversized symbols, rename preview.

    The rename PREVIEW is here because it only reads. Applying a rename is not,
    and never will be: it writes source, and the MCP surface is the boundary
    against an agent acting on text it was fed. Applying stays on the command
    line, where a human types the confirmation.
    """
    from ..code.deadcode import dead_code_candidates
    from ..code.refactor import refactor_suggestions
    from ..code.rename import preview_rename
    from ..code.risk import change_risk
    from ..code.size import large_symbols

    reg.register(
        ToolSpec(
            name="dkg.code.refactor",
            description=(
                "Refactoring SUGGESTIONS derived from the community structure "
                "and the coupling signals: moves, splits, merges, and "
                "decouplings. Each names the symbols involved, the measurement "
                "that produced it, and its own reason for possibly being wrong. "
                "Worded as suggestions because they are prompts for a human, not "
                "findings. Read-only."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "limit": limit_schema,
                    "per_kind": {"type": "integer", "minimum": 1, "maximum": 100},
                    "resolution": {"type": "number", "minimum": 0.1, "maximum": 10.0},
                    "max_nodes": max_nodes_schema,
                    "verbosity": verbosity_schema,
                },
            },
            handler=_verbose(
                lambda args: refactor_suggestions(
                    db,
                    limit=int(args.get("limit", 20)),
                    per_kind=int(args.get("per_kind", 5)),
                    resolution=float(args.get("resolution", 1.0)),
                    max_nodes=int(args.get("max_nodes", 20000)),
                )
            ),
        )
    )
    reg.register(
        ToolSpec(
            name="dkg.code.risk",
            description=(
                "Advisory risk score in 0 to 1 for a change set given as files, "
                "symbols, or both. Every factor is normalised against THIS "
                "graph's own distribution and reported with its contribution, "
                "which sums exactly to the score; the level cuts are derived the "
                "same way and published. The git change-frequency signal is "
                "opt-in, reported separately, can only raise a score, and reads "
                "history from the repository root this server is confined to, "
                "never a caller-named path. Read-only; advisory."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "files": {"type": "array", "items": {"type": "string"}},
                    "symbols": {"type": "array", "items": {"type": "string"}},
                    "with_churn": {"type": "boolean"},
                    "churn_commits": {"type": "integer", "minimum": 1, "maximum": 5000},
                    "limit": limit_schema,
                    "max_nodes": max_nodes_schema,
                    "verbosity": verbosity_schema,
                },
            },
            handler=_verbose(
                lambda args: change_risk(
                    db,
                    files=args.get("files"),
                    symbols=args.get("symbols"),
                    # Confined: the churn signal reads git history under the
                    # server's own root, never a path the caller names.
                    repo=code_root,
                    with_churn=bool(args.get("with_churn", False)),
                    churn_commits=int(args.get("churn_commits", 500)),
                    limit=int(args.get("limit", 50)),
                    max_nodes=int(args.get("max_nodes", 20000)),
                )
            ),
        )
    )
    reg.register(
        ToolSpec(
            name="dkg.code.dead",
            description=(
                "Candidate dead code: definitions with no inbound reference edge "
                "and no entry-point evidence. ADVISORY and over-approximate; the "
                "known false-positive sources (dynamic dispatch, reflection, "
                "framework registration, exported public interface) are named in "
                "the result. Read-only."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "include_modules": {"type": "boolean"},
                    "limit": limit_schema,
                    "max_nodes": max_nodes_schema,
                    "verbosity": verbosity_schema,
                },
            },
            handler=_verbose(
                lambda args: dead_code_candidates(
                    db,
                    include_modules=bool(args.get("include_modules", False)),
                    limit=int(args.get("limit", 50)),
                    max_nodes=int(args.get("max_nodes", 20000)),
                )
            ),
        )
    )
    reg.register(
        ToolSpec(
            name="dkg.code.large",
            description=(
                "Symbols whose recorded line span is at least min_lines, "
                "filterable by kind and path prefix. The threshold is the "
                "caller's; this graph's own length distribution is reported "
                "alongside so it can be placed. Read-only."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "min_lines": {"type": "integer", "minimum": 1, "maximum": 100000},
                    "kinds": {"type": "array", "items": {"type": "string"}},
                    "path_prefix": {"type": "string"},
                    "limit": limit_schema,
                    "max_nodes": max_nodes_schema,
                    "verbosity": verbosity_schema,
                },
                "required": ["min_lines"],
            },
            handler=_verbose(
                lambda args: large_symbols(
                    db,
                    min_lines=int(args["min_lines"]),
                    kinds=args.get("kinds"),
                    path_prefix=str(args["path_prefix"]) if args.get("path_prefix") else None,
                    limit=int(args.get("limit", 50)),
                    max_nodes=int(args.get("max_nodes", 20000)),
                )
            ),
        )
    )
    reg.register(
        ToolSpec(
            name="dkg.code.rename.preview",
            description=(
                "Preview a symbol rename as a read-only edit list: every file, "
                "line, and reference that would change, with ambiguous "
                "occurrences and occurrences inside comments or strings reported "
                "separately rather than included. Writes nothing and applies "
                "nothing; applying is command-line only by design. Reads are "
                "confined to the repository root and capped."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "new_name": {"type": "string"},
                    "max_nodes": max_nodes_schema,
                    "verbosity": verbosity_schema,
                },
                "required": ["symbol", "new_name"],
            },
            handler=_verbose(
                lambda args: preview_rename(
                    db,
                    str(args["symbol"]),
                    str(args["new_name"]),
                    repo_root=code_root,
                    max_nodes=int(args.get("max_nodes", 20000)),
                )
            ),
        )
    )


def _register_catalogue(reg, db, limit_schema, verbosity_schema) -> None:
    """Read-only readers over the precomputed catalogue and summaries.

    These read a small derived table rather than walking the graph, which is the
    whole reason the post-processing stage writes them. Every one reports its
    source and whether the row is current, so a stale answer is identifiable as
    stale rather than served as fresh. RUNNING the stage is a write and is
    therefore command-line only, like every other write in this project.
    """
    from ..code.catalogue import (
        community_summary,
        flows_affected_by,
        get_flow,
        list_flows,
        symbol_risk,
    )

    reg.register(
        ToolSpec(
            name="dkg.code.flows",
            description=(
                "List the catalogued execution flows in ranked order. Read from "
                "the precomputed catalogue, not traced live. Reports whether the "
                "catalogue is current for this graph; when nothing has been "
                "precomputed it says so rather than returning empty. Read-only."
            ),
            input_schema={
                "type": "object",
                "properties": {"limit": limit_schema, "verbosity": verbosity_schema},
            },
            handler=_verbose(lambda args: list_flows(db, limit=int(args.get("limit", 50)))),
        )
    )
    reg.register(
        ToolSpec(
            name="dkg.code.flow.get",
            description=(
                "Retrieve one catalogued flow by name or identifier, with its "
                "ordered steps. Structural and over-approximate like the call "
                "edges it rests on. Read-only."
            ),
            input_schema={
                "type": "object",
                "properties": {"flow": {"type": "string"}, "verbosity": verbosity_schema},
                "required": ["flow"],
            },
            handler=_verbose(lambda args: get_flow(db, str(args["flow"]))),
        )
    )
    reg.register(
        ToolSpec(
            name="dkg.code.flows.affected",
            description=(
                "Which catalogued flows pass through a changed file set. An "
                "index lookup over the catalogue rather than a re-trace of every "
                "entry point. Over-approximate in both directions and says so. "
                "Read-only."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "files": {"type": "array", "items": {"type": "string"}},
                    "limit": limit_schema,
                    "verbosity": verbosity_schema,
                },
                "required": ["files"],
            },
            handler=_verbose(
                lambda args: flows_affected_by(
                    db, args.get("files") or [], limit=int(args.get("limit", 50))
                )
            ),
        )
    )
    reg.register(
        ToolSpec(
            name="dkg.code.communities",
            description=(
                "Precomputed per-community summaries: members, files, internal "
                "and external edges, density, and entry points. Community "
                "indices are arbitrary per-run labels; never compare one across "
                "runs. Read-only."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "community_index": {"type": "integer"},
                    "limit": limit_schema,
                    "verbosity": verbosity_schema,
                },
            },
            handler=_verbose(
                lambda args: community_summary(
                    db,
                    int(args["community_index"]) if args.get("community_index") is not None else None,
                    limit=int(args.get("limit", 50)),
                )
            ),
        )
    )
    reg.register(
        ToolSpec(
            name="dkg.code.risk.index",
            description=(
                "The precomputed per-symbol structural risk index, highest "
                "first, or one symbol by canonical name. Structural factors "
                "only: the opt-in git churn signal is never precomputed. "
                "Read-only; advisory."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "limit": limit_schema,
                    "verbosity": verbosity_schema,
                },
            },
            handler=_verbose(
                lambda args: symbol_risk(
                    db,
                    str(args["symbol"]) if args.get("symbol") else None,
                    limit=int(args.get("limit", 50)),
                )
            ),
        )
    )


def _repos_search(args: dict) -> dict:
    """Search every registered repository, merged and attributed."""
    from ..search.federated import search_registered

    return search_registered(
        str(args["query"]),
        limit=int(args.get("limit", 20)),
        per_repo_limit=int(args.get("per_repo_limit", 10)),
        token_budget=int(args["token_budget"]) if args.get("token_budget") else None,
    )

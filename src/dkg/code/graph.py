"""Resolve code references into edges and write the code graph to the shared store.

Reference resolution is name-based and deterministic; every edge carries a
confidence from the documented heuristic in ``model``. Nodes go into the shared
``entities`` table, edges into ``relationships`` (confidence in ``weight``),
source text into ``chunks`` so code search works, with provenance envelopes. No
parallel store is created.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone

from ..core.audit import AuditEntry, AuditLog
from ..core.db import Database
from ..core.ids import content_id
from ..core.provenance import ProvenanceEnvelope, record_provenance
from .model import (
    CONF_DEFINES,
    CONF_INHERIT_NAME,
    CONF_INHERIT_RESOLVED,
    CONF_NAME_MATCH,
    CONF_RESOLVED,
    CONF_TESTED_BY,
    CONF_TYPE_RESOLVED,
    FALLBACK_CONFIDENCE_FACTOR,
    FIDELITY_FALLBACK,
    FRAMEWORK_PREDICATES,
    ParsedFile,
    edge_predicate,
    entity_kind,
)

_DEFINABLE = ("function", "method", "class", "type", "test")


@dataclass
class ResolvedEdge:
    from_qualified: str
    predicate: str
    to_qualified: str
    confidence: float


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_edges(
    parsed_files: list[ParsedFile],
    resolutions: dict[tuple[str, str], str] | None = None,
    aliases=None,
) -> list[ResolvedEdge]:
    """Resolve references into edges.

    When ``resolutions`` maps a (caller_qualified, callee_name) to a single
    callee, that call reference becomes one type-resolved edge and the ambiguous
    name-match fan-out is suppressed. With no resolutions the behaviour is the
    original name-based structural resolution.

    When ``aliases`` is a loaded compiler configuration, an import specifier it
    maps to a real in-repository module resolves to that module instead of
    falling through to stem matching. The project declared the mapping, so it is
    a stronger fact than a name that happens to match, and it is tried first.
    """
    resolutions = resolutions or {}
    by_qualified: dict[str, str] = {}  # qualified -> kind
    name_index: dict[str, set[str]] = {}  # short name -> {qualified}
    module_by_stem: dict[str, set[str]] = {}
    module_names: set[str] = set()  # module canonicals, for alias resolution
    for pf in parsed_files:
        for s in pf.symbols:
            by_qualified[s.qualified] = s.kind
            if s.kind in _DEFINABLE:
                name_index.setdefault(s.name, set()).add(s.qualified)
            if s.kind == "module":
                stem = s.name.rsplit(".", 1)[0]
                module_by_stem.setdefault(stem, set()).add(s.qualified)
                module_names.add(s.qualified)

    # Every symbol a fallback-parsed file produced, so an edge leaving one can be
    # scored below an edge the grammar path produced.
    fallback_sources: set[str] = set()
    for pf in parsed_files:
        if pf.fidelity == FIDELITY_FALLBACK:
            fallback_sources.add(pf.path)
            fallback_sources.update(s.qualified for s in pf.symbols)

    edges: list[ResolvedEdge] = []
    seen: set[tuple[str, str, str]] = set()

    def add(frm: str, pred: str, to: str, conf: float) -> None:
        if frm == to:
            return
        key = (frm, pred, to)
        if key in seen:
            return
        seen.add(key)
        if frm in fallback_sources:
            # A pattern-matched reference is weaker evidence than a parsed one.
            conf = round(conf * FALLBACK_CONFIDENCE_FACTOR, 4)
        edges.append(ResolvedEdge(frm, pred, to, conf))

    # Structural containment: parent defines child (certain by construction).
    for pf in parsed_files:
        for s in pf.symbols:
            if s.parent and s.parent in by_qualified:
                add(s.parent, "defines", s.qualified, CONF_DEFINES)

    # Name-based reference resolution.
    for pf in parsed_files:
        for r in pf.references:
            targets = name_index.get(r.name, set())
            if r.kind == "calls":
                resolved = resolutions.get((r.from_qualified, r.name))
                if resolved is not None and resolved in by_qualified:
                    # Type-aware resolution: one high-confidence edge replaces the
                    # ambiguous name-match fan-out for this reference.
                    add(r.from_qualified, "calls", resolved, CONF_TYPE_RESOLVED)
                    tested_targets = [resolved]
                else:
                    cands = [t for t in targets if by_qualified[t] in ("function", "method")]
                    if len(cands) == 1:
                        add(r.from_qualified, "calls", cands[0], CONF_RESOLVED)
                    elif len(cands) > 1:
                        for t in cands:
                            add(r.from_qualified, "calls", t, CONF_NAME_MATCH)
                    tested_targets = cands
                if by_qualified.get(r.from_qualified) == "test":
                    for t in tested_targets:
                        add(t, "tested_by", r.from_qualified, CONF_TESTED_BY)
            elif r.kind == "inherits":
                cands = [t for t in targets if by_qualified[t] in ("class", "type")]
                if len(cands) == 1:
                    add(r.from_qualified, "inherits", cands[0], CONF_INHERIT_RESOLVED)
                elif len(cands) > 1:
                    for t in cands:
                        add(r.from_qualified, "inherits", t, CONF_INHERIT_NAME)
            elif r.kind == "imports":
                aliased = _alias_target(aliases, r.name, module_names)
                if aliased is not None:
                    # The project's own compiler configuration says this
                    # specifier is that module. One edge, no fan-out.
                    add(r.from_qualified, "imports", aliased, CONF_RESOLVED)
                    continue
                icands = set(module_by_stem.get(r.name, set()))
                icands |= {t for t in name_index.get(r.name, set()) if by_qualified[t] in ("class", "type", "function")}
                cand_list = list(icands)
                if len(cand_list) == 1:
                    add(r.from_qualified, "imports", cand_list[0], CONF_RESOLVED)
                elif len(cand_list) > 1:
                    for t in cand_list:
                        add(r.from_qualified, "imports", t, CONF_NAME_MATCH)
            elif r.kind in FRAMEWORK_PREDICATES:
                # Framework relations resolve by name like the rest, but keep
                # their own predicate rather than being flattened into calls or
                # imports. A route does not call its handler in anything the
                # parser saw; the framework does, at runtime, from a URL. Losing
                # that distinction is what makes "what serves this endpoint"
                # unanswerable without guessing which imports are really renders.
                fcands = sorted(targets)
                if len(fcands) == 1:
                    add(r.from_qualified, r.kind, fcands[0], CONF_RESOLVED)
                elif len(fcands) > 1:
                    for t in fcands:
                        add(r.from_qualified, r.kind, t, CONF_NAME_MATCH)
    return edges


def _alias_target(aliases, specifier: str, module_names: set[str]) -> str | None:
    """The module an alias configuration maps this specifier to, if any.

    Isolated here so a configuration that is absent, empty, or unreadable costs
    one attribute check rather than changing the shape of the caller.
    """
    if aliases is None or not getattr(aliases, "present", False):
        return None
    from .aliases import resolve_specifier

    return resolve_specifier(specifier, aliases, module_names)


def _file_entities_prefixes(path: str) -> tuple[str, str]:
    # A file's entities are the module (canonical == path) and symbols
    # (canonical LIKE 'path::%').
    return path, f"{path}::%"


def _delete_file_graph(db: Database, tenant_id: str, path: str) -> None:
    exact, like = _file_entities_prefixes(path)
    ids = [
        r["entity_id"]
        for r in db.fetchall(
            "SELECT entity_id FROM entities WHERE tenant_id=? AND (canonical=? OR canonical LIKE ?);",
            (tenant_id, exact, like),
        )
    ]
    if ids:
        # The interpolated text is only a "?,?,?" placeholder list sized to the
        # id count; every value is bound as a parameter. Built as a variable so
        # it is not an f-string passed straight into execute().
        placeholders = ",".join("?" * len(ids))
        rel_sql = (
            "DELETE FROM relationships WHERE tenant_id=? "
            f"AND (subject_id IN ({placeholders}) OR object_id IN ({placeholders}));"
        )
        db.execute(rel_sql, (tenant_id, *ids, *ids))
        ent_sql = f"DELETE FROM entities WHERE entity_id IN ({placeholders});"
        db.execute(ent_sql, tuple(ids))
    doc = db.fetchone(
        "SELECT document_id FROM documents WHERE tenant_id=? AND format LIKE 'code:%' AND json_extract(metadata_json,'$.path')=?;",
        (tenant_id, path),
    )
    if doc:
        db.execute("DELETE FROM chunks WHERE document_id=?;", (doc["document_id"],))
        db.execute("DELETE FROM documents WHERE document_id=?;", (doc["document_id"],))


def write_code_graph(
    db: Database,
    parsed_files: list[ParsedFile],
    texts: dict[str, str],
    *,
    source_uri: str,
    tenant_id: str = "local",
    audit_path=None,
    replace_paths: set[str] | None = None,
    resolutions: dict[tuple[str, str], str] | None = None,
    aliases=None,
    extra_edges: list[tuple[str, str, str]] | None = None,
) -> dict:
    edges = resolve_edges(parsed_files, resolutions, aliases)
    # Edges whose BOTH endpoints are already known exactly, so they bypass name
    # matching rather than being turned into a fan-out by it. The configuration
    # bindings are the case this exists for: the key and the symbol that reads
    # it are both pinned by the line the binding sits on.
    for subject, predicate, obj in extra_edges or ():
        edges.append(ResolvedEdge(subject, predicate, obj, CONF_RESOLVED))
    src_id = content_id("src", tenant_id, source_uri)
    nodes_added = 0
    edges_added = 0
    files_added = 0

    with db.transaction():
        db.execute(
            "INSERT OR IGNORE INTO sources(source_id, tenant_id, kind, uri, display_name, added_at, metadata_json) VALUES (?,?,?,?,?,?,?);",
            (src_id, tenant_id, "code-repo", source_uri, source_uri, _now(), json.dumps({"plane": "code"})),
        )
        if replace_paths:
            for path in replace_paths:
                _delete_file_graph(db, tenant_id, path)

        for pf in parsed_files:
            write_file = pf.path in texts
            doc_id = None
            if write_file:
                text = texts[pf.path]
                doc_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                doc_id = content_id("doc", src_id, pf.path, doc_hash)
                db.execute(
                    """
                    INSERT OR IGNORE INTO documents(
                        document_id, source_id, tenant_id, format, content_sha256,
                        byte_length, ingested_at, version, metadata_json, supersedes)
                    VALUES (?,?,?,?,?,?,?,?,?,?);
                    """,
                    (
                        doc_id,
                        src_id,
                        tenant_id,
                        f"code:{pf.language}",
                        doc_hash,
                        len(text.encode("utf-8")),
                        _now(),
                        1,
                        json.dumps(
                            {
                                "path": pf.path,
                                "language": pf.language,
                                "fidelity": pf.fidelity,
                                "file_sha256": doc_hash,
                                # Persist references so an incremental update can
                                # rehydrate unchanged files and rebuild inbound
                                # cross-file edges without re-parsing them.
                                "references": [[r.from_qualified, r.kind, r.name] for r in pf.references],
                            }
                        ),
                        None,
                    ),
                )
                files_added += 1
            # Entities are always ensured present (idempotent); reconstructed
            # index-only files (not in texts) contribute their symbols to the
            # cross-file reference index without rewriting documents or chunks.
            for ordi, s in enumerate(pf.symbols):
                eid = content_id("ent", tenant_id, entity_kind(s.kind), s.qualified)
                db.execute(
                    "INSERT OR IGNORE INTO entities(entity_id, tenant_id, kind, canonical, display, metadata_json) VALUES (?,?,?,?,?,?);",
                    (
                        eid,
                        tenant_id,
                        entity_kind(s.kind),
                        s.qualified,
                        s.name,
                        json.dumps(
                            {
                                "path": pf.path,
                                "language": pf.language,
                                "start_line": s.start_line,
                                "end_line": s.end_line,
                                # How the symbol was found, so a consumer can
                                # tell a parsed symbol from a pattern-matched
                                # one without inferring it from an edge weight.
                                "fidelity": pf.fidelity,
                            }
                        ),
                    ),
                )
                nodes_added += 1
                if write_file and s.text and doc_id is not None:
                    ch_id = content_id("chunk", doc_id, s.qualified, str(s.start_line))
                    db.execute(
                        "INSERT OR IGNORE INTO chunks(chunk_id, document_id, tenant_id, "
                        "ord, text, text_sha256, start_offset, end_offset) VALUES (?,?,?,?,?,?,?,?);",
                        (
                            ch_id,
                            doc_id,
                            tenant_id,
                            ordi,
                            s.text,
                            hashlib.sha256(s.text.encode("utf-8")).hexdigest(),
                            s.start_line,
                            s.end_line,
                        ),
                    )
            if write_file and doc_id is not None:
                record_provenance(
                    db,
                    ProvenanceEnvelope(
                        subject_kind="document",
                        subject_id=doc_id,
                        actor="user_local",
                        method="code-parse",
                        inputs={"path": pf.path, "language": pf.language},
                        tenant_id=tenant_id,
                    ),
                )

        for e in edges:
            sid = _entity_id_for(db, tenant_id, e.from_qualified)
            oid = _entity_id_for(db, tenant_id, e.to_qualified)
            if not sid or not oid:
                continue
            rel_id = content_id("rel", sid, edge_predicate(e.predicate), oid)
            db.execute(
                """
                INSERT OR IGNORE INTO relationships(relationship_id, tenant_id, subject_id, predicate, object_id, support, weight, evidence_json, metadata_json)
                VALUES (?,?,?,?,?,?,?,?,?);
                """,
                (
                    rel_id,
                    tenant_id,
                    sid,
                    edge_predicate(e.predicate),
                    oid,
                    "supports",
                    e.confidence,
                    json.dumps([]),
                    json.dumps({"heuristic": "name-based", "confidence": e.confidence}),
                ),
            )
            edges_added += 1

        record_provenance(
            db,
            ProvenanceEnvelope(
                subject_kind="source",
                subject_id=src_id,
                actor="user_local",
                method="code-ingest",
                inputs={"uri": source_uri, "files": files_added},
                tenant_id=tenant_id,
            ),
        )

    AuditLog(db, audit_path).record(
        AuditEntry(
            action="code.ingest",
            outcome="ok",
            actor="user_local",
            subject_kind="source",
            subject_id=src_id,
            details={"files": files_added, "nodes": nodes_added, "edges": edges_added},
        )
    )
    return {
        "source_id": src_id,
        "files": files_added,
        "nodes": nodes_added,
        "edges": edges_added,
    }


def _entity_id_for(db: Database, tenant_id: str, qualified: str) -> str | None:
    row = db.fetchone(
        "SELECT entity_id FROM entities WHERE tenant_id=? AND canonical=? AND kind LIKE 'code:%' LIMIT 1;",
        (tenant_id, qualified),
    )
    return row["entity_id"] if row else None

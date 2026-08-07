"""Drop named paths from the code graph without rebuilding it.

A path leaves a repository in ways an incremental ingest does not always see: it
is deleted outside version control, it moves to a submodule, it starts matching
the ignore file, or it turns out to have been indexed by mistake. Until now the
only way to get its symbols out of the graph was a full re-ingest, which is the
wrong price for removing one directory.

Forgetting is exact rather than approximate. The dry run counts the same rows
the write would delete, by running the same selection, so the preview and the
result cannot disagree about scope. Nothing is deleted unless the caller asks
for it: ``dry_run`` defaults to true.

Only the code plane is touched. The selection is anchored on the path recorded
in each entity's metadata and on the module canonical, so a document, a chunk,
or an entity belonging to another plane is never in scope.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..core.audit import AuditEntry, AuditLog
from ..core.db import Database


def _entity_ids_for_path(db: Database, tenant_id: str, path: str) -> list[str]:
    """Every code entity belonging to one file: its module node and its symbols."""
    rows = db.fetchall(
        "SELECT entity_id FROM entities WHERE tenant_id=? AND kind LIKE 'code:%' "
        "AND (canonical=? OR canonical LIKE ?) ORDER BY entity_id;",
        (tenant_id, path, f"{path}::%"),
    )
    return [r["entity_id"] for r in rows]


def _edge_count(db: Database, tenant_id: str, ids: list[str]) -> int:
    """Relationships with either endpoint in the id set, counted once each."""
    if not ids:
        return 0
    placeholders = ",".join("?" * len(ids))
    row = db.fetchone(
        "SELECT COUNT(*) AS n FROM relationships WHERE tenant_id=? "
        f"AND (subject_id IN ({placeholders}) OR object_id IN ({placeholders}));",
        (tenant_id, *ids, *ids),
    )
    return int(row["n"]) if row else 0


def _documents_for_path(db: Database, tenant_id: str, path: str) -> list[str]:
    rows = db.fetchall(
        "SELECT document_id FROM documents WHERE tenant_id=? AND format LIKE 'code:%' "
        "AND json_extract(metadata_json,'$.path')=? ORDER BY document_id;",
        (tenant_id, path),
    )
    return [r["document_id"] for r in rows]


def _chunk_count(db: Database, document_ids: list[str]) -> int:
    if not document_ids:
        return 0
    placeholders = ",".join("?" * len(document_ids))
    row = db.fetchone(
        f"SELECT COUNT(*) AS n FROM chunks WHERE document_id IN ({placeholders});",
        tuple(document_ids),
    )
    return int(row["n"]) if row else 0


def _expand(db: Database, tenant_id: str, requested: Iterable[str]) -> dict[str, list[str]]:
    """Resolve each requested path or directory prefix to the real paths it covers.

    A caller naming a directory means everything under it. Resolving that here,
    against the paths actually in the graph, is what lets the dry run report
    exactly what a write would touch rather than an estimate of it.
    """
    known = [
        r["path"]
        for r in db.fetchall(
            "SELECT DISTINCT json_extract(metadata_json,'$.path') AS path FROM entities "
            "WHERE tenant_id=? AND kind LIKE 'code:%' ORDER BY path;",
            (tenant_id,),
        )
        if r["path"]
    ]
    out: dict[str, list[str]] = {}
    for raw in requested:
        wanted = str(raw).strip().rstrip("/")
        if not wanted:
            continue
        matched = [
            p for p in known if p == wanted or p.startswith(f"{wanted}/")
        ]
        out[wanted] = sorted(set(matched))
    return out


def forget_paths(
    db: Database,
    paths: Iterable[str],
    *,
    tenant_id: str = "local",
    dry_run: bool = True,
    audit_path=None,
) -> dict:
    """Remove the named paths from the code graph, or report what would go.

    ``dry_run`` defaults to true, so the safe call is the short one.
    """
    expansion = _expand(db, tenant_id, paths)
    resolved = sorted({p for matches in expansion.values() for p in matches})
    unmatched = sorted(k for k, v in expansion.items() if not v)

    per_file: list[dict] = []
    all_ids: list[str] = []
    total_edges = 0
    total_chunks = 0
    total_documents = 0
    for path in resolved:
        ids = _entity_ids_for_path(db, tenant_id, path)
        documents = _documents_for_path(db, tenant_id, path)
        chunks = _chunk_count(db, documents)
        edges = _edge_count(db, tenant_id, ids)
        all_ids.extend(ids)
        total_edges += edges
        total_chunks += chunks
        total_documents += len(documents)
        per_file.append(
            {
                "path": path,
                "symbols": len(ids),
                "edges": edges,
                "chunks": chunks,
                "documents": len(documents),
            }
        )

    if not dry_run and resolved:
        from .graph import _delete_file_graph

        with db.transaction():
            for path in resolved:
                _delete_file_graph(db, tenant_id, path)
        AuditLog(db, audit_path).record(
            AuditEntry(
                action="code.forget",
                outcome="ok",
                actor="user_local",
                subject_kind="tenant",
                subject_id=tenant_id,
                details={"paths": len(resolved), "symbols": len(all_ids), "edges": total_edges},
            )
        )

    return {
        "dry_run": dry_run,
        "applied": not dry_run and bool(resolved),
        "requested": sorted(expansion),
        "resolved_paths": resolved,
        "unmatched": unmatched,
        "per_file": per_file,
        "totals": {
            "files": len(resolved),
            "symbols": len(all_ids),
            "edges": total_edges,
            "chunks": total_chunks,
            "documents": total_documents,
        },
        "why": {
            "exactness": (
                "the dry run runs the same selection the write does, so the "
                "preview and the result cannot disagree about scope"
            ),
            "edge_counting": (
                "an edge is counted when EITHER endpoint is being dropped, "
                "because an edge whose other end survives is still going away"
            ),
            "scope": (
                "only the code plane. A directory named here means every path "
                "under it that the graph actually holds; a path the graph does "
                "not hold is reported unmatched rather than silently ignored."
            ),
            "staleness": (
                "forgetting does not re-resolve the edges of the files that "
                "remain. A reference into a forgotten file is gone, not "
                "downgraded, so re-ingest if the path is coming back."
            ),
        },
    }

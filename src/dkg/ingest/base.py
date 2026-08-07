"""High-level ingestion entry points.

The core function :func:`ingest_path` accepts a file or directory. For each
file it derives a source ID, reads the file, chunks it, records provenance,
extracts entities and claims deterministically, and writes an audit entry.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..core.audit import AuditEntry, AuditLog
from ..core.db import Database
from ..core.errors import IngestError
from ..core.ids import content_id, random_id
from ..core.provenance import ProvenanceEnvelope, record_provenance
from ..extract.claims import extract_claims
from ..extract.entities import extract_entities
from ..extract.relations import derive_cooccurrence_relations
from .chunker import chunk_paragraphs
from .readers import ReadResult, read_file, read_string


@dataclass
class IngestReport:
    source_id: str
    document_id: str
    documents_added: int
    chunks_added: int
    entities_added: int
    claims_added: int
    skipped: list[str]

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "document_id": self.document_id,
            "documents_added": self.documents_added,
            "chunks_added": self.chunks_added,
            "entities_added": self.entities_added,
            "claims_added": self.claims_added,
            "skipped": self.skipped,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def ingest_text(
    db: Database,
    text: str,
    *,
    display_name: str,
    kind: str = "note",
    tenant_id: str = "local",
    metadata: dict | None = None,
    audit_path: Path | None = None,
) -> IngestReport:
    """Ingest an in-memory string. Useful for tests and for the note reader."""
    return _ingest_read_result(
        db,
        read_string(text, fmt="text"),
        source_uri=f"note://{display_name}",
        display_name=display_name,
        kind=kind,
        tenant_id=tenant_id,
        metadata=metadata or {},
        method="text",
        audit_path=audit_path,
    )


def ingest_path(
    db: Database,
    path: Path,
    *,
    forced_format: str | None = None,
    recursive: bool = False,
    tenant_id: str = "local",
    dry_run: bool = False,
    audit_path: Path | None = None,
) -> dict:
    path = Path(path)
    if not path.exists():
        raise IngestError(f"path does not exist: {path}")
    files: list[Path]
    if path.is_file():
        files = [path]
    elif path.is_dir():
        pattern = "**/*" if recursive else "*"
        files = sorted(p for p in path.glob(pattern) if p.is_file())
    else:
        raise IngestError(f"unsupported path type: {path}")

    total_docs = 0
    total_chunks = 0
    total_entities = 0
    total_claims = 0
    skipped: list[str] = []
    last_source = ""
    last_doc = ""
    for f in files:
        try:
            rr = read_file(f, forced_format=forced_format)
        except Exception as e:
            skipped.append(f"{f}: {e}")
            continue
        if dry_run:
            skipped.append(f"{f}: dry-run")
            continue
        report = _ingest_read_result(
            db,
            rr,
            source_uri=f"file://{f.resolve()}",
            display_name=f.name,
            kind="file",
            tenant_id=tenant_id,
            metadata={"path": str(f.resolve())},
            method="file",
            audit_path=audit_path,
        )
        total_docs += report.documents_added
        total_chunks += report.chunks_added
        total_entities += report.entities_added
        total_claims += report.claims_added
        last_source = report.source_id
        last_doc = report.document_id

    return {
        "source_id": last_source,
        "document_id": last_doc,
        "documents_added": total_docs,
        "chunks_added": total_chunks,
        "entities_added": total_entities,
        "claims_added": total_claims,
        "skipped": skipped,
    }


def _ingest_read_result(
    db: Database,
    rr: ReadResult,
    *,
    source_uri: str,
    display_name: str,
    kind: str,
    tenant_id: str,
    metadata: dict,
    method: str,
    audit_path: Path | None,
) -> IngestReport:
    body = rr.text.encode("utf-8")
    doc_hash = _sha256_bytes(body)

    # source: id derived from URI so re-ingesting the same file keeps the same source
    src_id = content_id("src", tenant_id, source_uri)
    with db.transaction():
        db.execute(
            """
            INSERT OR IGNORE INTO sources(source_id, tenant_id, kind, uri, display_name, added_at, metadata_json)
            VALUES (?,?,?,?,?,?,?);
            """,
            (
                src_id,
                tenant_id,
                kind,
                source_uri,
                display_name,
                _now(),
                json.dumps(metadata, sort_keys=True),
            ),
        )

        # dedupe by content hash within the source
        existing = db.fetchone(
            "SELECT document_id, version FROM documents WHERE source_id=? AND content_sha256=?;",
            (src_id, doc_hash),
        )
        if existing:
            doc_id = existing["document_id"]
            return IngestReport(
                source_id=src_id,
                document_id=doc_id,
                documents_added=0,
                chunks_added=0,
                entities_added=0,
                claims_added=0,
                skipped=[f"duplicate content: {doc_hash[:12]}"],
            )

        # supersedes: latest version of this source, if any
        prior = db.fetchone(
            "SELECT document_id, version FROM documents WHERE source_id=? ORDER BY version DESC LIMIT 1;",
            (src_id,),
        )
        version = 1 if prior is None else int(prior["version"]) + 1
        supersedes = prior["document_id"] if prior else None

        doc_id = content_id("doc", src_id, doc_hash, str(version))
        db.execute(
            """
            INSERT INTO documents(
                document_id, source_id, tenant_id, format, content_sha256,
                byte_length, ingested_at, version, metadata_json, supersedes
            ) VALUES (?,?,?,?,?,?,?,?,?,?);
            """,
            (
                doc_id,
                src_id,
                tenant_id,
                rr.format,
                doc_hash,
                len(body),
                _now(),
                version,
                json.dumps(rr.metadata, sort_keys=True),
                supersedes,
            ),
        )

        chunks = chunk_paragraphs(rr.text)
        for ch in chunks:
            ch_id = content_id("chunk", doc_id, str(ch.ord), ch.text_sha256)
            db.execute(
                """
                INSERT INTO chunks(
                    chunk_id, document_id, tenant_id, ord, text, text_sha256,
                    start_offset, end_offset
                ) VALUES (?,?,?,?,?,?,?,?);
                """,
                (
                    ch_id,
                    doc_id,
                    tenant_id,
                    ch.ord,
                    ch.text,
                    ch.text_sha256,
                    ch.start_offset,
                    ch.end_offset,
                ),
            )

        # entities and claims. Source code is skipped here: the code plane
        # builds code entities and edges, not the document entity extractor,
        # which would produce noise on source text.
        entities_added = 0
        claims_added = 0
        chunk_entity_map: dict[str, list[str]] = {}
        chunk_rows = (
            []
            if rr.format.startswith("code:")
            else db.fetchall("SELECT chunk_id, text FROM chunks WHERE document_id = ?;", (doc_id,))
        )
        for row in chunk_rows:
            ents = extract_entities(row["text"])
            eids: list[str] = []
            for ent in ents:
                eid = _upsert_entity(db, tenant_id, ent.kind, ent.canonical, ent.display)
                eids.append(eid)
                # mention
                db.execute(
                    """
                    INSERT INTO mentions(mention_id, chunk_id, entity_id, tenant_id, surface, start_offset, end_offset)
                    VALUES (?,?,?,?,?,?,?);
                    """,
                    (
                        random_id("men"),
                        row["chunk_id"],
                        eid,
                        tenant_id,
                        ent.surface,
                        ent.start,
                        ent.end,
                    ),
                )
                entities_added += 1
            chunk_entity_map[row["chunk_id"]] = eids

            claims = extract_claims(row["text"])
            for c in claims:
                claim_id = content_id(
                    "cla", row["chunk_id"], c.predicate, c.object_text
                )
                subj_id = None
                if c.subject_hint:
                    subj_id = _upsert_entity(db, tenant_id, "concept", c.subject_hint, c.subject_hint)
                db.execute(
                    """
                    INSERT OR IGNORE INTO claims(
                        claim_id, chunk_id, tenant_id, subject_id, predicate,
                        object_text, confidence, extractor, metadata_json
                    ) VALUES (?,?,?,?,?,?,?,?,?);
                    """,
                    (
                        claim_id,
                        row["chunk_id"],
                        tenant_id,
                        subj_id,
                        c.predicate,
                        c.object_text,
                        c.confidence,
                        "deterministic-v1",
                        json.dumps({}),
                    ),
                )
                claims_added += 1
                # citation for the claim
                db.execute(
                    """
                    INSERT INTO citations(citation_id, tenant_id, target_kind, target_id, chunk_id, locator_json)
                    VALUES (?,?,?,?,?,?);
                    """,
                    (
                        random_id("cit"),
                        tenant_id,
                        "claim",
                        claim_id,
                        row["chunk_id"],
                        json.dumps({}),
                    ),
                )

        # deterministic co-occurrence relationships (per chunk)
        for cid, eids in chunk_entity_map.items():
            for rel in derive_cooccurrence_relations(eids):
                db.execute(
                    """
                    INSERT OR IGNORE INTO relationships(
                        relationship_id, tenant_id, subject_id, predicate, object_id,
                        support, weight, evidence_json, metadata_json
                    ) VALUES (?,?,?,?,?,?,?,?,?);
                    """,
                    (
                        content_id("rel", rel.subject_id, rel.predicate, rel.object_id),
                        tenant_id,
                        rel.subject_id,
                        rel.predicate,
                        rel.object_id,
                        "supports",
                        rel.weight,
                        json.dumps([cid]),
                        json.dumps({}),
                    ),
                )

        # provenance envelopes
        record_provenance(
            db,
            ProvenanceEnvelope(
                subject_kind="source",
                subject_id=src_id,
                actor="user_local",
                method=method,
                inputs={"uri": source_uri},
                tenant_id=tenant_id,
            ),
        )
        record_provenance(
            db,
            ProvenanceEnvelope(
                subject_kind="document",
                subject_id=doc_id,
                actor="user_local",
                method=method,
                inputs={"format": rr.format, "byte_length": len(body)},
                tenant_id=tenant_id,
            ),
        )

    AuditLog(db, audit_path).record(
        AuditEntry(
            action="ingest.file" if method == "file" else "ingest.batch",
            outcome="ok",
            actor="user_local",
            subject_kind="document",
            subject_id=doc_id,
            details={
                "source_id": src_id,
                "version": version,
                "chunks": len(chunks),
            },
        )
    )
    return IngestReport(
        source_id=src_id,
        document_id=doc_id,
        documents_added=1,
        chunks_added=len(chunks),
        entities_added=entities_added,
        claims_added=claims_added,
        skipped=[],
    )


def _upsert_entity(
    db: Database, tenant_id: str, kind: str, canonical: str, display: str
) -> str:
    from ..extract.resolver import canonicalise

    resolved = canonicalise(canonical)
    eid = content_id("ent", tenant_id, kind, resolved)
    db.execute(
        """
        INSERT OR IGNORE INTO entities(entity_id, tenant_id, kind, canonical, display, metadata_json)
        VALUES (?,?,?,?,?,?);
        """,
        (eid, tenant_id, kind, resolved, display, "{}"),
    )
    return eid

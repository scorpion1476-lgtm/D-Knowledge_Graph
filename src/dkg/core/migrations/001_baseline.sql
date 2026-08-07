-- 001_baseline: initial schema for D-Knowledge_Graph.
-- All queries executed against these tables MUST use parameter binding
-- (SchemaError otherwise) - see src/dkg/core/db.py.

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS meta (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tenants (
    tenant_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    quota_docs  INTEGER,
    quota_bytes INTEGER
);

-- Bootstrap tenant for single-user mode.
INSERT OR IGNORE INTO tenants (tenant_id, name, created_at)
VALUES ('local', 'local', strftime('%Y-%m-%dT%H:%M:%fZ','now'));

CREATE TABLE IF NOT EXISTS roles (
    role_id     TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    permissions TEXT NOT NULL  -- JSON array of capability strings
);

CREATE TABLE IF NOT EXISTS principals (
    principal_id TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    kind         TEXT NOT NULL CHECK (kind IN ('user','service','agent')),
    display_name TEXT,
    role_id      TEXT REFERENCES roles(role_id),
    created_at   TEXT NOT NULL
);

-- Sources: catalogued origins. A single source can have many versions.
CREATE TABLE IF NOT EXISTS sources (
    source_id     TEXT PRIMARY KEY,
    tenant_id     TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    kind          TEXT NOT NULL,            -- file, url, feed, upload, note, ...
    uri           TEXT NOT NULL,
    display_name  TEXT,
    added_at      TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_sources_tenant ON sources(tenant_id);
CREATE INDEX IF NOT EXISTS idx_sources_kind ON sources(kind);

-- Documents: a specific parsed rendering of a source at a specific time.
CREATE TABLE IF NOT EXISTS documents (
    document_id     TEXT PRIMARY KEY,
    source_id       TEXT NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
    tenant_id       TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    format          TEXT NOT NULL,          -- markdown, text, html, json, csv, pdf, ...
    content_sha256  TEXT NOT NULL,
    byte_length     INTEGER NOT NULL,
    ingested_at     TEXT NOT NULL,
    version         INTEGER NOT NULL DEFAULT 1,
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    supersedes      TEXT REFERENCES documents(document_id)
);

CREATE INDEX IF NOT EXISTS idx_documents_source ON documents(source_id);
CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents(content_sha256);
CREATE INDEX IF NOT EXISTS idx_documents_tenant ON documents(tenant_id);

-- Chunks: bounded units of text with stable IDs derived from content.
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id       TEXT PRIMARY KEY,
    document_id    TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    tenant_id      TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    ord            INTEGER NOT NULL,
    text           TEXT NOT NULL,
    text_sha256    TEXT NOT NULL,
    start_offset   INTEGER,
    end_offset     INTEGER
);

CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_tenant ON chunks(tenant_id);
CREATE INDEX IF NOT EXISTS idx_chunks_hash ON chunks(text_sha256);

-- Entities: normalised names discovered in chunks.
CREATE TABLE IF NOT EXISTS entities (
    entity_id     TEXT PRIMARY KEY,
    tenant_id     TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    kind          TEXT NOT NULL,           -- person, organisation, place, url, version, other
    canonical     TEXT NOT NULL,           -- canonicalised name
    display       TEXT NOT NULL,           -- best display form observed
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_entities_canonical ON entities(canonical);
CREATE INDEX IF NOT EXISTS idx_entities_kind ON entities(kind);
CREATE INDEX IF NOT EXISTS idx_entities_tenant ON entities(tenant_id);

-- Mentions: link an entity to a chunk with a span.
CREATE TABLE IF NOT EXISTS mentions (
    mention_id   TEXT PRIMARY KEY,
    chunk_id     TEXT NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    entity_id    TEXT NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
    tenant_id    TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    surface      TEXT NOT NULL,
    start_offset INTEGER,
    end_offset   INTEGER
);

CREATE INDEX IF NOT EXISTS idx_mentions_chunk ON mentions(chunk_id);
CREATE INDEX IF NOT EXISTS idx_mentions_entity ON mentions(entity_id);
CREATE INDEX IF NOT EXISTS idx_mentions_tenant ON mentions(tenant_id);

-- Claims: assertions extracted from chunks (deterministic baseline).
CREATE TABLE IF NOT EXISTS claims (
    claim_id      TEXT PRIMARY KEY,
    chunk_id      TEXT NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    tenant_id     TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    subject_id    TEXT REFERENCES entities(entity_id),
    predicate     TEXT NOT NULL,
    object_text   TEXT NOT NULL,
    confidence    REAL NOT NULL DEFAULT 0.5,
    extractor     TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_claims_chunk ON claims(chunk_id);
CREATE INDEX IF NOT EXISTS idx_claims_subject ON claims(subject_id);
CREATE INDEX IF NOT EXISTS idx_claims_predicate ON claims(predicate);
CREATE INDEX IF NOT EXISTS idx_claims_tenant ON claims(tenant_id);

-- Relationships: typed edges between entities with support kind.
CREATE TABLE IF NOT EXISTS relationships (
    relationship_id TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    subject_id      TEXT NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
    predicate       TEXT NOT NULL,
    object_id       TEXT NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
    support         TEXT NOT NULL DEFAULT 'supports'
        CHECK (support IN ('supports','refutes','uncertain','contradicts')),
    weight          REAL NOT NULL DEFAULT 1.0,
    evidence_json   TEXT NOT NULL DEFAULT '[]',  -- list of chunk_id
    metadata_json   TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_relationships_subject ON relationships(subject_id);
CREATE INDEX IF NOT EXISTS idx_relationships_object ON relationships(object_id);
CREATE INDEX IF NOT EXISTS idx_relationships_predicate ON relationships(predicate);
CREATE INDEX IF NOT EXISTS idx_relationships_tenant ON relationships(tenant_id);

-- Events: temporal facts (optional, empty by default).
CREATE TABLE IF NOT EXISTS events (
    event_id      TEXT PRIMARY KEY,
    tenant_id     TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    kind          TEXT NOT NULL,
    occurred_at   TEXT NOT NULL,
    subject_id    TEXT REFERENCES entities(entity_id),
    description   TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_events_occurred ON events(occurred_at);
CREATE INDEX IF NOT EXISTS idx_events_tenant ON events(tenant_id);

-- Citations: attach a chunk to a claim or a relationship with a locator.
CREATE TABLE IF NOT EXISTS citations (
    citation_id     TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    target_kind     TEXT NOT NULL CHECK (target_kind IN ('claim','relationship','event')),
    target_id       TEXT NOT NULL,
    chunk_id        TEXT NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    locator_json    TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_citations_target ON citations(target_kind, target_id);
CREATE INDEX IF NOT EXISTS idx_citations_chunk ON citations(chunk_id);

-- Provenance: envelope for anything with an external origin.
CREATE TABLE IF NOT EXISTS provenance (
    provenance_id  TEXT PRIMARY KEY,
    tenant_id      TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    subject_kind   TEXT NOT NULL,        -- source, document, chunk, claim, relationship, event
    subject_id     TEXT NOT NULL,
    recorded_at    TEXT NOT NULL,
    actor          TEXT NOT NULL,        -- principal_id, or 'system'
    method         TEXT NOT NULL,        -- how it was obtained: 'file', 'fetch:url', ...
    inputs_json    TEXT NOT NULL DEFAULT '{}',
    signature      TEXT                  -- optional detached signature ref
);
CREATE INDEX IF NOT EXISTS idx_provenance_subject ON provenance(subject_kind, subject_id);

-- Append-only audit log stored inside SQLite in addition to the file journal.
CREATE TABLE IF NOT EXISTS audit_log (
    audit_id     TEXT PRIMARY KEY,
    ts           TEXT NOT NULL,
    tenant_id    TEXT NOT NULL,
    actor        TEXT NOT NULL,
    action       TEXT NOT NULL,
    subject_kind TEXT,
    subject_id   TEXT,
    outcome      TEXT NOT NULL,             -- ok, denied, error
    details_json TEXT NOT NULL DEFAULT '{}',
    prev_hash    TEXT,                      -- SHA-256 of previous row's hash chain
    row_hash     TEXT NOT NULL              -- SHA-256 of canonicalised row + prev_hash
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);

-- Task ledger used by the multi-agent coordinator; append-only in practice.
CREATE TABLE IF NOT EXISTS task_runs (
    task_run_id   TEXT PRIMARY KEY,
    parent_id     TEXT,
    tenant_id     TEXT NOT NULL,
    agent         TEXT NOT NULL,
    kind          TEXT NOT NULL,
    status        TEXT NOT NULL,           -- pending, running, ok, error, cancelled, timeout
    started_at    TEXT,
    finished_at   TEXT,
    budget_units  INTEGER NOT NULL DEFAULT 0,
    used_units    INTEGER NOT NULL DEFAULT 0,
    input_json    TEXT NOT NULL DEFAULT '{}',
    output_json   TEXT NOT NULL DEFAULT '{}',
    error_json    TEXT
);
CREATE INDEX IF NOT EXISTS idx_task_runs_status ON task_runs(status);
CREATE INDEX IF NOT EXISTS idx_task_runs_parent ON task_runs(parent_id);

-- FTS5 index over chunks. Reserved: content='chunks', content_rowid mapping via triggers.
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text,
    content='chunks',
    content_rowid='rowid',
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS trg_chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text) VALUES (new.rowid, new.text);
END;
CREATE TRIGGER IF NOT EXISTS trg_chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
END;
CREATE TRIGGER IF NOT EXISTS trg_chunks_au AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
    INSERT INTO chunks_fts(rowid, text) VALUES (new.rowid, new.text);
END;

-- Baseline roles for single-user mode.
INSERT OR IGNORE INTO roles (role_id, tenant_id, name, permissions) VALUES
  ('role_local_owner', 'local', 'owner',
   '["read","ingest","curate","admin","export","approve"]'),
  ('role_local_reader', 'local', 'reader',
   '["read","export"]');

INSERT OR IGNORE INTO principals (principal_id, tenant_id, kind, display_name, role_id, created_at)
VALUES ('user_local', 'local', 'user', 'local', 'role_local_owner',
        strftime('%Y-%m-%dT%H:%M:%fZ','now'));

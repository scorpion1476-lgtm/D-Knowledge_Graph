-- Wave 3a: persisted chunk embeddings for real local vector search.
--
-- Vectors are keyed by (chunk_id, model). Keying by model name guarantees that
-- vectors from different embedding backends (for example the old hashing stub
-- and a real model) are never mixed: a query only reads rows whose model equals
-- the active adapter. Switching the embedding backend requires a re-index
-- (dkg reindex), which upserts fresh vectors for the new model.
CREATE TABLE IF NOT EXISTS chunk_embeddings (
    chunk_id   TEXT NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    tenant_id  TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    model      TEXT NOT NULL,
    dim        INTEGER NOT NULL,
    vector     BLOB NOT NULL,
    PRIMARY KEY (chunk_id, model)
);

CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_model ON chunk_embeddings(model);
CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_tenant ON chunk_embeddings(tenant_id);

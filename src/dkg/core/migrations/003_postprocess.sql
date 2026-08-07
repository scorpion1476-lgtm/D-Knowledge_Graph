-- 003_postprocess: derived views computed once during post-processing.
--
-- Everything in this migration is DERIVED. Nothing here is a source of truth:
-- each table can be dropped and rebuilt from the entities and relationships it
-- was computed from, and every reader falls back to computing live when a row
-- is absent. That is what keeps the shared substrate the only store and stops
-- these from becoming a parallel graph.
--
-- The reason they exist is cost. Community structure, execution flows, and the
-- per-symbol risk index are each a walk over the whole node and edge set, and
-- the orientation and review questions ask for them constantly. Recomputing on
-- every call means the cheapest possible answer is never available and a repeat
-- question pays the full price again.
--
-- Every row records when it was computed and against which graph revision, so a
-- stale answer is identifiable as stale rather than silently served as current.

-- One execution flow traced from an entry point, with its ordered steps.
CREATE TABLE IF NOT EXISTS code_flows (
    flow_id         TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    entry_canonical TEXT NOT NULL,
    entry_kind      TEXT NOT NULL,
    depth           INTEGER NOT NULL,
    step_count      INTEGER NOT NULL,
    file_count      INTEGER NOT NULL,
    rank_score      REAL NOT NULL,
    steps_json      TEXT NOT NULL,
    computed_at     TEXT NOT NULL,
    graph_revision  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_code_flows_tenant ON code_flows(tenant_id);
CREATE INDEX IF NOT EXISTS idx_code_flows_rank ON code_flows(tenant_id, rank_score DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_code_flows_name ON code_flows(tenant_id, name);

-- Which files each flow passes through. Separate from the flow row so "which
-- flows does this changed file set touch" is an index lookup rather than a scan
-- over every flow's serialised steps.
CREATE TABLE IF NOT EXISTS code_flow_files (
    flow_id   TEXT NOT NULL REFERENCES code_flows(flow_id) ON DELETE CASCADE,
    tenant_id TEXT NOT NULL,
    path      TEXT NOT NULL,
    PRIMARY KEY (flow_id, path)
);

CREATE INDEX IF NOT EXISTS idx_code_flow_files_path ON code_flow_files(tenant_id, path);

-- One community of the partition, summarised.
--
-- The community_index is an arbitrary per-run label. It is stored so a summary
-- can be addressed within one computed partition, and it must never be compared
-- across runs; graph_revision is here so a reader can tell whether two indices
-- came from the same run at all.
CREATE TABLE IF NOT EXISTS code_community_summaries (
    tenant_id        TEXT NOT NULL,
    community_index  INTEGER NOT NULL,
    member_count     INTEGER NOT NULL,
    file_count       INTEGER NOT NULL,
    internal_edges   INTEGER NOT NULL,
    external_edges   INTEGER NOT NULL,
    density          REAL NOT NULL,
    members_json     TEXT NOT NULL,
    files_json       TEXT NOT NULL,
    entry_points_json TEXT NOT NULL,
    computed_at      TEXT NOT NULL,
    graph_revision   TEXT NOT NULL,
    PRIMARY KEY (tenant_id, community_index)
);

-- The per-symbol structural risk score, with its factor breakdown.
CREATE TABLE IF NOT EXISTS code_symbol_risk (
    tenant_id      TEXT NOT NULL,
    canonical      TEXT NOT NULL,
    path           TEXT NOT NULL,
    score          REAL NOT NULL,
    level          TEXT NOT NULL,
    factors_json   TEXT NOT NULL,
    computed_at    TEXT NOT NULL,
    graph_revision TEXT NOT NULL,
    PRIMARY KEY (tenant_id, canonical)
);

CREATE INDEX IF NOT EXISTS idx_code_symbol_risk_path ON code_symbol_risk(tenant_id, path);
CREATE INDEX IF NOT EXISTS idx_code_symbol_risk_score ON code_symbol_risk(tenant_id, score DESC);

-- The last post-processing run: which level was asked for, which stages ran,
-- and which did not and why. Reported by the ingest so the level actually
-- applied is visible rather than assumed.
CREATE TABLE IF NOT EXISTS code_postprocess_runs (
    tenant_id      TEXT PRIMARY KEY,
    level          TEXT NOT NULL,
    stages_json    TEXT NOT NULL,
    ran_at         TEXT NOT NULL,
    graph_revision TEXT NOT NULL
);

# Architecture

D-Knowledge Graph is a small, dependency-light Python package structured as a
set of layers that can be used independently.

```
+-------------------------------------------------------------+
|  CLI  (dkg)   |  stdio MCP  |  HTTP MCP  |  Hooks / Skills   |
+-------------------------------------------------------------+
|  Multi-agent coordinator  (research, ingest, curation,       |
|  validation, contradiction, security, report)                |
+-------------------------------------------------------------+
|  Search (keyword / FTS5 / hybrid) | Graph query | Evidence   |
+-------------------------------------------------------------+
|  Ingestion (readers, chunker, archive) | Extraction (ents,   |
|  claims, relations, dedupe)                                  |
+-------------------------------------------------------------+
|  Adapters (LLM, embedding, connectors) | Capability registry |
+-------------------------------------------------------------+
|  Security (SSRF, redact, prompt defense, validators)         |
+-------------------------------------------------------------+
|  Core (config, ids, errors, db, schema, provenance, policy,  |
|  audit, version)                                             |
+-------------------------------------------------------------+
|                 SQLite (WAL, FTS5, foreign_keys)             |
+-------------------------------------------------------------+
```

## Storage

- SQLite in WAL mode. Tables: meta, tenants, roles, principals, sources,
  documents, chunks, entities, mentions, claims, relationships, events,
  citations, provenance, audit_log, task_runs, schema_migrations.
- One virtual table (`chunks_fts`) with triggers keeps an FTS5 index
  synchronised with chunks.
- Every write query is parameter-bound; the `Database` wrapper rejects SQL
  containing obvious string interpolation patterns.

## Content-derived IDs

- Sources, documents, chunks, entities, claims, and relationships have IDs of
  the form `<prefix>_<sha256[:24]>` derived from stable inputs. Re-ingesting
  the same content produces the same IDs, so deduplication is a natural side
  effect and backups round-trip losslessly.

## Ingestion

- `dkg.ingest.readers.read_file` accepts markdown, text, JSON, CSV, HTML,
  and PDF; HTML and PDF are gated on the corresponding capability.
- `dkg.ingest.chunker.chunk_paragraphs` splits text by paragraph, caps chunks
  at `chunk_max_chars`, and returns SHA-256 keyed `RawChunk` records with
  original offsets preserved.
- `dkg.ingest.archive.inspect_archive` refuses zip / tar bombs and unsafe
  paths before any decompression happens.

## Extraction

- Entities: URL, organisation-suffix, person-like Title-Case, version strings.
- Claims: simple predicate patterns (`is`, `are`, `reports`, `provides`,
  `has`, `was`). Confidence is fixed and low (0.4) so an LLM adapter can lift
  results without being overridden.
- Relationships: bounded co-occurrence within a chunk.

## Search

- `keyword_search` uses LIKE with parameter binding and a token-match score.
- `fts_search` uses `bm25` from FTS5, converted to a 0..1 score.
- `hybrid_search` reranks with reciprocal-rank fusion; every result carries a
  `why` object explaining the ranks and engines that contributed.

## Graph

- `dkg.graph.query.neighbourhood` does a bounded BFS with configurable depth
  and max nodes and returns the algorithm, depth, and truncation flag inside
  the result.

## Evidence

- `dkg.evidence.confidence.score_confidence` returns a 0..1 score and a full
  explanation record. The formula and weights are declared and reproducible.
- `dkg.evidence.contradiction.compare_claims` combines numeric mismatch,
  negation asymmetry, and antonym pairs; the caller sees the exact reason.

## Multi-agent coordinator

- `Coordinator.run_parallel` schedules tasks over a bounded worker pool.
- Every task is gated by the policy engine before any budget is spent.
- Task status is recorded in `task_runs` and mirrored to
  `evidence.ledger`.

## MCP surface

- Stdio server: newline-delimited JSON-RPC 2.0. Tools are read-only and cover
  status, search, graph neighbourhood, evidence, and facets.
- HTTP server: loopback bind by default, bearer token via environment,
  request-size cap and per-connection rate limit.

## Security

- SSRF guard resolves hosts, refuses private / loopback / metadata / link
  local / multicast / unspecified / reserved addresses, and pins the resolved
  IP for the caller to use.
- Redactor scans strings for common credential shapes before writing them to
  logs, exports, or MCP responses.
- Prompt-injection scanner rates fetched content and returns hit reasons.
- Archive inspector caps files, per-file bytes, total bytes, and compression
  ratio.

## Extensibility

- LLM adapter interface (`dkg.adapters.llm.LLMAdapter`) and embedding adapter
  interface (`dkg.adapters.embedding.EmbeddingAdapter`) let a caller drop in
  local or remote providers without changing the rest of the platform.
- Capability registry (`dkg.adapters.capability.CapabilityRegistry`) tracks
  which adapters are available and why.

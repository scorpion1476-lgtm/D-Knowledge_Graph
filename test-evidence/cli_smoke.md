# CLI end-to-end smoke

Re-recorded on 2026-08-07 by running the commands below against a throwaway
DKG home created with `mktemp -d`, in the project virtualenv, with
`DKG_ALLOW_OUTBOUND=0` and `DKG_TELEMETRY=0`.

The previous capture of this file was made on 2026-07-31 and had gone stale in
one figure: it recorded 7 read-only MCP tools, and the registry now serves 52.
It also carried the absolute path of the temporary directory that run happened
to use. Both are corrected here by re-running rather than by editing.

## dkg status (JSON)

`dkg --home "$SMOKE" --json status`

```
{
  "app_version": "0.1.0",
  "chunks": 0,
  "claims": 0,
  "documents": 0,
  "entities": 0,
  "home": "/private/tmp/dkg_smoke_WrbsjI",
  "network_allowed": false,
  "schema_major": 1,
  "telemetry_enabled": false
}
```

`network_allowed` is false and `telemetry_enabled` is false with no flags
passed, which is the air-gap default.

## Ingest and hybrid search

- Wrote `note.txt` containing `hello world about knowledge graph`.
- `dkg ingest note.txt` -> `ingested 1 documents / 1 chunks`.
- `dkg --json search knowledge` -> one result, `mode: hybrid`, snippet
  `hello world about knowledge graph`.

The `why` block on that result records how it was found:

```
"engines": ["keyword", "fts"],
"rank": {"fts": 0, "keyword": 0},
"reranked": true,
"reranker": "cross-encoder",
"vector": true
```

`reranked` and `vector` are true here because this environment has the
optional `embeddings` and `reranker` extras installed with their weights
pre-staged. On a core-only install both degrade to false and the two keyword
engines carry the query, which is the documented fallback.

## Stdio MCP handshake

Two JSON-RPC 2.0 requests written to the stdin of `dkg mcp-stdio`:

- `tools/list` -> 52 read-only tools returned.
- `tools/call { name: dkg.status }` -> `{"documents": 1, "chunks": 1,
  "entities": 0, "claims": 0}`, matching the state left by the ingest above.

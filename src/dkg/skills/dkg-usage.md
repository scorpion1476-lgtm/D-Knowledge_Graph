# dkg-usage

A short local skill describing how to drive D-Knowledge_Graph from an
interactive session or a compatible MCP client.

## What this skill covers

- Setting up a project-local `.dkg` directory with `dkg init`.
- Ingesting local notes, markdown files, and JSON snapshots.
- Running a hybrid search and reading the explain output.
- Fetching graph neighbourhoods and evidence packets.
- Running the deterministic multi-agent workflows without any model.
- Producing a portable backup and restoring it into a fresh home.

## Golden path

```bash
dkg init
dkg ingest ./my-notes --recursive
dkg status
dkg search "confidence formula"
dkg graph "d-knowledge_graph" --depth 2
dkg audit --verify
dkg backup --out backup.tar.gz
```

## Read-only defaults

The CLI never reaches the network unless you pass `--allow-network` and the
configuration has `network.allow_outbound` set to true. There is no telemetry.
Optional formats (HTML, PDF, RSS) require the matching extras and are reported
as unavailable if not installed.

## When there is no MCP host

The stdio MCP server accepts newline-delimited JSON-RPC 2.0 messages on
stdin and replies on stdout. You can call it from any script:

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | dkg mcp-stdio
```

## Known limits

- Extraction is deterministic and shallow by design; register an LLM adapter to
  raise recall and precision.
- Similarity search runs on a built-in hashing adapter; register a real
  embedding adapter for production similarity.
- The HTTP MCP surface binds to loopback by default. Do not expose it directly
  to the internet. Front it with a reverse proxy that terminates TLS.

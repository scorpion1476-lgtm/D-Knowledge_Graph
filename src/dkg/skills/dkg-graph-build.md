---
name: dkg-graph-build
kind: workflow-command
title: Build or update the knowledge graph
description: Build the D-Knowledge Graph for this repository, or bring an existing graph up to date, and report what the graph now contains.
cli: init, code-ingest, update, code-postprocess, code-languages, status, doctor
mcp: dkg.status, dkg.code.languages, dkg.code.symbols
bounds: local only, no network, bounded by the node caps each command documents
---

# Build or update the knowledge graph

Use this when the graph is missing, stale, or you have just pulled a large
change. It is the only workflow here that writes.

## Tools this drives

- CLI: `dkg init`, `dkg code-ingest`, `dkg update`, `dkg code-postprocess`,
  `dkg code-languages`, `dkg status`, `dkg doctor`.
- MCP (read-only): `dkg.status`, `dkg.code.languages`, `dkg.code.symbols`.

## Steps

1. Check whether a graph already exists.

   ```bash
   dkg status
   ```

   Call the `dkg.status` MCP tool instead if you are already connected to the
   server; it returns the same counts without leaving the session.

2. If there is no home yet, create one in the repository.

   ```bash
   dkg init
   ```

3. Confirm the languages in this repository are actually parsed here. A grammar
   that is not installed is reported as unavailable rather than silently
   skipped, and five languages are parsed by the documented pattern fallback
   instead of a grammar.

   ```bash
   dkg code-languages
   ```

4. Build or refresh.

   - First build, or after a change wide enough that an incremental pass is not
     worth it:

     ```bash
     dkg code-ingest . --resolve
     ```

     `--resolve` is opt-in type-aware resolution. It needs a pre-staged language
     server; without one the graph stays structural, which is reported rather
     than failing.

   - Routine refresh after edits or a pull:

     ```bash
     dkg update --repo .
     ```

     This is the one incremental path. It re-ingests only what git reports as
     changed.

5. Rebuild the derived views if you ran a partial ingest and want communities,
   flows, the risk index, and the search index in step with it.

   ```bash
   dkg code-postprocess
   ```

6. Report the result.

   ```bash
   dkg status
   dkg doctor
   ```

## Bounds this runs under

- Local only. This workflow runs offline: nothing in it reaches the network and
  there is no telemetry.
- `dkg code-ingest` and `dkg update` write to the graph. Every other command
  named here, and every MCP tool named here, is read-only.
- Parse coverage is honest: an absent optional grammar is reported as not
  measured, never scored as zero, and a file parsed by the pattern fallback is
  labelled `fallback` on its graph node and has its edge confidence scaled down.
- Community numbers are per-run labels. Do not compare them across runs.

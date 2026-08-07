# User guide

## First run

```bash
dkg init
dkg status
```

`dkg init` creates `.dkg/` in the current directory, writes a starter
`config.json`, and initialises the database. `dkg status` prints the
version, home path, and counts.

## Ingest local files

```bash
dkg ingest ./notes                 # single file or directory (non-recursive)
dkg ingest ./notes --recursive     # walk subdirectories
dkg ingest ./notes --dry-run       # list what would be ingested
```

Supported formats out of the box: text, markdown, JSON, CSV. Optional
extras add HTML, PDF, and RSS.

## Search

```bash
dkg search "confidence formula"                 # hybrid (default)
dkg search "confidence" --mode fts --limit 25   # FTS5 only
dkg search "beta" --entity ent_...              # filter by entity
dkg search "hello" --source src_...             # filter by source
```

Every result carries a `score` and a `why` field explaining the ranking.

## Explore the graph

```bash
dkg graph "beta" --depth 2 --max-nodes 100
```

Returns a bounded neighbourhood. The `truncated` flag tells you whether the
node cap was hit.

## Verify evidence

```bash
dkg evidence <claim_id>
```

Returns the claim, the source chunk, the citations, and the provenance
envelope for the document.

## Multi agent workflows

```bash
dkg agent research         --input '{"query":"beta"}'
dkg agent verify           --input '{"claim_id":"cla_..."}'
dkg agent contradiction    --input '{}'
dkg agent security-review  --input '{"limit":500}'
```

All workflows run without a model. Register an adapter to raise recall and
precision.

## Export and backup

```bash
dkg export --format markdown --out out.md
dkg export --format json     --out out.json
dkg export --format graphml  --out out.graphml
dkg backup --out backup.tar.gz
dkg restore backup.tar.gz --home /new/home
```

## Audit

```bash
dkg audit --limit 50
dkg audit --verify
```

`--verify` walks the hash chain and returns non-zero on the first break.

# Developer guide

## Repository layout

```
src/dkg/
  __init__.py               package version
  cli/                      command line entry point and output helpers
  core/                     config, ids, errors, db, schema, provenance, policy, audit, version
  ingest/                   readers, chunker, archive, base, web, rss
  extract/                  entities, claims, relations, dedupe
  graph/                    query
  search/                   keyword, fts, hybrid
  evidence/                 confidence, contradiction, ledger
  adapters/                 llm, embedding, connectors, capability
  export/                   json_, markdown, csv_, graphml, backup
  security/                 ssrf, redact, prompt_defense, validators
  mcp/                      protocol, tools, server_stdio, server_http
  agents/                   base, budget, ledger, coordinator + agent modules
  tenancy/                  models (tenants, roles, principals)
  skills/, plugins/         bundled skill package and plugin manifest schema
tests/
  unit/, integration/, security/, e2e/
docs/                       markdown docs
docker/                     Dockerfile + compose sample
scripts/                    dev, sbom, secret scan, licence inventory, checksum
.github/workflows/          CI
```

## Local development

```bash
python -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -e ".[dev,html,pdf,rss,web]"
./.venv/bin/python -m pytest -q
./.venv/bin/python -m ruff check src tests
```

## Running the test suite

```bash
bash scripts/run_tests.sh
```

Records the log under `test-evidence/pytest.<stamp>.log`.

## Adding a new command

A command belongs either to the shared entry module or to a surface that owns
its own file. Prefer the second for anything that is a distinct surface, so
independently developed work does not all have to edit one file.

1. For a surface of its own: add a module exposing `register(sub)` and
   `dispatch(cfg, args) -> int | None`, then add its import path to
   `EXTENSION_MODULES` in `src/dkg/cli/extensions.py`. `dispatch` must return
   `None` for a command it does not own. `src/dkg/mcp/install_cli.py`,
   `src/dkg/code/pr_cli.py`, and `src/dkg/export/viz_cli.py` are worked
   examples.
   Otherwise: add a subparser in `src/dkg/cli/entry.py` and a `_cmd_<name>`
   function that returns an int exit code.
2. Add integration tests under `tests/integration/` or `tests/e2e/`.
3. Document it in `docs/COMMANDS.md`. This is not optional:
   `tests/unit/test_docs_commands_complete.py` builds the real parser and fails
   naming any subcommand, option string, or default that the document is
   missing.
4. Add or update a traceability row in
   `docs/REQUIREMENTS_TRACEABILITY_MATRIX.csv`.

## Adding a new adapter

1. Implement the interface in `src/dkg/adapters/`.
2. Register the capability in `dkg.adapters.capability.default_registry`.
3. Add a unit test that covers the availability check.
4. Update the capability matrix.

## Adding a new agent

1. Subclass `dkg.agents.base.Agent`.
2. Register it in `Coordinator.__init__`.
3. Add integration tests that call `Coordinator.run_parallel`.

## Cross-platform notes

- The code uses `pathlib.Path` and the standard library everywhere.
- Windows should work but is not exercised in this session; run the full
  test suite on Windows before promoting `L-03` in the traceability matrix.
- SQLite behaves the same across macOS, Linux, and Windows in WAL mode,
  though file locking semantics can differ under aggressive concurrent
  writers. See `docs/OPERATIONS_RUNBOOK.md` for tuning.

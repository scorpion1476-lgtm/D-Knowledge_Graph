# Contributing

Read this first, because contributing here does not mean what it usually means.

## What contributing means under this licence

The entire repository is under the D-Knowledge Graph Source-Available
Non-Commercial Licence: PolyForm Noncommercial 1.0.0 plus an explicit
no-modification term. **This is not an open-source licence and not a
free-software licence.** Under it you may read the source, run it, and use its
output for personal and non-commercial purposes. You may not use it
commercially, you may not modify it, and you may not distribute a modified
version. Full text in `LICENSE`, summary in `NOTICE`.

That has direct consequences for this file:

- **Forking and publishing your changes is not permitted.** A public fork
  carrying modifications is exactly what the no-modification term prohibits.
- **A pull request is a proposal, not a right.** If a change is accepted, it is
  relicensed into this repository under the same single licence; there is no
  separate contributor licence agreement and no dual licensing, because there is
  only one licence.
- **The most valuable contribution is usually not code.** A precise bug report,
  a reproduction, a counterexample that breaks a measured claim, or a case where
  a document overstates what the code does is worth more here than a patch,
  because the project's standard is honest labelling and the fastest way to
  raise that standard is to catch a claim that is not true.
- **Versions distributed before 2026-08-05** were released under Apache-2.0.
  That grant is irrevocable for those versions and for anyone who received a
  copy under it. The current licence governs this version onward.

If you are unsure whether what you want to do is permitted, ask before doing it.

## Development in a clone

Python 3.10 or newer. The core install pulls zero runtime dependencies.

```bash
git clone <this repository>
cd D-Knowledge_Graph
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -e ".[dev]"
./.venv/bin/dkg --version
```

Add optional extras only for the areas you are working on:

```bash
./.venv/bin/python -m pip install -e ".[html,pdf,rss,web]"   # ingestion
./.venv/bin/python -m pip install -e ".[code]"               # source-code plane
./.venv/bin/python -m pip install -e ".[embeddings,reranker]" # retrieval
./.venv/bin/python -m pip install -e ".[media-image]"        # media plane
```

Two things to know about the environment:

- **The core must pass with no extra installed.** That is not a preference; it
  is the property that makes capability detection honest. A test that needs an
  optional tool must skip with a reason, never fail.
- **Nothing downloads a model at runtime.** Models are pre-staged by
  `scripts/prestage_models.py`, a build-time tool, and loaded local-files-only.
  If you find a runtime download, that is a bug worth reporting on its own.

Run against your working tree rather than the installed copy when you have more
than one checkout:

```bash
PYTHONPATH=$PWD/src ./.venv/bin/python -m pytest -q
```

## The test and gate commands

### Tests

```bash
./.venv/bin/python -m pytest -q                     # the whole suite
./.venv/bin/python -m pytest -q tests/unit          # one directory
./.venv/bin/python -m pytest -q tests/unit/test_docs_faq.py  # one file
bash scripts/run_tests.sh                           # records a run summary
```

`scripts/run_tests.sh` writes `test-evidence/test_run_summary.json`, the
committed record of counts, and a local log that is gitignored. It stops at the
first failure, and it records a red run as red rather than exiting zero.

### Gates

These are the checks that block. Run the ones your change can affect; the
deterministic completion gate `scripts/stop_gate.sh` runs all of them.

| Gate | Command | What it enforces |
|---|---|---|
| Lint | `python -m ruff check src tests` | Style and a security rule set, line length 160, target Python 3.10 |
| Types | `python scripts/mypy_gate.py` | A decreasing baseline: the error count may fall, never rise |
| Dashes | `bash scripts/check_dashes.sh` | No em dash and no en dash in any tracked text file |
| Secrets | `python scripts/secret_scan.py` | No credential shapes in the tree |
| Forbidden identifiers | `python scripts/scrub_scan.py --history` | Names that must never reach the public surface, over every local ref |
| Licences | `python scripts/license_inventory.py` | Every dependency licence stays permissive |
| Requirements | `python scripts/validate_traceability.py` | The matrix is structurally valid and the row count is what is expected |
| Documentation counts | `python -m pytest -q tests/unit/test_doc_count_consistency.py` | No document states a count the matrix disagrees with |
| Documentation links | `python -m pytest -q tests/unit/test_docs_links.py` | Every anchor, relative link, and cited repository path resolves |

Two notes on the gates, recorded rather than worked around:

- `python -m ruff check src tests` is the exact invocation. Ruff excludes the
  benchmark corpora under `tests/code/corpus`, `tests/retrieval/corpus`, and
  `tests/graph/corpus`, because those are deliberate test data rather than
  project code.
- The type gate is a budget, not a clean bill of health. `.mypy_baseline` holds
  the current allowed error count. If your change lowers the count, lower the
  baseline in the same change so the budget only ever decreases.

### What not to run casually

- `scripts/build_row_evidence.py` re-runs every row, and two of those rows shell
  out to `docker build`. Generate evidence for the rows you changed instead.
- `scripts/promote_rows.py` does not behave the way its docstring says: it
  recomputes every status rather than leaving unpromotable rows alone, and it
  applies a stricter bar than the documented one.
  `scripts/validate_traceability.py` is the authority.
- `scripts/prestage_models.py` downloads models. It is a build-time tool and it
  is the only place a download happens.

## Coding conventions

### The rules that are not negotiable

1. **No em dash and no en dash.** Anywhere: code, comments, documentation,
   commit messages, and translations. Use hyphens or restructure the sentence.
   This is gated, and it is easy to break by accident when writing prose in a
   language whose conventions favour them.
2. **Honest labelling.** A status is backed by real on-disk evidence. A row is
   production ready only when its implementation files exist, its acceptance is
   an executed test that passes, and its evidence is a real run rather than a
   manual note. Never force a green.
3. **No number you have not read out of an artifact.** Every measured figure in
   a document must be traceable to a file under `test-evidence/`. Several tests
   exist purely to enforce this, and they have caught real drift.
4. **Permissive third-party only.** Runtime dependencies must be Apache-2.0,
   MIT, BSD, ISC, HPND, or public-domain equivalent. No GPL, LGPL, or AGPL
   Python-linked runtime dependency, and no vendored copyleft source. Copyleft
   system tools such as ffmpeg may be invoked as optional external binaries by
   non-interactive subprocess, never linked. Avoid AGPL entirely.
5. **Air-gap default.** No content delivery network, no cloud call, no
   telemetry. Any egress is opt-in with an explicit warning. Build and
   continuous-integration tooling may use the network; that does not weaken the
   product default.
6. **Write original code.** Read other projects for inspiration only. Do not
   copy source from any other project, and do not reintroduce another tool's
   name, handle, URLs, or distinctive vocabulary anywhere in the tree. Use the
   project's own neutral, domain-general terms.

### The rules about how the code is shaped

- **One shared core, two planes.** The document-and-media plane lives in
  `src/dkg/media/`, the source-code plane in `src/dkg/code/`. They share the
  substrate and one standard, and they never share parsers. Do not put
  plane-specific logic into the shared core.
- **Capability detection, always.** A new optional dependency needs a capability
  registered in `dkg.adapters.capability.default_registry` that reports an
  honest reason when it is unavailable, and tests that skip rather than fail
  when it is absent.
- **Deterministic output.** Every list has an explicit sort key with ties broken
  by canonical name, so the same database produces byte-identical results and a
  result can be diffed.
- **Thresholds come from the data.** Analysis thresholds are derived from the
  graph's own observed distribution by nearest-rank percentile, so the cut is a
  value some node actually has. No constant may be tuned to a corpus, and both
  the threshold and its derivation are reported in the output.
- **Iterative, not recursive.** Graph algorithms are written iteratively so a
  deep graph cannot exhaust the Python stack.
- **Advisory results say so.** Structural code results are over-approximate, so
  every one carries a `why` block that says it, and review questions are worded
  as questions because they are prompts for a person.
- **Bounded and confined reads.** Anything reachable from the MCP surface is
  read-only, size-capped, and confined to a root. A cap on one dimension is not
  a bound: when the node read is capped the edge read is capped with it, and the
  `truncated` flag covers both.
- **Parameterised SQL only.** No string interpolation of query parameters, ever.
- **Fail loud in tooling.** A supply-chain or evidence script must fail rather
  than emit an empty artifact.

### Tests

- A new behaviour needs a test that can fail. Before you are done, break the
  thing on purpose and confirm the test goes red. A test that passes whatever
  the code does is worse than no test, because it reads as coverage.
- Prefer a test that cross-checks against something real: the parser, the
  registry, the requirements CSV, an evidence artifact. A test that asserts a
  string you also wrote is close to vacuous.
- Optional-tool tests skip with a reason. Never mark them expected-failure and
  never make them conditional on a silent `try`.

### Adding a command

1. Add the subparser in `src/dkg/cli/entry.py`, or register a surface through
   `src/dkg/cli/extensions.py` if it owns its own parser and handler.
2. Add the handler and give it an exit code.
3. Add tests under `tests/integration/` or `tests/e2e/`.
4. **Document it in `docs/COMMANDS.md`.** This is enforced:
   `tests/unit/test_docs_commands_complete.py` builds the real parser and the
   real MCP registry and fails when a registered subcommand, tool, option, or
   tool parameter is missing from that document.
5. Add or update the row in `docs/REQUIREMENTS_TRACEABILITY_MATRIX.csv`.

### Adding an MCP tool

The surface is read-only. A tool that writes to the database does not belong on
it, whatever the convenience. Register it in `dkg.mcp.tools`, keep its reads
bounded and confined, document it in `docs/COMMANDS.md`, and remember that the
surface is the trust boundary against an assistant acting on content it was fed.

## What a change has to satisfy

Before you propose a change, all of this should be true. The pull-request
template lists the same items as a checklist.

- [ ] It is permitted under the licence, and you are not distributing a modified
      version.
- [ ] It adds no non-permissive dependency, and ideally no dependency at all.
- [ ] It makes no network call on a runtime path and downloads no model at
      runtime.
- [ ] Every new capability is capability-detected and degrades with an honest
      reason.
- [ ] The core still installs and passes with no optional extra present.
- [ ] It has a test that can fail, and you have watched it fail.
- [ ] Every number it states in a document is read out of an artifact under
      `test-evidence/`.
- [ ] No em dash and no en dash anywhere in the change.
- [ ] `docs/COMMANDS.md` is updated if it touched the command line or the MCP
      surface.
- [ ] The requirements matrix row is updated if it changed what a row claims.
- [ ] The gates in the table above pass.

## Reporting rather than patching

- **A bug**: open an issue using the bug template. It asks for the version, the
  platform, which extras are present, a reproduction, and expected against
  observed behaviour, because a report without those cannot be acted on. The
  fastest way to supply most of it is to paste `dkg doctor` and
  `python scripts/probe_environment.py`.
- **A wrong claim in a document**: that is a bug, and a serious one here. Say
  which document, which sentence, and what the repository actually does.
- **A security issue**: do not open a public issue. Follow `SECURITY.md`.
- **Conduct**: `CODE_OF_CONDUCT.md`.

## See also

- `docs/DEVELOPER_GUIDE.md` for the repository layout and the adapter and agent
  extension points.
- `docs/COMMANDS.md` for the full command and tool reference.
- `docs/DEPENDENCY_AND_LICENCE_POLICY.md` for the dependency rules in detail.
- `docs/ROADMAP.md` for what is already planned, before you build it twice.

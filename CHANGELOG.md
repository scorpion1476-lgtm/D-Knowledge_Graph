# Changelog

All notable changes to D-Knowledge Graph are recorded here.
Dates are ISO-8601.
Versions follow SemVer once a stable API is declared.

## [Unreleased]

### Added
- Perl XS is read rather than reported unsupported. No permissive Tree-sitter
  grammar for `.xs` exists in any source available to this project, so it goes
  through a documented pattern extractor (`src/dkg/code/xs.py`) at explicitly
  `fallback` fidelity: the MODULE and PACKAGE sectioning, the two-line XSUB
  headers with the PREFIX rule applied so the recorded name is the one Perl
  sees, and plain C helpers above the first MODULE line. Measured at precision
  and recall 1.0 on the authored corpus and precision 0.875, recall 1.0 on the
  held-out corpus; the single false positive is an XSUB inside an `#if 0` block,
  which is the extractor's documented limit and is published rather than tuned
  away. Every edge leaving such a file is confidence-scaled and it is never
  reported as a parse.
- `dkg watch --repo PATH` watches ONE repository with no registry and no
  multi-repository daemon, re-ingesting incrementally on change and stopping
  cleanly. It writes no registry file and leaves an existing registry untouched.
- `dkg service` runs the multi-repository watcher as a managed background
  service: `start`, `stop`, `restart`, `status`, `log` and `run`, one supervised
  worker per repository, an `O_EXCL` process-identity file that refuses a second
  start and reclaims itself when its process is gone, per-repository log files
  that are size-capped and whose names are sanitised before reaching the
  filesystem, registry reconciliation on every cycle, and a health check that
  replaces a dead worker while carrying its failure counters forward.
- `docs/LANGUAGES.md`, generated from the live language registry rather than
  hand-typed, listing every language with its extensions, how it is read, and
  its grammar licence.

### Changed
- The contradiction held-out corpus grew from 15 cases to 18. The three new
  cases are negatives contributed by an adversarial review and they cover the
  shape the corpus had never contained: two different subjects distinguished by
  a word rather than a numeral. Recall and precision are unchanged at 6 of 9
  (0.6667) and 0.75, and that is the point of the entry. A relaxation of topic
  matching that took recall to 9 of 9 was built, measured against the enlarged
  corpus, found to take precision to 0.5294, and reverted. Two changes from that
  work are kept because they are general: four further stative verbs in the claim
  extractor, and a fix to a candidate index that was only ever correct under
  strict containment.
- The visual identity is one grayscale wordmark. Every other brand asset is
  derived from it by `scripts/build_brand_assets.py`, and the coloured emblem
  that had been shipping alongside it, which the README masthead was in fact
  rendering, has been removed.

### Verified
- Subversion incremental change detection, previously exercised only through a
  stub, now also runs against a real `svn` binary and a real repository created
  with `svnadmin`: recognition, versioned listing, an unversioned file excluded,
  a deleted file removed, and both committed and uncommitted edits re-parsing
  only what changed.
- The pull-request sticky-comment path now runs against a loopback TLS server
  through the real `urllib` transport, over a real socket, including the
  two-stage artifact hand-off. This is still NOT a hosted GitHub Actions run,
  and the rows that need one remain honestly short of verified.

- Source-code parsing across 42 languages and containers, up from seven. Web,
  backend, systems, mobile, scripting, shells, and domain-specific languages,
  plus Jupyter and Databricks notebooks, Vue, Svelte, and Astro single-file
  components, and Terraform, generic HCL, and Ansible. Thirty permissive
  third-party grammars ship in the new optional `code-full` extra, which is a superset
  of `code` and `code-extended` so installing it alone gives every language. Every one is
  MIT apart from the tree-sitter-elixir and tree-sitter-hcl packages, whose
  upstream licence is recorded in `THIRD_PARTY_NOTICES.md`. None is GPL, LGPL,
  or AGPL, and none is vendored. Run `dkg code-languages`, or call the read-only
  `dkg.code.languages` tool, to see the set and what is available in your
  environment.
- A documented pattern extractor for the five languages with no installable
  permissive grammar (R, GDScript, ReScript, VB.NET, and Perl on platforms with
  no wheel). It emits the same symbols and edges the grammar path emits, is
  labelled fallback fidelity everywhere it surfaces, records that fidelity on
  the graph node, and scales every edge it produces below the same edge from a
  parsed file.
- Framework-aware PHP parsing: Composer PSR-4 and PSR-0 autoload resolution,
  route definitions and the actions they dispatch to, Eloquent model
  relationships, and Blade templates as graph nodes addressed by the dotted name
  code calls them by. PHP and Blade comments are blanked out before any of it is
  read, so a commented-out reference never becomes an edge.
- Interpreter-line detection, so an extension-less executable script is parsed
  rather than skipped, through both the git and walk ingestion paths.
- Per-language parse accuracy measured against 82 hand-labelled corpus files and
  published in `docs/BENCHMARKS.md` with each language's fidelity, plus a
  separate held-out corpus written and labelled before it was ever parsed. The
  held-out figures are micro precision 0.9901 and recall 0.9804; the measurement
  taken before any parser change was made in response to it is retained in
  `test-evidence/held_out_first_measurement.json`.

### Changed
- Licence. The entire repository, the Ariadne module included, is now under the
  D-Knowledge Graph Source-Available Non-Commercial Licence (PolyForm
  Noncommercial 1.0.0 with the Distribution and New Works permissions withdrawn
  and an explicit no-modification term added). This is not an open-source and
  not a free-software licence: personal and non-commercial use is permitted,
  commercial use is not, and neither is modification or distribution of a
  modified version. There is no separately licensed component and nothing is
  excluded from the wheel. The 0.1.0 entry below records the Apache-2.0 grant
  the scaffold originally carried; versions distributed before 2026-08-05 keep
  that grant, which cannot be and is not withdrawn for them. This licence
  governs this version onward. Third-party dependencies are unaffected and stay
  permissive-only.

### Fixed
- Contradiction detection, which surfaced nothing at all. Claim extraction now
  segments markdown blocks before splitting sentences, so a document that opens
  with a heading is no longer silently skipped, and claims are grouped by a
  paraphrase-tolerant subject match instead of exact subject and predicate
  equality. Measured on a held-out corpus: recall 5 of 6 real disagreements,
  precision 1.0.

## [0.1.0] - 2026-07-31

### Added
- Project scaffold and standalone module `dkg` under `src/`. This version was
  released under Apache-2.0; that grant is superseded for later versions by the
  relicence recorded under Unreleased, and is not withdrawn for this one.
- Requirements traceability matrix covering capability areas A through L.
- Core: configuration loader, deterministic ID generator, error hierarchy, SQLite
  wrapper enforcing parameterised queries, schema and migration runner, provenance
  envelope helper, deterministic policy engine, append-only audit log.
- Ingestion: deterministic text, markdown, JSON, CSV, HTML, and RSS readers, safe
  archive inspector with decompression caps, content-hash deduplication,
  chunker with line and paragraph strategies.
- Extraction: deterministic entity, claim and relationship extractors (regex plus
  frequency baselines), no LLM dependency.
- Graph: adjacency traversal, bounded neighbourhood search with explain output.
- Search: keyword, FTS5, entity, claim, hybrid ranking, and explain output.
- Evidence: confidence scorer, contradiction signal, append-only ledger.
- Security: SSRF and DNS-rebinding guards, redaction, prompt-injection heuristics,
  size limits, safe file-type validation, XXE-safe XML rejection.
- Export: JSON, Markdown, CSV, GraphML, and portable backup + restore.
- MCP: stdio server exposing a read-only tool set; optional HTTP surface that
  binds to loopback by default with request-size limits and rate limiting.
- Multi-agent orchestration: coordinator with per-task budgets, timeouts,
  cancellation, resumability, and a deterministic implementation of every agent
  role so that the platform can run end-to-end with no model provider connected.
- Skills, plugin manifests, hook health check, and slash-style CLI subcommands.
- Documentation: architecture, security model, threat model, dependency and
  licence policy, deployment, user, administrator, developer, and operations
  guides, quick start, and honest capability status.

### Known limitations
- No release has been signed yet.
- Windows behaviour is documented from the standard library baseline; the
  primary CI target is macOS and Linux until per-platform evidence is captured.
- Optional adapters for local LLM or embedding runtimes are declared as extras
  but no default network implementation is bundled.
- HTTP MCP surface, container image, and remote MCP client handshake have
  not been exercised in this session. See
  `reports/REMAINING_EXTERNAL_BLOCKERS.md`.

## [0.1.0-r1] - 2026-07-31 (correction pass, no feature change)

### Fixed
- `docs/REQUIREMENTS_TRACEABILITY_MATRIX.csv`: repaired four rows
  (F-10, K-09, L-02, L-05) whose status column was blank because of a
  missing CSV field. Authoritative row count is 124.
- `scripts/secret_scan.py`: removed blanket `tests/` skip; added an
  explicit `(path, kind, value)` allowlist for two documented fake
  fixtures in `tests/security/test_redaction.py`.
- Reports updated to reflect the authoritative row count and to
  separate sandbox, local CLI, HTTP MCP, container, cross-platform,
  and remote MCP client validation categories.

### Added
- `scripts/validate_traceability.py` derives counts from the CSV and
  writes `docs/traceability_summary.json`.
- `tests/security/test_secret_scanner.py` proves the scanner catches
  real-looking secrets in `tests/` and only allows the exact
  documented fake fixtures.

### Retracted
- The phrases 'clean-room implementation', 'GitHub publication', and
  'end-to-end remote deployment' are retracted where the earlier
  wording implied an independent attestation that was not produced.

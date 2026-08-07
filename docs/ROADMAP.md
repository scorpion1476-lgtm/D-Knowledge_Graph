# Roadmap

What has shipped, what is being worked on now, and what is planned but not
started. There are no dates here, because a date nobody is accountable to is
not information.

## How to read this, and why it can be trusted

Every claim in the shipped section cites the requirement rows that back it, in
backticks, for example `A-01`. Those identifiers are rows in
`docs/REQUIREMENTS_TRACEABILITY_MATRIX.csv`, the project's single status source
of truth, and `tests/unit/test_docs_roadmap.py` re-reads that file and fails
when a shipped bullet cites nothing, cites a row that does not exist, or cites a
row whose status says the work is not built. That is the whole point of the
citations: a shipped claim here cannot outrun the matrix.

The statuses the matrix uses:

| Status | What it means here |
|---|---|
| PRODUCTION READY | The implementation files resolve on disk, acceptance is an executed test, and the evidence is a real passing run. |
| IMPLEMENTED BUT NOT FULLY VERIFIED | The code exists and works, but acceptance is a manual review or a non-pytest script, so it is not claimed as production ready. |
| PARTIAL | Some of the requirement is built and some is not. The row says which part. |
| NOT IMPLEMENTED | Nothing is built yet. |

No total is written out anywhere in this document. Counts live in
`docs/traceability_summary.json`, which is generated from the matrix by
`scripts/validate_traceability.py`, and a hand-typed total would be one more
thing to drift.

Absolute measured numbers live in `docs/BENCHMARKS.md`. Nothing on this page is
a performance or accuracy claim.

## Shipped

### The shared knowledge-graph core

- A schema-migrated SQLite store with content-derived identifiers, a provenance
  envelope on every record, migration and rollback, and per-record deletion,
  export, and retention: `A-01`, `A-02`, `A-04`, `A-12`.
- Search across keyword, FTS5, facets, entities, claims, graph neighbourhood,
  and a hybrid path that explains why a result came back: `C-01`, `C-02`,
  `C-03`, `C-08`, `C-12`.
- An evidence ledger with an explainable confidence formula, claim-level
  evidence packets, and a contradiction scanner whose output is advisory
  because it is lexical rather than an entailment model: `E-02`, `E-03`,
  `E-04`.
- Export to JSON, Markdown, CSV, GraphML, and a portable backup: `A-11`.
- Deterministic extraction with no model required, and pluggable
  provider-neutral adapters for the optional ones: `D-01`, `D-04`, `D-05`.

### Retrieval and graph structure

- Optional real local embeddings and an optional local cross-encoder reranker,
  both pre-staged and loaded with no runtime download, degrading to keyword and
  FTS rank fusion when absent: `O-04`.
- Published retrieval quality on a retained corpus: `O-03`.
- Two community detectors that both run in the default path, with the higher
  measured modularity winning rather than a preference: `O-05`, `O-06`, `O-07`.

### The document-and-media plane

- Local ingestion for text, Markdown, structured data, Word documents, HTML,
  and PDF, with input hashing and a provenance envelope: `B-01`, `B-03`.
- Safe archive inspection with decompression and size caps, and a web fetch
  behind strict server-side request forgery controls: `B-02`, `B-05`.
- Image decode and EXIF, image OCR through the external tesseract binary, SVG
  text extraction with entity expansion rejected, video container metadata
  through external ffprobe, and subtitle extraction with timecodes: `M-01`,
  `M-02`, `M-03`, `M-04`, `M-06`, `M-07`.
- Video keyframe and scene detection, and on-screen OCR on those keyframes,
  both with timecodes: `P-01`, `P-02`.
- Every media capability is capability-detected, so the core installs and
  passes with none of the tools present: `M-10`.

### The source-code plane

- 42 languages and containers, each measured against a labelled corpus before
  it is claimed. Perl XS has no permissive grammar anywhere, so it is read by a
  documented pattern extractor at explicitly lower fidelity and is never
  reported as a parse: `N-10`.

- Tree-sitter parsing for the starter set, a code graph on the shared
  substrate, git-incremental re-parse, code search, and structural
  blast-radius: `N-01`, `N-03`, `N-04`, `N-05`, `N-06`.
- Published per-language parse accuracy, including on a held-out corpus written
  and labelled before it was ever parsed: `N-02`, `N-23`.
- Notebooks, single-file components, infrastructure-as-code, and
  configuration-management automation, unwrapped and parsed by an ordinary
  grammar rather than guessed at: `N-11`, `N-12`, `N-13`, `N-14`.
- A labelled pattern fallback for the languages with no installable permissive
  grammar, reported at fallback fidelity and never as fully parsed: `N-16`.
- Structural execution-flow tracing: `P-04`.

### Advanced code analysis

- Intra-procedural dataflow with def-use type inference, an advisory
  source-to-sink taint pass, and published resolved-versus-structural
  precision on a retained ambiguity corpus: `Q-02`, `Q-03`.
- Three-tier edge confidence, weighted impact ranking, flow criticality, and an
  advisory zero-to-one change risk score with every factor's contribution
  shown: `Q-04`, `Q-05`, `Q-06`, `Q-07`.
- Impact accuracy measured against git co-change, which is ground truth the
  graph did not produce: `Q-09`.
- Dead-code candidates, large-symbol queries, and a read-only rename preview
  whose apply path is command-line only behind an explicit confirmation:
  `Q-10`, `Q-11`, `Q-12`, `Q-13`.

### Graph analysis and review

- One shared read-only in-memory view every analysis feature builds on, rather
  than a loader per feature: `T-01`.
- Hub and bridge detection, unexpected-coupling scoring, knowledge-gap
  analysis, generated review questions, an architecture overview with coupling
  warnings, and graph diffing over time: `T-02`, `T-03`, `T-04`, `T-05`,
  `T-06`, `T-07`.
- Free-form bounded traversal, oversized-community splitting, and a generated
  browsable knowledge base from the community structure: `T-09`, `T-10`,
  `T-11`.

### Surfaces

- A read-only stdio MCP server and a loopback HTTP one with bearer auth,
  request size limits, rate limiting, session isolation, and a structured error
  model: `F-01`, `F-02`, `F-03`, `F-05`, `F-06`, `F-07`, `F-08`.
- Origin and Host validation on the HTTP surface, and a caller-supplied tool
  allowlist that removes a tool rather than refusing it at call time: `F-15`,
  `F-19`.
- Reusable MCP prompt templates, an orientation tool, cross-repository search,
  and a documentation reader confined to its root: `F-13`, `F-14`, `F-16`,
  `F-17`.
- A command-line interface with human and machine-readable output, config
  repair and rollback, and version compatibility checks: `G-05`, `G-06`,
  `G-08`.

### Delivery

- `dkg watch --repo PATH` watches one repository with no registry and no
  multi-repository daemon, re-ingesting incrementally on change and stopping
  cleanly: `R-13`.
- `dkg service` runs the multi-repository watcher as a managed background
  service: start, stop, restart, status and log subcommands, one supervised
  worker per repository, a process-identity file that refuses a second start,
  per-repository log files, registry reconciliation without a restart, and a
  health check that replaces a dead worker: `R-24`.

- A consumer GitHub Action that installs the tool at a pinned version, a
  multi-repo registry with a bounded local watch daemon, an offline HTML viewer
  that loads nothing from a network, interoperability exports, and
  custom-language registration with a worked example: `R-01`, `R-02`, `R-03`,
  `R-04`, `R-05`.
- Editor and version-control hooks that update the graph incrementally:
  `R-12`.

### Evidence, supply chain, and licensing

- A one-command seeded benchmark harness across both planes, expanded corpora
  with published sizes and seeds, and a byte-identical wheel build: `S-01`,
  `S-02`, `S-03`.
- A blocking forbidden-identifier scan over the tracked tree, untracked files,
  and zip-container documents, plus a pre-publish pass over every local ref:
  `S-06`, `S-09`.
- A documentation-count guard, so a count a document states about the matrix
  cannot drift from it: `S-07`.
- Every grammar inside the multi-grammar bundle audited from its own upstream
  repository: `S-15`.
- The whole repository under one source-available non-commercial licence:
  `K-13`.

### Token cost and context levers

- Real BPE token counting with a dated price table, budgeted node-level context
  packing, delta-only session context, model-free exact answers,
  provenance-bounded context, and a token-budget parameter on the command line
  and the read-only MCP analysis tools: `U-01`, `U-02`, `U-03`, `U-04`,
  `U-05`, `U-06`.
- Four measured token-cost tasks and the harness that aggregates them, which
  reports a saving as a win only when correctness holds: `U-08`, `U-09`,
  `U-10`, `U-11`, `U-12`.

### Delivery, user experience, and semantic retrieval

- Configuring the read-only MCP server for twenty AI coding tools from one
  command, with detection of which are present, per-tool targeting, a dry run,
  and an ownership marker that refuses to replace an entry it did not write:
  `R-07`.
- Graph-aware guidance injected into each tool's rules file inside delimited
  managed blocks, install detection that produces a launch command that works
  on the machine it was generated on, a symmetric uninstall with explicit
  scopes, and workflow commands for the three recurring tasks: `R-09`, `R-10`,
  `R-11`, `R-14`.
- A pull-request review comment rendered with a named risk level, an ordered
  table of changed symbols with locations and coverage, the affected flows, the
  test gaps, and the token saving, with every value escaped by allowlist, and a
  merge gate on a named level whose thresholds come from the analysed
  repository's own score distribution: `R-16`, `R-17`.
- An offline viewer with in-page search, a toggling community legend, nodes
  scaled by degree, a deterministic force-directed layout, keyboard and
  assistive-technology access checked against WCAG 2.1 level AA, and a bounded
  loopback-only server to open it from a headless machine: `R-21`, `R-22`,
  `R-23`.
- A frequently-asked-questions document, a troubleshooting document, this
  roadmap, a complete commands reference gated against the real parser and tool
  registry, an extended environment diagnostic, a contributor guide, a code of
  conduct, a security policy, issue and pull-request templates, and a
  documentation cross-reference gate: `J-07`, `J-08`, `J-09`, `J-10`, `J-11`,
  `J-12`, `J-13`, `J-14`, `J-15`, `J-17`.
- A second selectable embedding backend behind the existing adapter interface,
  off by default and refusing to run without an explicit egress opt-in, and
  identifier-aware ranking with enriched embedding text, published with its
  measured before-and-after figures including an honest null on the retained
  corpus: `O-08`, `O-09`.

Built, but not fully verified, because each names behaviour only a live hosted
run can exercise and no such run has been performed. The rows say so:

- One pull-request comment posted and updated in place by its hidden marker,
  the built graph cached between continuous-integration runs, and the fork-safe
  two-stage publication that keeps a privileged token away from pull-request
  code: `R-15`, `R-18`, `R-19`.

## In progress

Work that is started and honestly incomplete. Each of these is a `PARTIAL` row,
and the row itself records which part is missing.

- Writing each supported tool's native hook definitions. The command and skill
  packages are written for almost every supported tool; hooks are not, because
  several tools document a hook mechanism whose event vocabulary could not be
  verified, and a guessed hook would fire on every tool call: `R-08`.
- Measuring per-question context reduction on large third-party repositories
  pinned to explicit commits, and the corpus of real repositories that needs:
  `S-10`, `U-15`.
- Translated README versions whose meaning has been checked by a native
  speaker. Four translations ship and their numbers, code blocks, structure and
  licence claims are gated by test, but no test can establish that a sentence
  still means what the English means: `J-16`.

## Planned

Not started. These are `NOT IMPLEMENTED` rows, listed so that the absence is on
the record rather than implied by silence.

- Multi-hop retrieval measured on a labelled task set: `S-12`.
- Incremental-update latency measured and published on a large repository:
  `S-11`.
- An editor extension client that reads the local graph in the editor: `R-20`.
- Automated dependency-update proposals for both the Python set and the pinned
  continuous-integration actions: `K-14`.
- A scheduled, bounded, report-only benchmark run in continuous integration:
  `S-13`.
- Publishing the signed, reproducibly built distribution to a public package
  index on a release event: `R-25`.

## Ongoing

These never finish, so they are not tracked as a row that can close.

- **Honest labelling.** Every status stays backed by an executed test and real
  on-disk evidence. A row is demoted the moment its evidence stops supporting
  it, and this has happened more than once.
- **Permissive third-party only.** Runtime dependencies stay Apache-2.0, MIT,
  BSD, ISC, HPND, or public domain. Copyleft system tools are allowed only as
  optional external binaries invoked by subprocess, never vendored and never
  Python-linked. The policy is in `docs/DEPENDENCY_AND_LICENCE_POLICY.md`.
- **Air-gap default.** No content delivery network, no cloud call, and no
  telemetry by default. Any egress stays opt-in with an explicit warning.
- **Supply-chain hygiene.** The lockfile, the software bill of materials, and
  the licence inventory stay generated from the real project environment, and
  GitHub Actions stay pinned by commit digest.
- **Benchmarks re-run rather than re-quoted.** `scripts/benchmark.py`
  regenerates every measured number in one seeded run, and a benchmark whose
  tool or model is absent is reported not run in this environment rather than
  failed.

## What is deliberately not planned

Saying no is part of a roadmap.

- **A hosted service.** The platform is local-first. There is no server-side
  component to sign up for, and adding one would contradict the air-gap
  default.
- **A required model provider.** Core function stays available with no model
  connected: `D-01`.
- **Write tools on the MCP surface.** The surface is read-only on purpose,
  because it is the trust boundary against an assistant acting on injected
  content. The rename preview returns an edit list; applying it is
  command-line only: `Q-11`, `Q-13`.
- **Copyleft runtime dependencies.** No GPL, LGPL, or AGPL Python-linked
  dependency will be added, whatever it would buy.
- **Commercial licensing of this repository.** The licence is source-available
  and non-commercial, with modification and modified redistribution prohibited.
  See `LICENSE` and `NOTICE`.

## See also

- `docs/REQUIREMENTS_TRACEABILITY_MATRIX.md` for the full row-by-row status.
- `docs/BENCHMARKS.md` for the measured numbers.
- `CHANGELOG.md` for what changed in each release.
- `CONTRIBUTING.md` for what a change has to satisfy.

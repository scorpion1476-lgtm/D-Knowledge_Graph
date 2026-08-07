# Commands reference

Every command-line subcommand and every read-only MCP tool this build registers,
with its parameters and its defaults.

This document is checked mechanically. `tests/unit/test_docs_commands_complete.py`
builds the real argument parser and the real MCP tool registry, then fails when a
registered subcommand, a registered tool, a command-line option, or a tool
parameter is missing from this file. Adding a command without documenting it here
turns that test red; the fix is to document the command, never to relax the test.

## How to read the tables

- **Required** is whether the command or tool refuses to run without it. Several
  tools take two alternative selectors where neither is individually required but
  one of them must be given; the tool's own description says so.
- **Default** is the value used when the parameter is omitted, read out of the
  code that reads it. `not set` means there is no substituted value: omitting the
  parameter changes behaviour rather than supplying a fallback. `token_budget`
  left `not set` means the payload is not trimmed at all; an omitted optional
  selector simply is not applied; an omitted `format` returns the structured
  result rather than the rendered one.
- `off` is an absent command-line flag.

## How to add a command to this document

1. Add a `### ` heading. For a subcommand it is the invocation in backticks,
   `### ` followed by `` `dkg <name>` ``. For an MCP tool it is the tool name in
   backticks, `### ` followed by `` `dkg.some.tool` ``. The test matches on those
   headings, so the backticks matter.
2. Add one line of description.
3. Add the parameter table with the same five columns as its neighbours. Every
   option string (including every alias) and every schema property must appear in
   the table, because the test checks each one individually.

A `### ` heading is reserved for a command or a tool. Anything else in this file
sits at `## ` or `#### `, so a command can be added without touching it.

## Conventions that apply everywhere

#### Global command-line options

These are accepted before the subcommand, for example `dkg --json status`.

| Option | Kind | Default | Notes |
|---|---|---|---|
| `--home` | string | `none` | override the DKG home directory |
| `--json` | flag | `off` | machine-readable JSON output |
| `--version` | flag | `off` | print the version and exit |
| `--token-budget` | int | `none` | bound the JSON payload to roughly this many tokens by trimming ranked lists from the tail; totals still report the true counts |
| `--no-savings` | flag | `off` | omit the estimated context-savings record from impact, review, change, and architecture results |
| `--verify-savings` | flag | `off` | cross-check the savings estimate against the real tokenizer and publish the calibration error |

#### What the surfaces do and do not do

- The network is off by default. Only `dkg ingest-web` and `dkg ingest-rss` can
  reach outward, and only when `--allow-network` is passed and the configuration
  allows it.
- The MCP surface is read-only. Only query and read tools are registered; write
  tools are intentionally never exposed. `dkg.code.rename.preview` returns the
  edit list a rename would make and applies nothing; applying is command-line
  only, behind `dkg code-rename --apply --confirm`.
- Code-plane results are structural and over-approximate unless the graph was
  built with `dkg code-ingest --resolve`. Every affected result carries a `why`
  block saying so.
- Commands that need an optional extra or an external binary say so in their help
  text and degrade with an honest reason rather than failing silently. Run
  `dkg capabilities` or `python scripts/probe_environment.py` to see what this
  machine has.

## Command-line subcommands

Invoked as `dkg <subcommand>`. Run `dkg help` for the same list at the terminal.

### `dkg init`

Initialise a project-local .dkg home.

Takes no parameter of its own.

### `dkg status`

Show status.

Takes no parameter of its own.

### `dkg doctor`

Run self-check.

Takes no parameter of its own.

### `dkg capabilities`

List adapter capabilities.

Takes no parameter of its own.

### `dkg ingest`

Ingest local file or directory.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `path` | positional | yes | n/a | file or directory |
| `--format` | string | no | `none` | force a specific format |
| `--recursive` | flag | no | `off` |  |
| `--dry-run` | flag | no | `off` |  |

### `dkg ingest-web`

Ingest one URL (requires --allow-network).

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `url` | positional | yes | n/a |  |
| `--allow-network` | flag | no | `off` |  |

### `dkg ingest-rss`

Ingest an RSS or Atom feed (requires --allow-network).

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `url` | positional | yes | n/a |  |
| `--allow-network` | flag | no | `off` |  |

### `dkg code-ingest`

Ingest a source repository into the code graph (requires the 'code' extra).

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `repo` | positional | yes | n/a | path to a source repository or directory |
| `--full` | flag | no | `off` | full re-parse instead of incremental |
| `--resolve` | flag | no | `off` | type-aware resolution via language servers and dataflow (pre-staged servers) |
| `--languages` | string | no | `none` | path to a dkg.languages.json config registering additional parser languages |
| `--include-submodules` | flag | no | `off` | also collect git submodule contents (off by default) |
| `--postprocess` | string | no | `standard` | how much of the derived-view stage to run. One of `none`, `minimal`, `standard`, `full` |

### `dkg code-postprocess`

Run the derived-view stage on its own: communities, flows, risk index, search index.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `--level` | string | no | `standard` | One of `none`, `minimal`, `standard`, `full` |
| `--stage` | repeatable | no | `none` | run only these stages, ignoring the level (repeatable) |
| `--resolution` | float | no | `1.0` |  |
| `--max-nodes` | int | no | `20000` |  |

### `dkg code-flows`

List, retrieve, or query the persisted execution-flow catalogue.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `--name` | string | no | `none` | retrieve one flow by name or identifier |
| `--changed` | repeatable | no | `none` | report which flows a changed file touches (repeatable) |
| `--limit` | int | no | `50` |  |

### `dkg code-summaries`

Read the precomputed community summaries and per-symbol risk index.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `--what` | string | no | `communities` | One of `communities`, `risk`, `flows` |
| `--key` | string | no | `none` | one community index, or one symbol canonical name |
| `--limit` | int | no | `50` |  |

### `dkg code-languages`

List every language the source-code plane parses, how, and whether it is available here.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `--available-only` | flag | no | `off` | list only languages whose grammar is installed in this environment |

### `dkg code-flow`

Trace structural execution flow (forward call chains) from a code entity.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `entity` | positional | yes | n/a | code entity id or qualified name (for example path/to/file.py::func) |
| `--depth` | int | no | `5` |  |
| `--max-nodes` | int | no | `500` |  |

### `dkg code-hubs`

Find the most connected symbols and the architectural chokepoints.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `--limit` | int | no | `20` |  |
| `--max-nodes` | int | no | `20000` |  |

### `dkg code-coupling`

Score edges that are surprising given the surrounding structure.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `--limit` | int | no | `20` |  |
| `--resolution` | float | no | `1.0` |  |
| `--max-nodes` | int | no | `20000` |  |

### `dkg code-gaps`

Isolated symbols, untested hotspots, and thin communities.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `--limit` | int | no | `20` |  |
| `--resolution` | float | no | `1.0` |  |
| `--max-nodes` | int | no | `20000` |  |

### `dkg code-impact`

Structural blast radius for a code entity or file.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `--entity` | string | no | `none` | a code entity's canonical or short name |
| `--file` | string | no | `none` | a repository-relative file path |
| `--depth` | int | no | `3` |  |
| `--max-nodes` | int | no | `500` |  |
| `--repo` | string | no | `.` | repository root, used for the savings baseline |

### `dkg code-wiki`

Generate a browsable markdown knowledge base from the community structure.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `out` | positional | yes | n/a | directory to write the knowledge base into |
| `--full` | flag | no | `off` | rewrite every page instead of only what changed |
| `--resolution` | float | no | `1.0` |  |
| `--max-nodes` | int | no | `20000` |  |

### `dkg code-forget`

Drop named paths from the code graph without a full rebuild.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `paths` | positional | yes | n/a | file or directory paths to forget |
| `--apply` | flag | no | `off` | actually delete; the default is a dry run |

### `dkg code-refactor`

Refactoring suggestions derived from community structure and coupling.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `--limit` | int | no | `20` |  |
| `--per-kind` | int | no | `5` |  |
| `--resolution` | float | no | `1.0` |  |
| `--max-nodes` | int | no | `20000` |  |

### `dkg code-risk`

Advisory 0 to 1 risk score for a change set, with every factor's contribution.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `--file` | repeatable | no | `none` | changed file (repeatable) |
| `--symbol` | repeatable | no | `none` | changed symbol (repeatable) |
| `--with-churn` | flag | no | `off` | opt in to the git change-frequency signal (off by default) |
| `--repo` | string | no | `.` | repository root, needed only for --with-churn |
| `--churn-commits` | int | no | `500` | how many commits of history to read |
| `--limit` | int | no | `50` |  |
| `--max-nodes` | int | no | `20000` |  |

### `dkg code-cochange`

Measure impact accuracy against git co-change, the non-circular ground truth.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `--repo` | string | no | `.` | repository root whose history supplies the ground truth |
| `--depth` | int | no | `3` |  |
| `--max-commits` | int | no | `500` |  |
| `--min-support` | int | no | `2` | commits a pair must share to count |
| `--max-commit-files` | int | no | `25` | commits wider than this are excluded |
| `--max-nodes` | int | no | `500` |  |

### `dkg code-dead`

Candidate dead code: definitions nothing references and no entry point reaches.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `--include-modules` | flag | no | `off` | also consider file-level module nodes |
| `--limit` | int | no | `50` |  |
| `--max-nodes` | int | no | `20000` |  |

### `dkg code-large`

Symbols at or above a line-count threshold you choose.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `--min-lines` | int | yes | `none` | the threshold, inclusive |
| `--kind` | repeatable | no | `none` | filter by symbol kind (repeatable) |
| `--path-prefix` | string | no | `none` | restrict to one subtree |
| `--limit` | int | no | `50` |  |
| `--max-nodes` | int | no | `20000` |  |

### `dkg code-rename`

Preview a symbol rename, and apply it only when you confirm.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `symbol` | positional | yes | n/a | canonical name (path::Symbol) or a short name unique in the graph |
| `new_name` | positional | yes | n/a | the new identifier |
| `--repo` | string | no | `.` | repository root; every file read is confined to it |
| `--apply` | flag | no | `off` | write the change (needs --confirm as well) |
| `--confirm` | flag | no | `off` | acknowledge that applying edits source files |
| `--diff` | flag | no | `off` | print the unified diff instead of the JSON preview |
| `--max-nodes` | int | no | `20000` |  |

### `dkg code-questions`

Suggested review questions generated from the graph analysis.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `--limit` | int | no | `20` |  |
| `--per-category` | int | no | `5` |  |
| `--resolution` | float | no | `1.0` |  |
| `--max-nodes` | int | no | `20000` |  |

### `dkg code-architecture`

Component-level architecture overview with coupling warnings.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `--format` | string | no | `markdown` | One of `markdown`, `json` |
| `--out` | string | no | `none` | write the overview to this file |
| `--limit` | int | no | `40` |  |
| `--resolution` | float | no | `1.0` |  |
| `--max-nodes` | int | no | `20000` |  |

### `dkg graph-snapshot`

Write a code-graph snapshot for later comparison.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `out` | positional | yes | n/a | path to write the snapshot JSON to |
| `--label` | string | no | `none` | human-readable label recorded in the snapshot |
| `--resolution` | float | no | `1.0` |  |
| `--max-nodes` | int | no | `20000` |  |

### `dkg graph-diff`

Compare two code-graph snapshots.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `before` | positional | yes | n/a | path to the earlier snapshot |
| `after` | positional | yes | n/a | path to the later snapshot |

### `dkg search`

Search.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `query` | positional | yes | n/a |  |
| `--mode` | string | no | `hybrid` | One of `keyword`, `fts`, `hybrid` |
| `--limit` | int | no | `10` |  |
| `--source` | string | no | `none` |  |
| `--entity` | string | no | `none` |  |

### `dkg reindex`

Re-embed all chunks with the active embedding model (run after changing the embedding backend).

Takes no parameter of its own.

### `dkg graph`

Graph query.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `entity` | positional | yes | n/a | entity ID or canonical name |
| `--depth` | int | no | `2` |  |
| `--max-nodes` | int | no | `100` |  |

### `dkg community`

Detect communities over the entity graph (modularity optimization).

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `--detector` | string | no | `both` | both runs a Mnemosyne base pass and an Ariadne refinement pass (default). One of `both`, `mnemosyne`, `ariadne` |
| `--resolution` | float | no | `1.0` |  |

### `dkg evidence`

Fetch evidence for a claim.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `claim_id` | positional | yes | n/a |  |

### `dkg export`

Export.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `--format` | string | yes | `none` | One of `json`, `markdown`, `csv`, `graphml`, `dot`, `cypher`, `svg`, `obsidian`, `html` |
| `--out` | string | yes | `none` |  |
| `--source` | string | no | `none` | restrict to a source ID |

### `dkg backup`

Write a portable backup.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `--out` | string | yes | `none` |  |

### `dkg restore`

Restore from a portable backup.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `archive` | positional | yes | n/a |  |
| `--home` | string | no | `none` |  |

### `dkg audit`

Audit log.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `--limit` | int | no | `20` |  |
| `--verify` | flag | no | `off` |  |

### `dkg agent`

Run a deterministic multi-agent workflow.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `workflow` | positional | yes | n/a | One of `research`, `verify`, `contradiction`, `security-review` |
| `--input` | string | no | `{}` | workflow input as JSON |

### `dkg registry`

Manage the multi-repo watch registry.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `action` | positional | yes | n/a | One of `add`, `list`, `remove` |
| `name` | positional | no | `none` | repository name (add, remove) |
| `path` | positional | no | `none` | repository path (add) |

### `dkg repos-search`

Search across every registered repository, with per-repository attribution.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `query` | positional | yes | n/a | the search query |
| `--limit` | int | no | `20` | merged result cap |
| `--per-repo-limit` | int | no | `10` | hits taken from each repository |
| `--max-repos` | int | no | `50` |  |

### `dkg update`

Re-ingest only what changed in a repository (the one incremental update path).

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `--repo` | string | no | `.` | repository to update |
| `--quiet` | flag | no | `off` | print nothing on success |
| `--resolve` | flag | no | `off` | also run type-aware resolution |

### `dkg hooks`

Install or remove the editor-and-commit graph update hook.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `action` | positional | yes | n/a | One of `install`, `status`, `uninstall` |
| `--repo` | string | no | `.` | repository to act on |
| `--hook` | string | no | `post-commit` | which git hook to use |
| `--force` | flag | no | `off` | replace a hook this project did not write |

### `dkg watch`

Watch ONE repository and re-ingest incrementally as it changes (no registry needed).

Independent of the registry and of `dkg daemon`: it registers nothing, writes no
registry file, and leaves an existing registry untouched.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `--repo` | string | no | `.` | repository to watch |
| `--once` | flag | no | `off` | run one scan-and-reingest pass, then exit |
| `--interval` | float | no | `1.0` | polling interval in seconds |
| `--max-seconds` | float | no | `none` | stop automatically after this many seconds |
| `--poll` | flag | no | `off` | force the polling backend even if watchfiles is installed |
| `--languages` | string | no | `none` | path to a dkg.languages.json for custom languages |

### `dkg service`

Run the multi-repo watcher as a managed background service (start, stop, restart, status, log).

`start` spawns the supervisor in its own session, so it survives the terminal
that started it, and records its process identity so a second `start` is refused
rather than double-running against the same database. One supervised worker runs
per registered repository, each with its own log file under `watch-logs/` in the
DKG home. The registry is reconciled on every cycle, so `dkg registry add` takes
effect without a restart, and a worker whose thread has died is replaced with its
failure counters carried forward.

`run` is the supervisor itself, in the foreground. It is what `start` spawns, and
it is a public action so that a service which will not start can be run and
watched directly.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `action` | positional | yes | n/a | One of `start`, `stop`, `restart`, `status`, `log`, `run` |
| `name` | positional | no | `none` | repository name (log); omit for the supervisor's own log |
| `--interval` | float | no | `1.0` | per-repository polling interval in seconds |
| `--max-seconds` | float | no | `none` | stop automatically after this many seconds |
| `--lines` | int | no | `200` | log lines to show (log) |
| `--languages` | string | no | `none` | path to a dkg.languages.json for custom languages |

### `dkg daemon`

Watch registered repos and re-ingest incrementally (bounded, local).

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `--once` | flag | no | `off` | run one scan-and-reingest pass, then exit |
| `--interval` | float | no | `1.0` | polling interval in seconds |
| `--max-seconds` | float | no | `none` | stop automatically after this many seconds |
| `--poll` | flag | no | `off` | force the polling backend even if watchfiles is installed |
| `--languages` | string | no | `none` | path to a dkg.languages.json for custom languages |

### `dkg mcp-stdio`

Run stdio MCP server on this process.

Takes no parameter of its own.

### `dkg mcp-http`

Run HTTP MCP server (loopback default).

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `--bind` | string | no | `none` |  |
| `--port` | int | no | `none` |  |

### `dkg help`

Show help.

Takes no parameter of its own.

### `dkg mcp-install`

Write the read-only MCP server entry, hooks, commands, and rules for an AI coding tool.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `tool` | positional | no | `none` | target tool (see 'dkg mcp-tools'); omit with --all |
| `--config-root` | string | no | `none` | configuration root (defaults to the user home) |
| `--target-os` | string | no | `current` | One of `current`, `darwin`, `linux`, `win32`. resolve platform-specific configuration paths for this operating system |
| `--all` | flag | no | `off` | configure every supported tool detected under the config root |
| `--all-supported` | flag | no | `off` | with --all, configure every supported tool whether or not it was detected |
| `--dry-run` | flag | no | `off` | report what would change and write nothing |
| `--command` | string | no | `none` | override the launch command instead of detecting it |
| `--no-hooks` | flag | no | `off` | do not write the tool's hook definitions |
| `--no-commands` | flag | no | `off` | do not write the tool's command or skill package |
| `--no-rules` | flag | no | `off` | do not inject the managed guidance block |
| `--force` | flag | no | `off` | overwrite an existing server entry that this project did not write (refused by default) |

### `dkg mcp-uninstall`

Remove the MCP server entry, hooks, commands, and rules this project wrote.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `tool` | positional | no | `none` | unbind this tool only; omit with --all |
| `--config-root` | string | no | `none` | configuration root (defaults to the user home) |
| `--target-os` | string | no | `current` | One of `current`, `darwin`, `linux`, `win32`. resolve platform-specific configuration paths for this operating system |
| `--all` | flag | no | `off` | unbind every supported tool under the config root |
| `--all-repos` | flag | no | `off` | also unbind every repository in the watch registry, using each as a config root |
| `--server-only` | flag | no | `off` | remove only the MCP server entry |
| `--keep-data` | flag | no | `on` | keep the graph data. This is the default; the flag is accepted so the choice can be stated explicitly. Mutually exclusive with `--purge-data` |
| `--purge-data` | flag | no | `off` | also delete the DKG home named by `--home`; refused unless that directory holds a graph database. Mutually exclusive with `--keep-data` |
| `--dry-run` | flag | no | `off` | report what would change and write nothing |

### `dkg mcp-detect`

Report which supported AI coding tools are present.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `--config-root` | string | no | `none` | configuration root (defaults to the user home) |
| `--target-os` | string | no | `current` | One of `current`, `darwin`, `linux`, `win32`. resolve platform-specific configuration paths for this operating system |

### `dkg mcp-tools`

List the AI coding tools mcp-install can configure.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `--target-os` | string | no | `current` | One of `current`, `darwin`, `linux`, `win32`. resolve platform-specific configuration paths for this operating system |

### `dkg code-report`

Analyze a repository and write a structural code report (requires the 'code' extra).

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `repo` | positional | yes | n/a | path to a source repository or directory |
| `--base` | string | no | `none` | git base ref for changed-file impact (for example the PR base SHA) |
| `--format` | string | no | `markdown` | One of `markdown`, `json` |
| `--out` | string | no | `none` | write the report to this file |
| `--fail-on-impact` | int | no | `none` | DEPRECATED advisory gate: exit non-zero if the structural impacted-entity count exceeds N. A raw count is not comparable across repositories; prefer --risk-gate. Behaviour is unchanged |
| `--risk-gate` | string | no | `off` | One of `off`, `low`, `moderate`, `elevated`, `high`. gate the run on a NAMED risk level, off by default. The run fails when the observed level is at or above this one. Thresholds are derived from this graph's own score distribution and are published in the output. Note that 'low' is the bottom cut, so gating there fails on any scored change |
| `--review` | flag | no | `off` | build the review block (risk level, changed symbols, flows, test gaps, token saving) |
| `--comment-out` | string | no | `none` | render the pull-request review comment to this file (implies --review) |
| `--marker` | string | no | `none` | hidden marker key identifying the sticky comment (default dkg-code-review) |
| `--top` | int | no | `10` | rows per table in the review (default 10) |
| `--cache-check` | flag | no | `off` | validate a restored graph database before analysing it; when it is unusable the file is removed and the graph is rebuilt in full |
| `--full` | flag | no | `off` | full re-parse instead of git-incremental |
| `--languages` | string | no | `none` | path to a dkg.languages.json for custom languages |

### `dkg pr-publish`

Validate a rendered review comment and post it as one sticky pull-request comment.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `--body-file` | string | yes | `none` | the rendered comment to post |
| `--repo` | string | yes | `none` | OWNER/NAME of the repository |
| `--pr` | int | yes | `none` | pull-request number |
| `--marker` | string | no | `none` | marker key (default dkg-code-review) |
| `--api-base` | string | no | `none` | API base URL (https only) |
| `--token-env` | string | no | `GITHUB_TOKEN` | environment variable holding the API token (default GITHUB_TOKEN) |
| `--allow-egress` | flag | no | `off` | EXPLICIT OPT-IN OUTBOUND CALL. Without this the command validates and, with --dry-run, reports what it would do, but never contacts the network |
| `--dry-run` | flag | no | `off` | validate the body locally and report the planned action without writing |
| `--timeout` | float | no | `15.0` | per-request timeout in seconds |

### `dkg viz-serve`

Serve a generated offline viewer from a bounded, loopback-only local server.

| Parameter | Kind | Required | Default | Notes |
|---|---|---|---|---|
| `--file` | string | no | `none` | an already-generated viewer HTML file to serve; when omitted, one is generated from the current database into the DKG home |
| `--host` | string | no | `127.0.0.1` | loopback bind address; any non-loopback address is refused (default: 127.0.0.1) |
| `--port` | int | yes | `none` | the port to bind, always explicit; nothing is chosen for you |
| `--max-requests` | int | no | `100` | stop after serving this many requests (default: 100) |
| `--request-timeout` | float | no | `30.0` | per-connection socket timeout in seconds (default: 30.0) |
| `--max-request-bytes` | int | no | `65536` | reject a request larger than this many bytes (default: 65536) |
| `--max-nodes` | int | no | `none` | node cap when generating the viewer; ignored when --file is given |

## MCP tools

Every tool below is registered read-only by `dkg.mcp.tools.build_read_registry`
and served over stdio by `dkg mcp-stdio` or over loopback HTTP by `dkg mcp-http`.
A server can be narrowed further with the `DKG_MCP_TOOLS` allowlist, which drops
every tool not named rather than refusing it at call time, so a narrowed server
does not advertise what it will not run.

Parameters are JSON-RPC tool-call arguments, not command-line flags.

### `dkg.status`

Return database counts and app version.

Takes no parameter.

### `dkg.search`

Hybrid search over chunks.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `query` | string | yes | n/a |  |
| `limit` | integer | no | `10` | min 1; max 100 |

### `dkg.search.keyword`

Keyword search over chunks.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `query` | string | yes | n/a |  |
| `limit` | integer | no | `10` |  |

### `dkg.search.fts`

FTS5 search over chunks.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `query` | string | yes | n/a |  |
| `limit` | integer | no | `10` |  |

### `dkg.graph.neighbourhood`

Return the graph neighbourhood around an entity.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `entity` | string | yes | n/a |  |
| `depth` | integer | no | `2` | min 0; max 5 |
| `max_nodes` | integer | no | `100` | min 1; max 1000 |

### `dkg.graph.community`

Detect communities over the entity graph by modularity optimization. The default runs BOTH detectors: a Mnemosyne base pass and an Ariadne refinement pass, returning whichever partition scores higher modularity and reporting both. Pass detector='mnemosyne' or 'ariadne' to run one alone. Read-only; advisory.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `detector` | string | no | `both` | one of `both`, `mnemosyne`, `ariadne` |
| `resolution` | number | no | `1.0` | min 0.1; max 10.0 |

### `dkg.evidence.claim`

Fetch evidence for a claim by ID.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `claim_id` | string | yes | n/a |  |

### `dkg.facets.source`

List sources with per-source chunk counts.

Takes no parameter.

### `dkg.code.symbols`

Parse a source file and return its code symbols (read-only, no DB write).

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `path` | string | no | not set |  |
| `text` | string | no | not set |  |
| `language` | string | no | not set |  |

### `dkg.code.languages`

List every language the source-code plane parses, how each one is read (grammar, composite, or documented fallback), and whether its grammar is available in this environment. Read-only; parses nothing.

Takes no parameter.

### `dkg.code.search`

Search code symbols and code text.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `query` | string | yes | n/a |  |
| `limit` | integer | no | `10` |  |

### `dkg.code.impact`

Structural blast-radius for a code entity or file. Over-approximate; refinements deferred.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `entity` | string | no | not set |  |
| `file` | string | no | not set |  |
| `depth` | integer | no | `3` | min 1; max 10 |
| `max_nodes` | integer | no | `500` | min 1; max 5000 |
| `context_savings` | boolean | no | `true` |  |
| `verify_savings` | boolean | no | `false` |  |

### `dkg.code.flow`

Structural execution-flow trace (forward call chains) from a code entity. Over-approximate; refinements deferred.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `entity` | string | yes | n/a |  |
| `depth` | integer | no | `5` | min 1; max 20 |
| `max_nodes` | integer | no | `500` | min 1; max 5000 |

### `dkg.code.hubs`

Most connected code symbols and architectural chokepoints (betweenness, degree, articulation points, bridge edges). Read-only; structural and advisory.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `limit` | integer | no | `20` | min 1; max 500 |
| `max_nodes` | integer | no | `20000` | min 1; max 200000 |
| `token_budget` | integer | no | not set | min 100; max 1000000 |

### `dkg.code.coupling`

Score code edges that are surprising given the surrounding structure: crossing a community, crossing a language, or linking a peripheral symbol to a hub. Read-only; advisory heuristic and over-approximate.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `limit` | integer | no | `20` | min 1; max 500 |
| `resolution` | number | no | `1.0` | min 0.1; max 10.0 |
| `max_nodes` | integer | no | `20000` | min 1; max 200000 |
| `token_budget` | integer | no | not set | min 100; max 1000000 |

### `dkg.code.gaps`

Knowledge gaps in the code graph: isolated symbols, heavily called symbols with no test edge, and thin communities. Read-only; structural and advisory.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `limit` | integer | no | `20` | min 1; max 500 |
| `resolution` | number | no | `1.0` | min 0.1; max 10.0 |
| `max_nodes` | integer | no | `20000` | min 1; max 200000 |
| `token_budget` | integer | no | not set | min 100; max 1000000 |

### `dkg.code.questions`

Suggested review questions generated from the graph analysis, each carrying the evidence that prompted it. Deterministic templates, no model call. Read-only; questions are prompts for a reviewer, not findings.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `limit` | integer | no | `20` | min 1; max 500 |
| `per_category` | integer | no | `5` | min 1; max 100 |
| `resolution` | number | no | `1.0` | min 0.1; max 10.0 |
| `max_nodes` | integer | no | `20000` | min 1; max 200000 |
| `token_budget` | integer | no | not set | min 100; max 1000000 |
| `context_savings` | boolean | no | `true` |  |
| `verify_savings` | boolean | no | `false` |  |

### `dkg.code.architecture`

Component-level architecture overview with coupling warnings (dependency cycles, high fan-in and fan-out, low cohesion, cross-language edges). Set format='markdown' for a rendered overview with a Mermaid diagram. Read-only; structural and advisory.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `format` | string | no | not set | one of `json`, `markdown` |
| `limit` | integer | no | `40` | min 1; max 500 |
| `resolution` | number | no | `1.0` | min 0.1; max 10.0 |
| `max_nodes` | integer | no | `20000` | min 1; max 200000 |
| `token_budget` | integer | no | not set | min 100; max 1000000 |
| `context_savings` | boolean | no | `true` |  |
| `verify_savings` | boolean | no | `false` |  |

### `dkg.graph.diff`

Compare two code-graph snapshots written by 'dkg graph-snapshot': added and removed nodes and edges, changed edge confidence, and community membership changes. Reads the two snapshot files only and does not touch the database. Both paths are confined to the snapshot directory and the read is size-capped.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `before` | string | yes | n/a |  |
| `after` | string | yes | n/a |  |

### `dkg.code.callers`

Symbols that CALL the named symbol (follows code:calls backwards). Returns answer-shaped node-level slices: one entry per SYMBOL, reduced to its declaration plus the lines bearing on the query, never whole files. Read-only; structural and over-approximate.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `symbol` | string | yes | n/a |  |
| `depth` | integer | no | `1` | min 1; max 10 |
| `detail` | string | no | `signature` | one of `signature`, `focused`, `full` |
| `max_nodes` | integer | no | `500` | min 1; max 5000 |
| `token_budget` | integer | no | not set | min 100; max 1000000 |
| `verbosity` | string | no | `full` | one of `compact`, `full` |

### `dkg.code.callees`

Symbols the named symbol CALLS (follows code:calls forwards). Returns answer-shaped node-level slices: one entry per SYMBOL, reduced to its declaration plus the lines bearing on the query, never whole files. Read-only; structural and over-approximate.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `symbol` | string | yes | n/a |  |
| `depth` | integer | no | `1` | min 1; max 10 |
| `detail` | string | no | `signature` | one of `signature`, `focused`, `full` |
| `max_nodes` | integer | no | `500` | min 1; max 5000 |
| `token_budget` | integer | no | not set | min 100; max 1000000 |
| `verbosity` | string | no | `full` | one of `compact`, `full` |

### `dkg.code.neighbours`

Symbols related to the named one in either direction, across calls, imports, and inheritance. Returns answer-shaped node-level slices: one entry per SYMBOL, reduced to its declaration plus the lines bearing on the query, never whole files. Read-only; structural and over-approximate.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `symbol` | string | yes | n/a |  |
| `depth` | integer | no | `1` | min 1; max 10 |
| `detail` | string | no | `signature` | one of `signature`, `focused`, `full` |
| `max_nodes` | integer | no | `500` | min 1; max 5000 |
| `token_budget` | integer | no | not set | min 100; max 1000000 |
| `verbosity` | string | no | `full` | one of `compact`, `full` |

### `dkg.code.implementations`

Types that INHERIT FROM the named type (follows code:inherits backwards). Each result carries its three-tier edge confidence. Read-only.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `symbol` | string | yes | n/a |  |
| `limit` | integer | no | `100` | min 1; max 1000 |
| `verbosity` | string | no | `full` | one of `compact`, `full` |

### `dkg.code.base_types`

Types the named type INHERITS FROM (follows code:inherits forwards). Each result carries its three-tier edge confidence. Read-only.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `symbol` | string | yes | n/a |  |
| `limit` | integer | no | `100` | min 1; max 1000 |
| `verbosity` | string | no | `full` | one of `compact`, `full` |

### `dkg.code.importers`

Modules that IMPORT the named module (follows code:imports backwards). Each result carries its three-tier edge confidence. Read-only.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `symbol` | string | yes | n/a |  |
| `limit` | integer | no | `100` | min 1; max 1000 |
| `verbosity` | string | no | `full` | one of `compact`, `full` |

### `dkg.code.tests_for`

Tests that exercise the named symbol (follows code:tested_by forwards). Each result carries its three-tier edge confidence. Read-only.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `symbol` | string | yes | n/a |  |
| `limit` | integer | no | `100` | min 1; max 1000 |
| `verbosity` | string | no | `full` | one of `compact`, `full` |

### `dkg.code.framework`

Framework relations for a symbol: routes_to, renders, relates_to, configures, and dispatches. Read-only; structural and advisory.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `symbol` | string | yes | n/a |  |
| `relation` | string | no | `` |  |
| `reverse` | boolean | no | `false` |  |
| `limit` | integer | no | `100` | min 1; max 1000 |
| `verbosity` | string | no | `full` | one of `compact`, `full` |

### `dkg.code.slices`

Answer-shaped node-level slices for a structural question: one entry per SYMBOL, reduced to its declaration plus the lines bearing on the seed, ranked and packed into a token budget. Returns code, but never a whole file. Read-only.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `symbol` | string | yes | n/a |  |
| `relation` | string | no | `impact` | one of `impact`, `callers`, `callees`, `flow`, `neighbours` |
| `depth` | integer | no | `3` | min 1; max 10 |
| `detail` | string | no | `focused` | one of `signature`, `focused`, `full` |
| `max_nodes` | integer | no | `500` | min 1; max 5000 |
| `token_budget` | integer | no | not set | min 100; max 1000000 |
| `verbosity` | string | no | `full` | one of `compact`, `full` |

### `dkg.code.traverse`

Free-form graph traversal from any node, breadth-first or depth-first, bounded by BOTH a depth limit and a token budget. Reports which bound bit, because a cap on one dimension is not a bound. Read-only.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `symbol` | string | yes | n/a |  |
| `order` | string | no | `breadth` | one of `breadth`, `depth` |
| `direction` | string | no | `out` | one of `out`, `in`, `both` |
| `depth` | integer | no | `3` | min 1; max 20 |
| `max_nodes` | integer | no | `1000` | min 1; max 20000 |
| `token_budget` | integer | no | not set | min 100; max 1000000 |
| `verbosity` | string | no | `full` | one of `compact`, `full` |

### `dkg.code.criticality`

Score every execution flow from an entry point by weighted criticality: depth, peak fan-in, files touched, mean edge confidence, and a bonus for being untested. Every weight and component is reported next to the total. Read-only; advisory.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `entry` | string | yes | n/a |  |
| `depth` | integer | no | `6` | min 1; max 20 |
| `max_paths` | integer | no | `50` | min 1; max 500 |
| `max_nodes` | integer | no | `2000` | min 1; max 200000 |
| `verbosity` | string | no | `full` | one of `compact`, `full` |

### `dkg.graph.community.split`

Detect communities, then split any that hold more than a documented share of the graph by re-detecting inside them. A split is kept only when it measurably improves modularity; a rejected one is reported with its numbers. Community indices are arbitrary per-run labels, so never compare them across runs. Read-only.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `resolution` | number | no | `1.0` | min 0.1; max 10.0 |
| `oversize_share` | number | no | `DEFAULT_OVERSIZE_SHARE` | min 0.01; max 1.0 |
| `verbosity` | string | no | `full` | one of `compact`, `full` |

### `dkg.code.review_context`

Everything a reviewer needs about one symbol in a single call: what it is, who calls it, what it calls, whether anything tests it, its edge-confidence mix, and the review questions the graph would raise about it. Read-only; advisory, and the questions are prompts for a human rather than findings.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `symbol` | string | yes | n/a |  |
| `depth` | integer | no | `1` | min 1; max 5 |
| `token_budget` | integer | no | not set | min 100; max 1000000 |
| `verbosity` | string | no | `full` | one of `compact`, `full` |

### `dkg.code.impact_radius`

Blast radius for a symbol or file with each impacted symbol ranked by a documented weighted score (distance, edge confidence, and fan-in), rather than returned as a flat set. Read-only; structural and over-approximate.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `symbol` | string | yes | n/a |  |
| `depth` | integer | no | `3` | min 1; max 10 |
| `limit` | integer | no | `50` | min 1; max 500 |
| `max_nodes` | integer | no | `500` | min 1; max 5000 |
| `token_budget` | integer | no | not set | min 100; max 1000000 |
| `verbosity` | string | no | `full` | one of `compact`, `full` |

### `dkg.code.confidence`

The three-tier confidence profile of the code graph: how many edges are extracted, inferred, or ambiguous, per predicate, with what each tier means. Tells a caller how much of an answer over this graph rests on a guess. Read-only.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `verbosity` | string | no | `full` | one of `compact`, `full` |

### `dkg.orient`

A compact orientation for an unfamiliar graph: its shape, the highest-value entry points, the languages present, and the suggested next calls. One small call instead of six. Read-only.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `limit` | integer | no | `10` | min 1; max 50 |
| `verbosity` | string | no | `full` | one of `compact`, `full` |

### `dkg.prompts.list`

List the reusable prompt templates for the recurring review workflows. Deterministic text; these are prompts to run, not answers. Read-only.

Takes no parameter.

### `dkg.prompts.get`

Fetch one reusable prompt template by name. Read-only.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `name` | string | yes | n/a |  |

### `dkg.docs.section`

Fetch a named section of the shipped documentation. Confined to the packaged docs directory and size-capped, because a tool that opened a caller-named path would be a filesystem read primitive behind the MCP trust boundary. Read-only.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `document` | string | yes | n/a |  |
| `section` | string | no | `` |  |

### `dkg.repos.list`

List every registered repository with its per-repository status. Read-only; reads the registry file and never writes it.

Takes no parameter.

### `dkg.repos.search`

Search across EVERY registered repository, returning per-repository attribution and honouring the same bounds and token budget as the single-repository search. Each repository's database is opened without the migration runner, so searching never writes to one; a repository that cannot be searched is reported with its reason rather than dropped. Read-only.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `query` | string | yes | n/a |  |
| `limit` | integer | no | `20` | min 1; max 200 |
| `per_repo_limit` | integer | no | `10` | min 1; max 200 |
| `token_budget` | integer | no | not set | min 100; max 1000000 |

### `dkg.memory.list`

List the recorded answers held in the memory loop. Each is a document written when a question was answered, carrying the time and graph revision it came from. A recorded answer is not a live one. Read-only.

Takes no parameter.

### `dkg.code.change`

Structural summary of the repository this server is confined to, plus the advisory blast-radius of the files changed since a base ref. Over-approximate, like the edges it walks. Carries an estimated context-savings record. Read-only.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `base` | string | no | not set |  |
| `depth` | integer | no | `3` | min 1; max 10 |
| `max_nodes` | integer | no | `500` | min 1; max 5000 |
| `context_savings` | boolean | no | `true` |  |
| `verify_savings` | boolean | no | `false` |  |

### `dkg.code.refactor`

Refactoring SUGGESTIONS derived from the community structure and the coupling signals: moves, splits, merges, and decouplings. Each names the symbols involved, the measurement that produced it, and its own reason for possibly being wrong. Worded as suggestions because they are prompts for a human, not findings. Read-only.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `limit` | integer | no | `20` | min 1; max 500 |
| `per_kind` | integer | no | `5` | min 1; max 100 |
| `resolution` | number | no | `1.0` | min 0.1; max 10.0 |
| `max_nodes` | integer | no | `20000` | min 1; max 200000 |
| `verbosity` | string | no | `full` | one of `compact`, `full` |

### `dkg.code.risk`

Advisory risk score in 0 to 1 for a change set given as files, symbols, or both. Every factor is normalised against THIS graph's own distribution and reported with its contribution, which sums exactly to the score; the level cuts are derived the same way and published. The git change-frequency signal is opt-in, reported separately, can only raise a score, and reads history from the repository root this server is confined to, never a caller-named path. Read-only; advisory.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `files` | array | no | not set | array of string |
| `symbols` | array | no | not set | array of string |
| `with_churn` | boolean | no | `false` |  |
| `churn_commits` | integer | no | `500` | min 1; max 5000 |
| `limit` | integer | no | `50` | min 1; max 500 |
| `max_nodes` | integer | no | `20000` | min 1; max 200000 |
| `verbosity` | string | no | `full` | one of `compact`, `full` |

### `dkg.code.dead`

Candidate dead code: definitions with no inbound reference edge and no entry-point evidence. ADVISORY and over-approximate; the known false-positive sources (dynamic dispatch, reflection, framework registration, exported public interface) are named in the result. Read-only.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `include_modules` | boolean | no | `false` |  |
| `limit` | integer | no | `50` | min 1; max 500 |
| `max_nodes` | integer | no | `20000` | min 1; max 200000 |
| `verbosity` | string | no | `full` | one of `compact`, `full` |

### `dkg.code.large`

Symbols whose recorded line span is at least min_lines, filterable by kind and path prefix. The threshold is the caller's; this graph's own length distribution is reported alongside so it can be placed. Read-only.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `min_lines` | integer | yes | n/a | min 1; max 100000 |
| `kinds` | array | no | not set | array of string |
| `path_prefix` | string | no | not set |  |
| `limit` | integer | no | `50` | min 1; max 500 |
| `max_nodes` | integer | no | `20000` | min 1; max 200000 |
| `verbosity` | string | no | `full` | one of `compact`, `full` |

### `dkg.code.rename.preview`

Preview a symbol rename as a read-only edit list: every file, line, and reference that would change, with ambiguous occurrences and occurrences inside comments or strings reported separately rather than included. Writes nothing and applies nothing; applying is command-line only by design. Reads are confined to the repository root and capped.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `symbol` | string | yes | n/a |  |
| `new_name` | string | yes | n/a |  |
| `max_nodes` | integer | no | `20000` | min 1; max 200000 |
| `verbosity` | string | no | `full` | one of `compact`, `full` |

### `dkg.code.flows`

List the catalogued execution flows in ranked order. Read from the precomputed catalogue, not traced live. Reports whether the catalogue is current for this graph; when nothing has been precomputed it says so rather than returning empty. Read-only.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `limit` | integer | no | `50` | min 1; max 500 |
| `verbosity` | string | no | `full` | one of `compact`, `full` |

### `dkg.code.flow.get`

Retrieve one catalogued flow by name or identifier, with its ordered steps. Structural and over-approximate like the call edges it rests on. Read-only.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `flow` | string | yes | n/a |  |
| `verbosity` | string | no | `full` | one of `compact`, `full` |

### `dkg.code.flows.affected`

Which catalogued flows pass through a changed file set. An index lookup over the catalogue rather than a re-trace of every entry point. Over-approximate in both directions and says so. Read-only.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `files` | array | yes | n/a | array of string |
| `limit` | integer | no | `50` | min 1; max 500 |
| `verbosity` | string | no | `full` | one of `compact`, `full` |

### `dkg.code.communities`

Precomputed per-community summaries: members, files, internal and external edges, density, and entry points. Community indices are arbitrary per-run labels; never compare one across runs. Read-only.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `community_index` | integer | no | not set |  |
| `limit` | integer | no | `50` | min 1; max 500 |
| `verbosity` | string | no | `full` | one of `compact`, `full` |

### `dkg.code.risk.index`

The precomputed per-symbol structural risk index, highest first, or one symbol by canonical name. Structural factors only: the opt-in git churn signal is never precomputed. Read-only; advisory.

| Parameter | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `symbol` | string | no | not set |  |
| `limit` | integer | no | `50` | min 1; max 500 |
| `verbosity` | string | no | `full` | one of `compact`, `full` |

## Where these numbers come from

The parameter tables are generated from the shipped argument parser and the
shipped tool registry, so a default here is the default the code uses. Nothing in
this file is a claim about accuracy or quality; the measured numbers live in
`docs/BENCHMARKS.md`, and requirement status lives in
`docs/REQUIREMENTS_TRACEABILITY_MATRIX.csv`.

## See also

- `docs/USER_GUIDE.md` for worked workflows rather than a reference.
- `docs/TROUBLESHOOTING.md` when a command fails.
- `docs/FAQ.md` for what this platform is and is not.

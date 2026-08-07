# Troubleshooting

Every entry below has the same three parts, in the same order: **Symptom**,
what you see; **Cause**, why it happens; **Fix**, what to do about it. If an
entry cannot honestly give all three it is not in this document.

## Before anything else

Two commands answer most of these before you read further:

```bash
dkg doctor                          # the application's own self-check, as JSON
python scripts/probe_environment.py # the environment around it, as JSON
```

The first tells you whether the install works. The second tells you what this
machine has: the interpreter, which optional extras are installed, which
external binaries and pre-staged models were found, whether a loopback port can
be bound, and whether the package index is reachable. Run both and paste both
into a bug report. The package-index check is the only outbound request the
diagnostic can make, it names the URL in its own output, and `--offline` skips
it.

## Honesty note about platform coverage

macOS is the primary development platform and is exercised. Linux is exercised
through a container image; a bare-metal Linux run remains an operator step.
**Windows and its Linux subsystem are not exercised.** Every Windows entry in
this document is marked **inferred from the code**, meaning it is derived from
reading the implementation and from how the standard library behaves on that
platform, not from an observed failure on a Windows machine. Treat those entries
as leads, not as confirmed behaviour, and if one turns out to be wrong, that is
worth reporting. The corresponding matrix row is `L-03`, and it is deliberately
not marked production ready.

## Install and path problems

### `dkg: command not found`

- **Symptom.** The shell reports `dkg: command not found` (or, on Windows,
  `'dkg' is not recognized`) immediately after an install that appeared to
  succeed.
- **Cause.** The console script was installed into an environment whose
  `bin` (or `Scripts`) directory is not on your `PATH`. This is usual when the
  install ran inside a virtual environment that is not active in the current
  shell, or when `pip` was a different interpreter's `pip` from the `python`
  you are now running.
- **Fix.** Call it through the interpreter that installed it, which never
  depends on `PATH`:
  ```bash
  ./.venv/bin/python -m dkg --version
  ```
  If that works, the install is fine and only the path is wrong: activate the
  environment (`source .venv/bin/activate`) or call `./.venv/bin/dkg`
  directly. Confirm which interpreter you are on with the `interpreter` block
  of `python scripts/probe_environment.py`.

### `pip install` fails and you cannot tell whether it is you or the network

- **Symptom.** `pip install -e ".[dev]"` fails with a resolution, timeout, or
  connection error.
- **Cause.** Two very different problems look the same from inside pip: the
  package index is unreachable from this shell (proxy, firewall, offline
  machine, captive portal), or the index is fine and the requirement genuinely
  cannot be satisfied on this interpreter.
- **Fix.** Ask the diagnostic, which reports both facts side by side:
  ```bash
  python scripts/probe_environment.py
  ```
  If `network_egress_pypi.ok` is false, it is the environment; fix the proxy or
  install from a local wheel directory. If it is true, it is the requirement;
  read the `extras` block, which lists every declared extra with each of its
  requirements and whether that distribution resolved.

### `ModuleNotFoundError` for an optional dependency

- **Symptom.** A command raises `ModuleNotFoundError: No module named 'bs4'`,
  `'pypdf'`, `'tree_sitter'`, or similar.
- **Cause.** That capability lives behind an optional extra which is not
  installed. The core install pulls no runtime dependency at all, on purpose.
- **Fix.** Install just the extra you need, for example
  `pip install -e ".[html]"` or `pip install -e ".[code]"`. Run
  `dkg capabilities` first: it lists every optional adapter with an honest
  reason for each unavailable one, which names the extra.

### Python is too old

- **Symptom.** The install fails with a metadata or syntax error, or
  `dkg` raises on an unfamiliar syntax.
- **Cause.** The project requires Python 3.10 or newer.
- **Fix.** Check with `python -V`, or read `interpreter.version` from the
  diagnostic, and build the virtual environment from a 3.10 or newer
  interpreter. Note that `tomllib` arrived in 3.11, so on 3.10 the diagnostic
  reports that it read `pyproject.toml` through its documented text fallback;
  that is expected, not a fault.

### The editable install points at the wrong tree

- **Symptom.** Your edits to `src/dkg/` have no effect, or a command behaves
  like an older version.
- **Cause.** An editable install from a different checkout is earlier on
  `sys.path` than the one you are editing. This happens easily with several
  worktrees of the same repository.
- **Fix.** Print where the package actually comes from:
  ```bash
  python -c "import dkg, sys; print(dkg.__file__)"
  ```
  If it is not the tree you are editing, either re-install from this tree or run
  with `PYTHONPATH=$PWD/src` so this checkout shadows the installed one.

## Server start-up failures

### `dkg mcp-http` exits immediately with an address error

- **Symptom.** The HTTP MCP server fails at start with `Address already in use`
  or a permission error on bind.
- **Cause.** Another process already holds the port, or the port is privileged,
  or this shell is not allowed to bind a socket at all (some sandboxes forbid
  it).
- **Fix.** Check the third case first, because it is the one that looks like the
  other two: `socket_bind_loopback` in the diagnostic reports whether this shell
  can bind a loopback port at all. If it can, choose another port with
  `dkg mcp-http --port 8901`.

### The HTTP MCP server refuses every request with an authorisation error

- **Symptom.** Requests are rejected before any tool runs.
- **Cause.** The HTTP surface requires a bearer token by design. If the
  configured token environment variable is unset or empty, there is no
  credential to match.
- **Fix.** Set the token the configuration names (`DKG_MCP_TOKEN` by default)
  in the server's environment, and send it as `Authorization: Bearer ...`.
  Loopback binding is not a substitute for the token: any local process can
  reach a loopback port, which is exactly why the token is not optional.

### The HTTP MCP server rejects a request for its `Origin` or `Host`

- **Symptom.** A request from a browser page or a proxy is refused even with a
  valid token.
- **Cause.** The server validates the `Origin` and `Host` headers against the
  addresses it was bound to, so that a web page cannot drive a local server on
  your behalf.
- **Fix.** Address the server by the authority it is bound to, or set
  `DKG_MCP_ALLOWED_HOSTS` and `DKG_MCP_ALLOWED_ORIGINS` deliberately. Widening
  them is a security decision; make it on purpose.

### The stdio MCP server appears to hang

- **Symptom.** `dkg mcp-stdio` produces no output and does not return.
- **Cause.** That is what a stdio server does. It reads JSON-RPC requests on
  standard input and writes responses to standard output; with no client
  attached there is nothing to do.
- **Fix.** Drive it from a client, or send one request by hand:
  ```bash
  echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | dkg mcp-stdio
  ```
  Never print debug output to standard output from this process: it is the
  protocol channel, and anything extra corrupts the stream.

### A configured editor or assistant does not see the tools

- **Symptom.** The client connects but lists no tools, or fewer than expected.
- **Cause.** Either the client is launching a different `dkg` (a path problem
  again, see above) or the served set has been narrowed by the `DKG_MCP_TOOLS`
  allowlist, which removes a tool outright rather than refusing it at call time.
- **Fix.** Run `dkg mcp-tools` to see which clients can be configured, use
  `dkg mcp-install` to write the entry with an absolute interpreter path, and
  check whether `DKG_MCP_TOOLS` is set in the environment the client launches.

## Database lock and staleness

### `database is locked`

- **Symptom.** A command fails with `sqlite3.OperationalError: database is
  locked`, often while another long command is running.
- **Cause.** SQLite allows one writer at a time. The database is opened in
  write-ahead logging mode with a five second busy timeout, so a short overlap
  waits and succeeds; a long one gives up. A daemon, a watcher, an editor hook,
  and an interactive command all writing at once will exceed it.
- **Fix.** Let the other writer finish. Find it before assuming there is none:
  the watch daemon (`dkg daemon`), an installed git hook (`dkg hooks status`),
  and a long `dkg code-ingest` are the usual candidates. Read paths are not
  affected, so queries continue to work while a write is in flight.

### Stray `-wal` and `-shm` files next to the database

- **Symptom.** `graph.sqlite-wal` and `graph.sqlite-shm` sit beside the
  database, sometimes large.
- **Cause.** Normal write-ahead logging. The write-ahead log holds committed
  data that has not been folded back into the main file yet.
- **Fix.** Nothing, in normal operation. They are checkpointed and removed on a
  clean close. When copying a database by hand, copy all three files or use
  `dkg backup --out ...`, which writes a consistent portable archive. Copying
  only the `.sqlite` file while a write-ahead log exists loses recent data.

### The graph is stale: recent edits are not in the answers

- **Symptom.** A query returns the old shape of the code, or a document you
  changed still matches its previous text.
- **Cause.** The graph is a materialised view. It changes when you re-ingest,
  not when the file changes. Code ingestion is git-incremental, so a file that
  git does not report as changed is not re-parsed.
- **Fix.** For a repository, `dkg update --repo .` re-ingests only what changed;
  `dkg code-ingest <repo> --full` forces a complete re-parse when the
  incremental path has been confused, for example after a history rewrite. For
  documents, re-run `dkg ingest`. To keep it current automatically, install the
  hook with `dkg hooks install` or run `dkg daemon`.

### Search returns nothing after a successful ingest

- **Symptom.** Ingest reports documents, and `dkg search` returns no hits.
- **Cause.** Most often the query does not appear in the corpus. The next most
  likely cause is that the vector index has never been built: the read-only
  surfaces never build it on demand, because building it is a write.
- **Fix.** Confirm the corpus first with `dkg status`, then try
  `dkg search "<term>" --mode keyword` to take ranking out of the picture. If
  keyword finds it and hybrid does not, run `dkg reindex`, which is also
  required after changing the embedding backend, since vectors are keyed by
  model so backends never mix.

### The database refuses to open after a downgrade

- **Symptom.** A command fails saying the database was written by a newer major
  schema than this build supports.
- **Cause.** The store records the schema major version that wrote it and
  refuses to open a future one, rather than mis-migrating it silently.
- **Fix.** Use the newer build, or restore an older backup with
  `dkg restore <archive>`. Do not delete the version record to force it open.

### Community numbers changed between two runs

- **Symptom.** The same graph reports different community indices than last
  time.
- **Cause.** Community indices are arbitrary labels produced independently per
  run. They are not identifiers and were never stable across runs.
- **Fix.** Compare co-membership (which symbols share a community) rather than
  index numbers. `dkg graph-snapshot` and `dkg graph-diff` compare snapshots by
  canonical name for exactly this reason.

## Missing optional components

### A media command reports the tool is unavailable

- **Symptom.** OCR, video metadata, keyframes, or speech-to-text report
  unavailable with a reason instead of running.
- **Cause.** Those capabilities are external binaries, not Python packages:
  OCR needs `tesseract`, video metadata needs `ffprobe`, keyframes and scene
  detection need `ffmpeg`, and speech-to-text needs both an engine and a
  pre-staged model. They are deliberately not vendored, and copyleft ones are
  used only as external binaries invoked by subprocess.
- **Fix.** Install the binary with your system package manager, then confirm
  with the `binaries.media` block of `python scripts/probe_environment.py`,
  which reports the resolved path for each and what each one is needed for.

### Speech-to-text says no model is staged

- **Symptom.** An ASR run reports that no model is available, even with an
  engine installed.
- **Cause.** Models are never downloaded at runtime. The engine needs a model
  that has been staged in advance and named by `DKG_ASR_MODEL`.
- **Fix.** Stage the model out of band, then export `DKG_ASR_MODEL` pointing at
  it. The diagnostic's `models.asr` block reports whether the path is set and
  whether it exists.

### Embeddings or reranking silently fall back

- **Symptom.** Hybrid search works, but the results look like keyword ranking
  and `dkg capabilities` shows the embedding or reranker adapter unavailable.
- **Cause.** The model is not pre-staged. Both adapters load local files only
  and never fetch, so an absent model means a clean degradation to
  keyword-plus-FTS rank fusion rather than an error.
- **Fix.** Stage the models with `python scripts/prestage_models.py`, which is
  a build-time tool that does reach the network, or point
  `DKG_EMBEDDING_MODEL` and `DKG_RERANKER_CACHE` at an existing staged copy.
  Provenance and checksums for the staged files are in
  `docs/model_provenance.json`. After changing the backend, run `dkg reindex`.

### A language is not parsed

- **Symptom.** `dkg code-ingest` produces no symbols for files in a particular
  language.
- **Cause.** Either the grammar is not installed, or the language has no
  installable permissive grammar at all and is read by the documented pattern
  fallback at reduced fidelity.
- **Fix.** Run `dkg code-languages`, which reports every language, how it is
  parsed, and whether it is available here. Install `code`, `code-extended`, or
  `code-full` as needed. A fallback-parsed language is labelled `fallback`
  everywhere it appears, including on the graph node, and every edge leaving
  such a file is scaled down; that labelling is intentional and is not a bug.

### `--resolve` changes nothing

- **Symptom.** `dkg code-ingest --resolve` runs but the impact results are
  still over-approximate.
- **Cause.** Type-aware resolution needs Node and a pre-staged language server.
  Without them the run degrades to the structural path and says so rather than
  failing.
- **Fix.** Check the `binaries.code` block of the diagnostic: it reports
  `node`, `pyright-langserver`, and `typescript-language-server` individually.
  Resolution covers Python and JavaScript; other languages stay structural, and
  the results still carry the caveat that says so.

## Windows and the Linux subsystem

Every entry in this section is **inferred from the code** and from documented
standard-library behaviour on Windows. None of it was observed on a Windows
machine. See the honesty note at the top.

### `'dkg' is not recognized as an internal or external command`

- **Symptom.** The console script cannot be found after installing.
- **Cause, inferred from the code.** On Windows the console script lands in
  `.venv\Scripts\`, not `.venv/bin/`, and that directory is on `PATH` only
  while the environment is activated.
- **Fix.** Activate the environment (`.venv\Scripts\activate`) or invoke
  `.venv\Scripts\python.exe -m dkg`. The interpreter form does not depend on
  `PATH` on any platform.

### A file cannot be deleted or replaced while a command is running

- **Symptom.** A file operation fails with a sharing or permission error that
  the same code does not produce on macOS or Linux.
- **Cause, inferred from the code.** Windows does not allow an open file to be
  removed or renamed the way POSIX does. Any path where the code holds a handle
  while another step replaces the file will behave differently there.
- **Fix.** Let the command finish before moving or deleting its files, and
  close any editor or viewer holding the database open. If you can reproduce a
  specific command that fails this way, that is a genuinely useful bug report,
  because it moves this entry from inferred to observed.

### Paths with backslashes or drive letters appear wrong in output

- **Symptom.** A repository-relative path in the graph does not match what you
  typed.
- **Cause, inferred from the code.** The implementation uses `pathlib` and
  normalises separators; canonical names in the graph use forward slashes so a
  graph stays comparable across platforms.
- **Fix.** Query by the canonical form shown in the output rather than by the
  form you typed. A short symbol name works too where it is unique in the
  graph.

### A path is rejected or truncated for its length

- **Symptom.** Deeply nested repositories fail to ingest on Windows.
- **Cause, inferred from the code.** Windows enforces a maximum path length
  unless long paths are enabled system-wide. Nothing in this project raises that
  limit for you.
- **Fix.** Enable long path support in Windows, or check the repository out
  closer to the drive root.

### Under the Linux subsystem, everything is slow on a Windows drive

- **Symptom.** Ingest and parsing take far longer than they should when the
  repository lives under `/mnt/c/...`.
- **Cause, inferred from the code and from the subsystem's documented
  behaviour.** Cross-filesystem access between the Linux subsystem and the
  Windows filesystem is much slower per file operation, and both ingestion and
  parsing are file-operation heavy.
- **Fix.** Keep the repository and the DKG home on the Linux filesystem inside
  the subsystem rather than under `/mnt/`.

### Under the Linux subsystem, file watching misses changes

- **Symptom.** `dkg daemon` does not notice edits made from Windows tools to a
  directory under `/mnt/c/...`.
- **Cause, inferred from the code.** The watcher uses filesystem change
  notifications when the optional `watch` extra is installed, and change
  notifications do not cross the subsystem boundary reliably. The standard
  library polling fallback does not depend on them.
- **Fix.** Force polling with `dkg daemon --poll`, or keep the repository on the
  Linux filesystem.

## Network, or the deliberate lack of it

### A fetch is refused before anything is downloaded

- **Symptom.** `dkg ingest-web` or `dkg ingest-rss` refuses immediately.
- **Cause.** Outbound network is off by default. Egress needs the explicit
  `--allow-network` flag and a configuration allowance, and each fetch then
  passes a post-resolution address check that rejects private, loopback,
  link-local, multicast, reserved, and cloud-metadata addresses.
- **Fix.** Pass `--allow-network` deliberately. If a fetch is still refused, the
  address check is doing its job: the target resolved to an address the guard
  will not fetch, and that is not something to work around casually.

### Everything works but the machine has no network at all

- **Symptom.** You are on an air-gapped machine and want to know what will
  break.
- **Cause.** Nothing, in the product. The core, both planes, search, the graph,
  the evidence ledger, and both MCP surfaces are all offline paths.
- **Fix.** Nothing to fix. What does need network is build-time and
  continuous-integration tooling: installing packages, staging models with
  `scripts/prestage_models.py`, the dependency audit, and the disclosed
  reachability probe in the diagnostic. Run the diagnostic with `--offline` on
  such a machine so it does not attempt the one request it can make.

## Still stuck

Open an issue with:

1. The full output of `dkg doctor`.
2. The full output of `python scripts/probe_environment.py` (use `--offline` if
   the machine has no network).
3. The exact command you ran and the exact error, not a summary of it.
4. What you expected instead.

The issue template asks for exactly these. `SECURITY.md` covers what to do
instead when the problem is a vulnerability: do not open a public issue for
that.

## See also

- `docs/FAQ.md` for whether this is the right tool at all.
- `docs/COMMANDS.md` for every subcommand and every MCP tool.
- `docs/OPERATIONS_RUNBOOK.md` for running it as a service.
- `docs/DEPLOYMENT_GUIDE.md` for deployment specifics.

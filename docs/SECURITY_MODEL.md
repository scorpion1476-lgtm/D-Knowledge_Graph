# Security model

> To report a vulnerability, see [`SECURITY.md`](../SECURITY.md) at the
> repository root. It states the supported versions, the private reporting
> channel, what a report should contain, and the acknowledgement and fix
> timelines. Do not open a public issue for a security problem.

## Design defaults

1. Local and read-only. The default profile writes only to the project-local
   `.dkg` home, opens the SQLite file with WAL, and refuses any outbound
   network request.
2. No telemetry. Nothing is emitted from the process by default.
3. No hard-coded credentials. Adapters receive references (environment
   variables, keyring lookups); nothing is committed.
4. Every write query is parameter-bound. `Database.execute` refuses
   statements that look like they were built by string interpolation.
5. Every optional integration is behind an adapter interface with an
   `available()` check. The capability registry never lies.
6. Every network-touching action requires both a configuration opt-in and,
   for external effects, an explicit consent grant from the policy engine.
7. Redaction is applied before values reach logs, exports, and MCP
   responses.
8. Content fetched from outside the trust boundary is treated as untrusted
   data, never instruction. The prompt-injection scanner rates it and
   `wrap_untrusted` marks it clearly when it is passed to a model.
9. The audit log carries a per-row hash chain, mirrored to a file journal,
   so tampering is detected by `dkg audit --verify`.

## Trust boundaries

- User -> CLI or MCP -> Coordinator -> Agents -> Storage. The policy engine
  sits in front of every state-changing hop.
- Sources -> Ingestion -> Extraction -> Storage. Ingested text is not
  treated as trusted; every extractor is deterministic and does not execute
  any code from the source.
- Adapters cross into third-party systems. Their interfaces are documented,
  their availability is reported, and no adapter is required for the core
  platform to run.

## Network defence-in-depth

- Outbound is disabled unless `network.allow_outbound` is `true`.
- `dkg.security.ssrf.validate_url` resolves the host and refuses any
  address that is private, loopback, link-local, multicast, reserved, or
  points at a cloud metadata endpoint.
- The web adapter follows no redirects, sets a size cap on the response,
  uses a fixed timeout, and never carries a session cookie.
- The HTTP MCP surface binds to loopback by default and authorises by
  credential only. **A loopback peer is not a trusted peer**: a page in an
  ordinary browser on the same machine also connects from 127.0.0.1, so
  authorising on the peer address would hand the graph to any site the user
  visits. Four checks run before any handler:
  - **Host** must match an expected authority derived from the bind address and
    port, plus anything in `http_allowed_hosts`. This is what stops a DNS name
    rebound to 127.0.0.1 from making the responses readable to its origin.
  - **Origin** must be absent or explicitly allow-listed
    (`DKG_MCP_ALLOWED_ORIGINS`, empty by default). A real MCP client is not a
    browser and sends no Origin; a browser always attaches one to a
    cross-origin POST and a page cannot suppress it. `Referer` is checked the
    same way when `Origin` is absent. `Origin: null` is never allow-listable.
  - **Content-Type** on `/rpc` must be JSON. The three CORS-safelisted types
    (`text/plain`, `application/x-www-form-urlencoded`, `multipart/form-data`)
    are refused, which removes the no-preflight path. The server emits no CORS
    headers, so a preflight is never granted.
  - **Authorisation** comes from a bearer token. A non-loopback bind always
    requires one and `serve_http` refuses to start without it. A loopback bind
    with no token is refused unless the operator sets
    `DKG_MCP_ALLOW_UNAUTHENTICATED_LOOPBACK=1`, which is off by default.
  Verified by `tests/security/test_http_origin_guard.py`, which includes a
  same-machine loopback caller rejected without a token over a real socket.

## Data at rest

- SQLite in WAL mode. WAL and SHM files are treated as sensitive.
- Backups are tar.gz with a manifest containing per-file SHA-256. Restore
  refuses any archive whose files do not match the manifest.
- Secret references go in configuration; the redactor removes them from any
  output that leaves the process.

## Data in transit

- All outbound HTTP goes through the SSRF-validated web adapter.
- The stdio MCP server operates on newline-delimited JSON on the local
  process boundary.
- The HTTP MCP surface expects a reverse proxy for TLS in production. The
  deployment guide gives an example configuration.

## Supply chain

- Zero runtime dependencies by default. Every extra is declared explicitly
  in `pyproject.toml`.
- Secret scanning, SBOM generation, and licence inventory scripts ship in
  `scripts/`.
- `SHA256SUMS` is generated for `test-evidence/` and `dist/` on demand.

## What is out of scope

- Confidential computing enclaves.
- Automated dependency vulnerability database lookups (requires network to
  a public database; see the remaining-limitation column of
  `docs/REQUIREMENTS_TRACEABILITY_MATRIX.csv`).
- Signed release verification. Signing is configured but no key material is
  bundled; do not publish a signed release from an untrusted machine.

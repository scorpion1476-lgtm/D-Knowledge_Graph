# Threat model

> Found something this model does not cover? See
> [`SECURITY.md`](../SECURITY.md) at the repository root for the private
> reporting channel, the supported versions, and the response timelines. Do not
> open a public issue for a security problem.

## Assets

- The graph database (`.dkg/graph.sqlite`) and its WAL / SHM files.
- The append-only audit log (`.dkg/audit.log`) and evidence ledger.
- Secret references stored in configuration or environment.
- Fetched content that carries citations back to the graph.

## Adversaries

1. Untrusted content in an ingested source that tries to hijack an LLM
   prompt or exfiltrate secrets.
2. A malicious archive supplied to the ingestor (zip bomb, path traversal,
   symlink escape).
3. An SSRF payload that resolves a public hostname to a private or metadata
   address.
4. A tampering attempt against the audit log or task ledger.
5. A supply-chain injection through an optional adapter or an update of a
   declared extra.
6. An accidental credential in a log line or an export.

## Mitigations

| Threat | Mitigation |
|--------|------------|
| Prompt injection | `dkg.security.prompt_defense.scan` rates untrusted text; `wrap_untrusted` marks it clearly; agents do not follow instructions from ingested content. |
| Zip / tar bomb | `dkg.ingest.archive.inspect_archive` refuses on max_files, per-file bytes, total bytes, or compression ratio breach. |
| Path traversal | Archive entries and file paths are rejected on `..`, absolute paths, and symlink escapes. `validate_repo_root` requires a project marker. |
| SSRF and DNS rebinding | `dkg.security.ssrf.validate_url` resolves every address and refuses any private / loopback / metadata address. Callers pin the resolved IP. |
| Audit tampering | Per-row SHA-256 hash chain; `dkg audit --verify` detects the first break. |
| Supply chain | Zero runtime dependencies by default; extras are pinned by constraint; SBOM + licence inventory + secret scanner ship in `scripts/`. |
| Credential leak | `dkg.security.redact.redact` scans strings for common secret shapes before writing to logs, exports, or MCP responses. |
| SQL injection | `Database.execute` requires parameter binding and refuses SQL with obvious interpolation patterns. |
| XXE and XML entity expansion | We only write XML (GraphML export); we do not parse arbitrary XML. XML entity expansion is disabled in configuration. |
| Denial of service (large payloads) | Configurable request-size caps on HTTP MCP; per-task budgets, timeouts, and retries in the coordinator. |

## Residual risk

- Optional extras (BeautifulSoup, pypdf, feedparser, httpx) execute code
  from their own maintainers. Pin them and audit them on a schedule.
- The prompt injection scanner is heuristic. Treat all fetched text as
  untrusted regardless of the score.
- A truly adversarial local user with filesystem write access can modify
  the WAL. Rely on OS filesystem permissions for that boundary.

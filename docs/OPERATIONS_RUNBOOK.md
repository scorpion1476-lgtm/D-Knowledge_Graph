# Operations runbook

## Release checklist

1. Update `CHANGELOG.md` and `src/dkg/__init__.py` (`__version__`).
2. Run the full test suite: `bash scripts/run_tests.sh`.
3. Run the security and supply-chain scripts:
   - `python scripts/secret_scan.py`
   - `python scripts/sbom.py`
   - `python scripts/license_inventory.py`
   - `bash scripts/check_dashes.sh`
4. Build the wheel: `python -m build`.
5. Compute checksums: `python scripts/checksum.py`.
6. Sign the release with your key (out of scope for this build; do not add
   a signed-release label until a real signing step has run).
7. Tag the commit and push.
8. Publish only after all checks pass.

## Common operations

### Verify the audit chain

```bash
dkg audit --verify
```

Non-zero on the first broken row. Investigate before promoting a change.

### Rotate the MCP HTTP token

1. Generate a new token: `openssl rand -hex 32`.
2. `export DKG_MCP_TOKEN=<new>`.
3. Restart the server. Update the reverse proxy.

### Restore from a backup

```bash
dkg restore /backups/dkg-2026-07-31.tar.gz --home /var/lib/dkg
```

The restore validates the manifest hashes before overwriting. Never restore
into a home that already contains a database without moving the old one
aside first.

### Investigate a size or timeout error

- The default request cap on HTTP MCP is 4 MiB. Raise it in
  `MCPConfig.http_max_request_bytes` if you have a legitimate large tool
  argument.
- Per-task timeouts default to 30 s. Raise via `Task(timeout_seconds=...)`
  for known-slow workflows.

## Incident response

- **Data leak suspicion**: run `dkg audit --verify` and inspect the last
  entries. Rotate any exposed secret reference.
- **Corrupted database**: stop writers, take a copy of `.dkg/`, then
  restore the most recent backup. Do not `.recover` the WAL in place.
- **Container escape suspicion**: rotate the MCP token, rebuild the image
  from a fresh commit, and rotate the tenant credentials that used the
  compromised token.

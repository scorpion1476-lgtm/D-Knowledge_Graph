# Deployment guide

D-Knowledge Graph is designed to run either as a local CLI or as a
self-hosted HTTP MCP surface behind a reverse proxy. There is no cloud
requirement at any tier.

## Local single-user

```bash
python -m venv .venv
./.venv/bin/pip install -e ".[dev]"
./.venv/bin/dkg init
./.venv/bin/dkg status
```

The `.dkg` home defaults to the current working directory. Point at a
different location with `DKG_HOME=/absolute/path`.

## Docker or Podman

`docker/Dockerfile` builds a minimal image that installs the wheel and drops
privileges to a non-root user. `docker/compose.yml` mounts a host directory
as `/data`, publishes only loopback, and disables root capabilities.

```bash
docker build -t d-knowledge-graph:local -f docker/Dockerfile .
docker run --rm -v $PWD/data:/data d-knowledge-graph:local status
```

To expose the MCP HTTP surface:

```bash
docker compose --project-directory docker up
```

The compose file publishes only `127.0.0.1:8765`. Do not remove the
`127.0.0.1:` prefix; expose the service through a reverse proxy that
terminates TLS instead.

## Reverse proxy with TLS

Example nginx snippet:

```nginx
server {
  listen 443 ssl;
  server_name kg.example.internal;
  ssl_certificate     /etc/ssl/certs/kg.crt;
  ssl_certificate_key /etc/ssl/private/kg.key;
  client_max_body_size 4m;
  location / {
    proxy_pass         http://127.0.0.1:8765;
    proxy_set_header   Host $host;
    proxy_set_header   X-Forwarded-For $remote_addr;
  }
}
```

The MCP HTTP surface authorises by credential, never by peer address. Set
`DKG_MCP_TOKEN` and callers must present it. With no credential configured the
surface refuses every caller, including one on this machine, unless the
operator sets `DKG_MCP_ALLOW_UNAUTHENTICATED_LOOPBACK=1`; a browser page on the
same machine is a loopback caller too, which is why that is a deliberate opt-in
rather than the default. A non-loopback bind with no credential is refused at
startup, so the process never opens the socket. Host and Origin are validated
against allow-lists on every request. See `docs/SECURITY_MODEL.md`.

## Backup and recovery

- `dkg backup --out /backups/dkg-$(date +%F).tar.gz` produces a portable
  archive with a manifest.
- `dkg restore /backups/dkg-2026-07-31.tar.gz --home /new/dkg` validates the
  manifest before writing.
- Backups should live outside the container mount and outside the same
  filesystem when possible.

## Monitoring

- `dkg status` reports database counts and configuration.
- `GET /healthz` on the HTTP MCP surface returns `{"ok": true}` for basic
  liveness.
- `dkg audit --verify` returns non-zero on a chain break.

## Log retention

Read this before planning a retention policy, because the answer is not the
usual one.

- The application writes no log files. There is no log directory to rotate and
  nothing for `logrotate` to own. Diagnostic output goes to the standard
  streams of whatever supervises the process, so retention for that output is
  your init system's or container runtime's setting, not this application's.
- The only durable record is the audit log, and it lives inside the database as
  an append-only hash chain. That is deliberate: `dkg audit --verify` walks the
  chain, and deleting or rewriting entries breaks it. Trimming the audit log to
  save space destroys the property the audit log exists for.
- There is therefore no automated time-based retention in this build. Deletion
  is manual and is described in `docs/ADMINISTRATOR_GUIDE.md`, which also
  requires recording the deletion in the audit log so the chain stays
  explicable. A time-based retention policy is on the roadmap and is not
  shipped; do not plan around it.
- Practical retention is done with backup rotation instead. Take periodic
  `dkg backup` archives, keep them for as long as your policy requires, and
  expire the archives rather than the audit chain inside the live database.

## Upgrade and rollback

- Test the upgrade against a copy of the target home first (see
  `tests/integration/test_admin.py::test_upgrade_rollback_via_backup`).
- The schema major is recorded in `meta`; a newer application refuses to
  open a database written by an incompatible future major version.
- To roll back, restore a backup taken before the upgrade and pin the older
  wheel.

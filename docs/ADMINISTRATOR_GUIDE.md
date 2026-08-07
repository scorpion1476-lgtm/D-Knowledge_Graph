# Administrator guide

## Tenancy

Single-user mode uses the built-in `local` tenant. To add more:

```python
from dkg.core.db import open_database
from dkg.tenancy.models import create_role, create_tenant, create_principal

with open_database(".dkg/graph.sqlite") as db:
    t = create_tenant(db, "acme", quota_docs=10000)
    role = create_role(db, t.tenant_id, "reader", ["read", "export"])
    p = create_principal(db, t.tenant_id, "user", "alice", role_id=role.role_id)
```

Every write action passes through the policy engine and requires the
matching capability on the principal.

## Quotas

Quotas are recorded on the tenant row. `check_quota(db, tenant_id)` returns
whether the tenant is over quota. The current build reports quota status;
enforcement at ingest time is a follow-up. Until then, run the check on a
schedule and disable ingest for over-quota tenants.

## Audit

- `dkg audit --limit 100` shows the recent entries.
- `dkg audit --verify` walks the hash chain and returns non-zero on any
  break. Run this on a schedule and alert on failure.

## Backup and restore

- `dkg backup --out /backups/dkg-$(date +%F).tar.gz`
- `dkg restore /backups/dkg-2026-07-31.tar.gz --home /var/lib/dkg`

The restore validates every included file against the manifest's SHA-256.

## Deletion

`dkg` does not run a scheduler. Deletion is manual: run direct SQL DELETE
statements within a transaction, then rewrite the audit log entry that
justifies the deletion using `AuditLog.record`. A time-based retention
policy is on the roadmap.

## HTTP MCP

- Set `DKG_MCP_TOKEN` before starting the HTTP surface. A non-loopback bind
  without it is refused at startup, not per request.
- Authorisation is by credential only. A loopback peer is not a trusted peer,
  because a browser page on the same machine is one too. Serving loopback with
  no token needs the explicit `DKG_MCP_ALLOW_UNAUTHENTICATED_LOOPBACK=1`.
- Name any browser origin that must reach the surface in
  `DKG_MCP_ALLOWED_ORIGINS`; the list is empty by default and an unlisted
  Origin is refused. Add a served hostname to `DKG_MCP_ALLOWED_HOSTS`, or the
  Host check will refuse it.
- Restrict the served tool set with `DKG_MCP_TOOLS` when a caller needs only
  part of the surface.
- Front it with a reverse proxy for TLS.
- Enforce rate limits in the reverse proxy in addition to the in-process
  limiter.

## Health checks

- `GET /healthz` returns 200 with `{"ok": true}` while the server accepts
  requests.
- `dkg status --json` returns database counts and configuration.
- `dkg doctor` prints a broader self-check including audit-chain
  verification and adapter capability status.

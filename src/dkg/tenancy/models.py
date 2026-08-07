"""Tenant, principal, and role helpers over the SQLite schema."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..core.db import Database
from ..core.errors import ValidationError
from ..core.ids import random_id


@dataclass
class Tenant:
    tenant_id: str
    name: str
    created_at: str
    quota_docs: int | None = None
    quota_bytes: int | None = None


@dataclass
class Principal:
    principal_id: str
    tenant_id: str
    kind: str
    display_name: str | None
    role_id: str | None


@dataclass
class Role:
    role_id: str
    tenant_id: str
    name: str
    permissions: list[str] = field(default_factory=list)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_tenant(db: Database, name: str, *, quota_docs: int | None = None, quota_bytes: int | None = None) -> Tenant:
    if not name or "/" in name:
        raise ValidationError("tenant name must be a non-empty string without '/'")
    tid = "t_" + name.lower().replace(" ", "_")
    db.execute(
        "INSERT INTO tenants(tenant_id, name, created_at, quota_docs, quota_bytes) VALUES (?,?,?,?,?);",
        (tid, name, _now(), quota_docs, quota_bytes),
    )
    return Tenant(tenant_id=tid, name=name, created_at=_now(), quota_docs=quota_docs, quota_bytes=quota_bytes)


def create_role(db: Database, tenant_id: str, name: str, permissions: list[str]) -> Role:
    if not permissions:
        raise ValidationError("role must have at least one permission")
    rid = "role_" + tenant_id + "_" + name.lower().replace(" ", "_")
    db.execute(
        "INSERT INTO roles(role_id, tenant_id, name, permissions) VALUES (?,?,?,?);",
        (rid, tenant_id, name, json.dumps(permissions)),
    )
    return Role(role_id=rid, tenant_id=tenant_id, name=name, permissions=permissions)


def create_principal(
    db: Database, tenant_id: str, kind: str, display_name: str, *, role_id: str | None = None
) -> Principal:
    if kind not in ("user", "service", "agent"):
        raise ValidationError("principal kind must be user, service, or agent")
    pid = random_id("prin", length=8)
    db.execute(
        "INSERT INTO principals(principal_id, tenant_id, kind, display_name, role_id, created_at) VALUES (?,?,?,?,?,?);",
        (pid, tenant_id, kind, display_name, role_id, _now()),
    )
    return Principal(principal_id=pid, tenant_id=tenant_id, kind=kind, display_name=display_name, role_id=role_id)


def list_tenants(db: Database) -> list[Tenant]:
    return [
        Tenant(
            tenant_id=r["tenant_id"],
            name=r["name"],
            created_at=r["created_at"],
            quota_docs=r["quota_docs"],
            quota_bytes=r["quota_bytes"],
        )
        for r in db.fetchall("SELECT * FROM tenants ORDER BY name;")
    ]


def count_documents(db: Database, tenant_id: str) -> int:
    row = db.fetchone("SELECT COUNT(*) AS n FROM documents WHERE tenant_id=?;", (tenant_id,))
    return int(row["n"]) if row else 0


def check_quota(db: Database, tenant_id: str) -> dict:
    t = db.fetchone("SELECT * FROM tenants WHERE tenant_id=?;", (tenant_id,))
    if t is None:
        raise ValidationError(f"tenant not found: {tenant_id}")
    docs = count_documents(db, tenant_id)
    over = False
    reasons = []
    if t["quota_docs"] is not None and docs > int(t["quota_docs"]):
        over = True
        reasons.append(f"docs {docs} > quota {t['quota_docs']}")
    return {"tenant_id": tenant_id, "docs": docs, "over_quota": over, "reasons": reasons}


def delete_tenant(db: Database, tenant_id: str) -> dict:
    if tenant_id == "local":
        raise ValidationError("cannot delete the built-in 'local' tenant")
    with db.transaction() as conn:
        conn.execute("DELETE FROM tenants WHERE tenant_id=?;", (tenant_id,))
    return {"tenant_id": tenant_id, "deleted": True}

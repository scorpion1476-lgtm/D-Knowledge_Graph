"""Local identity adapter.

D-Knowledge_Graph does not require any external identity provider. This
adapter maps a subject string (e.g. an OS username, environment
variable, or bearer token) to a Principal row in the tenancy schema.
Callers that want OIDC or SAML can implement the same protocol against
a real provider; the base platform never needs one.

The adapter is intentionally minimal:
- ``authenticate(subject)`` returns the matching Principal or None.
- ``bind(subject, principal_id)`` stores a subject-to-principal mapping
  in the ``meta`` table so future calls resolve without a fresh
  lookup.
- Nothing here talks to the network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from ..core.db import Database
from ..core.errors import ValidationError
from .models import Principal

META_KEY = "identity_bindings_v1"


@dataclass
class LocalIdentityAdapter:
    db: Database

    def _load(self) -> dict[str, str]:
        row = self.db.fetchone(
            "SELECT value FROM meta WHERE key=?;", (META_KEY,)
        )
        if not row:
            return {}
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            return {}

    def _save(self, bindings: dict[str, str]) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?);",
            (META_KEY, json.dumps(bindings, sort_keys=True)),
        )

    def bind(self, subject: str, principal_id: str) -> None:
        if not subject or not principal_id:
            raise ValidationError("subject and principal_id are required")
        b = self._load()
        b[subject] = principal_id
        self._save(b)

    def unbind(self, subject: str) -> None:
        b = self._load()
        b.pop(subject, None)
        self._save(b)

    def resolve(self, subject: str) -> Principal | None:
        b = self._load()
        pid = b.get(subject)
        if not pid:
            return None
        row = self.db.fetchone(
            "SELECT * FROM principals WHERE principal_id=?;", (pid,)
        )
        if row is None:
            return None
        return Principal(
            principal_id=row["principal_id"],
            tenant_id=row["tenant_id"],
            kind=row["kind"],
            display_name=row["display_name"],
            role_id=row["role_id"],
        )

    def authenticate(self, subject: str) -> Principal | None:
        return self.resolve(subject)


class IdentityAdapter(Protocol):
    def authenticate(self, subject: str) -> Principal | None: ...
    def bind(self, subject: str, principal_id: str) -> None: ...
    def unbind(self, subject: str) -> None: ...


def build_default_identity(db: Database) -> LocalIdentityAdapter:
    return LocalIdentityAdapter(db)

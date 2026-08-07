"""Deterministic policy engine.

The engine takes a Request (action, subject, principal, context) and returns a
Decision (allow, deny, require_consent) with an explanation. It is used by the
CLI, MCP surface, and multi-agent coordinator to consistently gate any action
that could reach the network, mutate storage, or produce an irreversible side
effect.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal

Decision = Literal["allow", "deny", "require_consent"]


@dataclass(frozen=True)
class PolicyRequest:
    action: str
    subject_kind: str
    subject_id: str
    principal: str
    principal_permissions: frozenset[str] = frozenset({"read"})
    external_effect: bool = False
    network: bool = False
    context: dict = field(default_factory=dict)


@dataclass
class PolicyDecision:
    decision: Decision
    reason: str
    matched_rule: str

    def to_dict(self) -> dict:
        return {"decision": self.decision, "reason": self.reason, "rule": self.matched_rule}


@dataclass
class PolicyEngine:
    allow_outbound_network: bool = False
    external_actions_require_consent: bool = True
    consent_grants: set[str] = field(default_factory=set)  # opaque grant tokens

    def evaluate(self, req: PolicyRequest) -> PolicyDecision:
        # 1. Read-only actions with the read permission are always allowed.
        if req.action in _READ_ACTIONS and "read" in req.principal_permissions:
            return PolicyDecision("allow", "read action with read permission", "read-default")

        # 2. Network-touching requests require both configuration and consent.
        if req.network and not self.allow_outbound_network:
            return PolicyDecision(
                "deny",
                "outbound network is disabled in configuration",
                "network-disabled",
            )

        # 3. External effects (mutating remote resources) require explicit consent.
        if req.external_effect and self.external_actions_require_consent:
            grant = req.context.get("consent_grant")
            if grant not in self.consent_grants:
                return PolicyDecision(
                    "require_consent",
                    "action has external effect and requires an explicit consent grant",
                    "external-consent",
                )

        # 4. Write actions require the appropriate capability.
        needed = _WRITE_ACTIONS.get(req.action)
        if needed is not None and needed not in req.principal_permissions:
            return PolicyDecision(
                "deny",
                f"action {req.action} requires the {needed} capability",
                "capability-check",
            )

        # 5. Default deny if we do not recognise the action at all.
        if req.action not in _KNOWN_ACTIONS:
            return PolicyDecision(
                "deny",
                f"unknown action: {req.action}",
                "unknown-action",
            )

        return PolicyDecision("allow", "action permitted", "default-allow")

    def grant_consent(self, token: str) -> None:
        if not isinstance(token, str) or len(token) < 8:
            raise ValueError("consent token must be a string of at least 8 characters")
        self.consent_grants.add(token)

    def revoke_consent(self, token: str) -> None:
        self.consent_grants.discard(token)


_READ_ACTIONS = {
    "graph.query",
    "search.keyword",
    "search.fts",
    "search.hybrid",
    "search.explain",
    "evidence.get",
    "evidence.compare",
    "documents.list",
    "sources.list",
    "provenance.get",
    "audit.list",
    "capability.list",
    "status.get",
    "export.dryrun",
}

_WRITE_ACTIONS = {
    "ingest.file": "ingest",
    "ingest.directory": "ingest",
    "ingest.rss": "ingest",
    "ingest.web": "ingest",
    "ingest.batch": "ingest",
    "graph.mutate": "curate",
    "entities.merge": "curate",
    "claims.upsert": "curate",
    "relationships.upsert": "curate",
    "export.write": "export",
    "backup.write": "admin",
    "restore.run": "admin",
    "delete.record": "admin",
    "tenant.create": "admin",
    "tenant.delete": "admin",
    "role.assign": "admin",
    "approve.action": "approve",
    "config.update": "admin",
}

_KNOWN_ACTIONS = _READ_ACTIONS | set(_WRITE_ACTIONS.keys())


def encode_decision(d: PolicyDecision) -> str:
    return json.dumps(d.to_dict(), sort_keys=True)

"""Typed error hierarchy for D-Knowledge_Graph.

All internal failures raise subclasses of DKGError so that CLI and MCP layers
can translate them into structured responses without leaking internal details.
"""

from __future__ import annotations


class DKGError(Exception):
    """Base class for all recoverable D-Knowledge_Graph errors."""

    code: str = "dkg/error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": str(self)}


class ConfigError(DKGError):
    code = "dkg/config"


class StorageError(DKGError):
    code = "dkg/storage"


class MigrationError(StorageError):
    code = "dkg/storage/migration"


class SchemaError(StorageError):
    code = "dkg/storage/schema"


class NotFoundError(DKGError):
    code = "dkg/not_found"


class PolicyError(DKGError):
    code = "dkg/policy"


class ConsentRequiredError(PolicyError):
    code = "dkg/policy/consent_required"


class IngestError(DKGError):
    code = "dkg/ingest"


class UnsupportedFormatError(IngestError):
    code = "dkg/ingest/unsupported_format"


class SecurityError(DKGError):
    code = "dkg/security"


class SSRFError(SecurityError):
    code = "dkg/security/ssrf"


class DecompressionError(SecurityError):
    code = "dkg/security/decompression"


class ValidationError(DKGError):
    code = "dkg/validation"


class AdapterError(DKGError):
    code = "dkg/adapter"


class AdapterUnavailableError(AdapterError):
    code = "dkg/adapter/unavailable"


class BudgetExceededError(DKGError):
    code = "dkg/budget/exceeded"


class TaskCancelledError(DKGError):
    code = "dkg/task/cancelled"

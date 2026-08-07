"""Configuration for D-Knowledge_Graph.

Defaults are safe for offline single-user use. Anything that could reach the
network, cross a trust boundary, or modify system state must be explicitly
enabled through configuration or an interactive consent gate.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .errors import ConfigError

DEFAULT_DIR_NAME = ".dkg"
DEFAULT_DB_NAME = "graph.sqlite"
DEFAULT_AUDIT_NAME = "audit.log"
DEFAULT_LEDGER_NAME = "evidence.ledger"


def default_home(base: Path | None = None) -> Path:
    """Return the default data directory (project-local)."""
    root = base or Path.cwd()
    return root / DEFAULT_DIR_NAME


@dataclass
class NetworkConfig:
    allow_outbound: bool = False  # explicit opt-in required
    allowlist_domains: list[str] = field(default_factory=list)
    denylist_domains: list[str] = field(default_factory=list)
    request_timeout_seconds: float = 15.0
    max_response_bytes: int = 25 * 1024 * 1024
    user_agent: str = "D-Knowledge_Graph/0.1 (+local)"
    respect_robots: bool = True


@dataclass
class IngestConfig:
    max_document_bytes: int = 100 * 1024 * 1024
    max_archive_files: int = 4096
    max_archive_uncompressed_bytes: int = 500 * 1024 * 1024
    max_archive_ratio: float = 200.0
    chunk_paragraphs_per_chunk: int = 4
    chunk_max_chars: int = 4096


@dataclass
class MCPConfig:
    stdio_enabled: bool = True
    http_enabled: bool = False
    http_bind: str = "127.0.0.1"  # loopback default
    http_port: int = 8765
    http_bearer_token_env: str = "DKG_MCP_TOKEN"
    http_max_request_bytes: int = 4 * 1024 * 1024
    http_rate_limit_per_minute: int = 120
    # A loopback peer is not a trusted peer: a page in a browser on this machine
    # is one too. Serving without a credential is therefore off by default and
    # must be opted into deliberately, never inferred from the peer address.
    http_allow_unauthenticated_loopback: bool = False
    # Extra Host header values to answer to, beyond those derived from the bind
    # address and port. Needed only when serving under a name.
    http_allowed_hosts: list[str] = field(default_factory=list)
    # Browser origins permitted to drive the surface. Empty means none, which is
    # the correct default for a local tool that no web page should be calling.
    http_allowed_origins: list[str] = field(default_factory=list)
    # Read-only tool allowlist. Empty means serve the whole read-only registry.
    tool_allowlist: list[str] = field(default_factory=list)


@dataclass
class OrchestrationConfig:
    max_parallel_workers: int = 4
    per_task_timeout_seconds: float = 60.0
    per_task_max_retries: int = 2
    default_budget_units: int = 1000


@dataclass
class SecurityConfig:
    redact_secrets: bool = True
    block_private_ips: bool = True
    block_metadata_ips: bool = True
    block_localhost_dns_rebind: bool = True
    max_redirect_hops: int = 3
    max_xml_entity_expansion: int = 0  # disabled entirely


@dataclass
class TelemetryConfig:
    enabled: bool = False  # no telemetry by default


@dataclass
class DKGConfig:
    home: Path
    db_path: Path
    audit_path: Path
    ledger_path: Path
    network: NetworkConfig = field(default_factory=NetworkConfig)
    ingest: IngestConfig = field(default_factory=IngestConfig)
    mcp: MCPConfig = field(default_factory=MCPConfig)
    orchestration: OrchestrationConfig = field(default_factory=OrchestrationConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)

    def to_dict(self) -> dict:
        data = asdict(self)
        # Path objects are not JSON serialisable.
        for key in ("home", "db_path", "audit_path", "ledger_path"):
            data[key] = str(data[key])
        return data


def _read_env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _read_env_list(name: str, default: list[str]) -> list[str]:
    """Read a comma-separated list, keeping the configured value when unset.

    An explicitly empty value means an empty list, not the default, so an
    operator can clear a list from the environment.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


def load_config(
    home: Path | str | None = None, config_file: Path | str | None = None
) -> DKGConfig:
    """Load config from optional JSON file plus a small set of env vars.

    Environment overrides:
      DKG_HOME            - alternate data directory
      DKG_ALLOW_OUTBOUND  - set to 1 to enable outbound network at all
      DKG_MCP_HTTP        - set to 1 to enable HTTP MCP surface (loopback default)
      DKG_MCP_HTTP_BIND   - override HTTP bind address
      DKG_MCP_ALLOW_UNAUTHENTICATED_LOOPBACK
                          - set to 1 to serve loopback callers with no token.
                            Off by default: a browser page on this machine is a
                            loopback caller too, so this is a real decision.
      DKG_MCP_ALLOWED_HOSTS   - comma-separated extra Host header values
      DKG_MCP_ALLOWED_ORIGINS - comma-separated permitted browser origins
      DKG_MCP_TOOLS       - comma-separated read-only tool allowlist
      DKG_TELEMETRY       - set to 1 to opt in to local telemetry
    """
    home_path = Path(home or os.environ.get("DKG_HOME") or default_home()).resolve()
    home_path.mkdir(parents=True, exist_ok=True)

    cfg = DKGConfig(
        home=home_path,
        db_path=home_path / DEFAULT_DB_NAME,
        audit_path=home_path / DEFAULT_AUDIT_NAME,
        ledger_path=home_path / DEFAULT_LEDGER_NAME,
    )

    if config_file:
        path = Path(config_file)
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                raise ConfigError(f"config file {path} is not valid JSON: {e}") from e
            _apply_overrides(cfg, raw)

    # environment overrides (narrow, explicit set)
    cfg.network.allow_outbound = _read_env_bool(
        "DKG_ALLOW_OUTBOUND", cfg.network.allow_outbound
    )
    cfg.mcp.http_enabled = _read_env_bool("DKG_MCP_HTTP", cfg.mcp.http_enabled)
    cfg.mcp.http_bind = os.environ.get("DKG_MCP_HTTP_BIND", cfg.mcp.http_bind)
    cfg.mcp.http_allow_unauthenticated_loopback = _read_env_bool(
        "DKG_MCP_ALLOW_UNAUTHENTICATED_LOOPBACK",
        cfg.mcp.http_allow_unauthenticated_loopback,
    )
    cfg.mcp.http_allowed_hosts = _read_env_list(
        "DKG_MCP_ALLOWED_HOSTS", cfg.mcp.http_allowed_hosts
    )
    cfg.mcp.http_allowed_origins = _read_env_list(
        "DKG_MCP_ALLOWED_ORIGINS", cfg.mcp.http_allowed_origins
    )
    cfg.mcp.tool_allowlist = _read_env_list("DKG_MCP_TOOLS", cfg.mcp.tool_allowlist)
    cfg.telemetry.enabled = _read_env_bool("DKG_TELEMETRY", cfg.telemetry.enabled)
    return cfg


def _apply_overrides(cfg: DKGConfig, raw: dict) -> None:
    # only known keys are honoured; unknown keys are ignored to keep migrations tolerant
    for section_name in ("network", "ingest", "mcp", "orchestration", "security", "telemetry"):
        if section_name in raw and isinstance(raw[section_name], dict):
            section = getattr(cfg, section_name)
            for k, v in raw[section_name].items():
                if hasattr(section, k):
                    setattr(section, k, v)

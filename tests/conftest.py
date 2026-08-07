"""Shared test fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

# Make src/ importable without needing an install for tests
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest  # noqa: E402

from dkg.core.config import (  # noqa: E402
    DKGConfig,
    IngestConfig,
    MCPConfig,
    NetworkConfig,
    OrchestrationConfig,
    SecurityConfig,
    TelemetryConfig,
)
from dkg.core.db import open_database  # noqa: E402


@pytest.fixture
def tmp_home(tmp_path):
    home = tmp_path / ".dkg"
    home.mkdir(parents=True, exist_ok=True)
    return home


@pytest.fixture
def cfg(tmp_home):
    return DKGConfig(
        home=tmp_home,
        db_path=tmp_home / "graph.sqlite",
        audit_path=tmp_home / "audit.log",
        ledger_path=tmp_home / "evidence.ledger",
        network=NetworkConfig(),
        ingest=IngestConfig(),
        mcp=MCPConfig(),
        orchestration=OrchestrationConfig(max_parallel_workers=2),
        security=SecurityConfig(),
        telemetry=TelemetryConfig(),
    )


@pytest.fixture
def db(cfg):
    with open_database(cfg.db_path) as d:
        yield d

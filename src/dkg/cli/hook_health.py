"""Hook: report basic health as JSON. Used by external harnesses."""

from __future__ import annotations

import json
import sys

from ..adapters.capability import CapabilityRegistry, default_registry
from ..core.config import load_config
from ..core.db import open_database
from ..core.version import record_open


def run(out=None) -> int:
    stream = out or sys.stdout
    try:
        cfg = load_config()
        with open_database(cfg.db_path) as db:
            vi = record_open(db)
            registry: CapabilityRegistry = default_registry()
            payload = {
                "ok": True,
                "app_version": vi.app,
                "schema_major": vi.schema_major,
                "home": str(cfg.home),
                "network_allowed": cfg.network.allow_outbound,
                "telemetry_enabled": cfg.telemetry.enabled,
                "capabilities": registry.describe(),
            }
    except Exception as e:  # pragma: no cover - hook must be robust
        payload = {"ok": False, "error": str(e)}
    stream.write(json.dumps(payload, sort_keys=True) + "\n")
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())

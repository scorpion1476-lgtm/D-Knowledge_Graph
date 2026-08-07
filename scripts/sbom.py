#!/usr/bin/env python3
"""Generate a minimal CycloneDX 1.5 JSON SBOM by scanning the current venv.

Run this with the interpreter of the environment to inventory (for example the
project virtualenv), so the SBOM reflects the real declared closure. If pip
enumeration fails, this raises rather than emitting a misleading partial SBOM.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _project_meta() -> dict:
    return {
        "name": "d-knowledge-graph",
        "version": "0.1.0",
        "type": "application",
        "purl": "pkg:pypi/d-knowledge-graph@0.1.0",
        # Not an SPDX id: this licence is a LicenseRef, so it goes in "name".
        "licenses": [{"license": {"name": "LicenseRef-DKG-Source-Available-NonCommercial"}}],
    }


def _pip_components() -> list[dict]:
    try:
        raw = subprocess.check_output(
            [sys.executable, "-m", "pip", "list", "--format=json"], stderr=subprocess.DEVNULL
        )
        data = json.loads(raw.decode("utf-8"))
        return [
            {
                "type": "library",
                "name": p["name"],
                "version": p["version"],
                "purl": f"pkg:pypi/{p['name'].lower()}@{p['version']}",
            }
            for p in data
        ]
    except Exception as e:
        # Fail loud: a silent empty component list would produce a misleading
        # SBOM. The caller must know pip enumeration failed.
        raise RuntimeError(f"SBOM generation failed to enumerate packages: {e}") from e


def main() -> int:
    components = [_project_meta()] + _pip_components()
    serial = "urn:uuid:" + hashlib.sha256(
        (json.dumps(components, sort_keys=True) + datetime.now(timezone.utc).isoformat()).encode()
    ).hexdigest()[:36]
    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": serial,
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "component": _project_meta(),
            "tools": [{"vendor": "d-knowledge-graph", "name": "sbom.py", "version": "0.1.0"}],
        },
        "components": components,
    }
    out_dir = ROOT / "test-evidence"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "sbom.cdx.json"
    target.write_text(json.dumps(sbom, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

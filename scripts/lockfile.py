#!/usr/bin/env python3
"""Emit a deterministic lockfile of the current Python environment.

Writes ``requirements-lock.txt`` at the project root using
``pip freeze`` style output. Every line is ``name==version`` sorted
case-insensitively. Comments record the Python version used to
resolve.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _pip_list() -> list[dict]:
    try:
        raw = subprocess.check_output(
            [sys.executable, "-m", "pip", "list", "--format=json"],
            stderr=subprocess.DEVNULL,
        )
        return json.loads(raw.decode("utf-8"))
    except Exception as e:
        # Fail loud: a silent empty lockfile would misrepresent the closure.
        raise RuntimeError(f"lockfile generation failed to enumerate packages: {e}") from e


def main() -> int:
    pkgs = _pip_list()
    header = [
        "# D-Knowledge_Graph lockfile",
        f"# generated: {datetime.now(timezone.utc).isoformat()}",
        f"# python: {sys.version.split()[0]}",
        f"# implementation: {sys.implementation.name}",
    ]
    body: list[str] = []
    for p in sorted(pkgs, key=lambda x: x["name"].lower()):
        body.append(f"{p['name']}=={p['version']}")
    out = ROOT / "requirements-lock.txt"
    out.write_text("\n".join(header + body) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    print(f"packages: {len(body)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

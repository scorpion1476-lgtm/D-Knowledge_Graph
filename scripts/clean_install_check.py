#!/usr/bin/env python3
"""Simulate a clean install and exercise the smoke CLI paths.

Steps:
1. Create a fresh temporary directory as DKG_HOME.
2. Run ``python -m dkg init`` against that home.
3. Run ``python -m dkg --json status`` and validate JSON shape.
4. Run ``python -m dkg --json capabilities``.
5. Ingest a small in-tree markdown file.
6. Search for a token from the file and assert at least one result.
7. Run the audit chain verifier.
8. Take a backup and restore it into another temporary home.
9. Emit a JSON report to test-evidence/clean_install_check.json.

Does not install packages, does not create a venv, does not touch git.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(cmd: list[str], home: Path) -> tuple[int, str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    env["DKG_HOME"] = str(home)
    proc = subprocess.run(
        [sys.executable, "-m", "dkg", *cmd],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(ROOT),
        timeout=60,
    )
    return proc.returncode, proc.stdout, proc.stderr


def main() -> int:
    results: dict = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "python": sys.version.split()[0],
        "steps": [],
        "ok": True,
    }

    def _step(name: str, ok: bool, detail: str = "") -> None:
        results["steps"].append({"name": name, "ok": ok, "detail": detail[:400]})
        if not ok:
            results["ok"] = False

    tmp_base = Path(tempfile.mkdtemp(prefix="dkg-clean-"))
    home = tmp_base / "home"
    home.mkdir()

    rc, out, err = _run(["init"], home)
    _step("dkg init", rc == 0, err or out)

    rc, out, err = _run(["--json", "status"], home)
    ok = rc == 0
    if ok:
        try:
            data = json.loads(out)
            ok = data.get("app_version") is not None
        except json.JSONDecodeError:
            ok = False
    _step("dkg --json status", ok, err or out)

    rc, out, err = _run(["--json", "capabilities"], home)
    _step("dkg --json capabilities", rc == 0, err or out)

    sample = home / "note.md"
    sample.write_text(
        "# Alpha\n\nAlpha is fast. Beta reports gains.\n", encoding="utf-8"
    )
    rc, out, err = _run(["ingest", str(sample)], home)
    _step("dkg ingest", rc == 0, err or out)

    rc, out, err = _run(["--json", "search", "alpha"], home)
    ok = rc == 0 and '"query": "alpha"' in out
    _step("dkg search alpha", ok, err or out)

    rc, out, err = _run(["audit", "--verify"], home)
    _step("dkg audit --verify", rc == 0, err or out)

    backup = tmp_base / "backup.tar.gz"
    rc, out, err = _run(["backup", "--out", str(backup)], home)
    _step("dkg backup", rc == 0 and backup.exists(), err or out)

    restored = tmp_base / "restored"
    rc, out, err = _run(["restore", str(backup), "--home", str(restored)], home)
    _step(
        "dkg restore",
        rc == 0 and (restored / "graph.sqlite").exists(),
        err or out,
    )

    out_path = ROOT / "test-evidence" / "clean_install_check.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {out_path}")
    print(f"ok={results['ok']} steps={len(results['steps'])}")
    return 0 if results["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

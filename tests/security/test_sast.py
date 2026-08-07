"""Assert the SAST scanner finds obvious issues and passes a clean tree."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_sast():
    spec = importlib.util.spec_from_file_location(
        "dkg_sast_under_test",
        REPO_ROOT / "scripts" / "sast.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _write(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "repo"
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return root


def test_flags_exec(tmp_path):
    sast = _load_sast()
    root = _write(tmp_path, {"src/bad.py": "def f(x):\n    return exec(x)\n"})
    findings = sast.scan_tree(root)
    rules = {f.rule for f in findings}
    assert "B101" in rules


def test_flags_shell_true(tmp_path):
    sast = _load_sast()
    root = _write(
        tmp_path,
        {"src/bad.py": "import subprocess\nsubprocess.run(['ls'], shell=True)\n"},
    )
    rules = {f.rule for f in sast.scan_tree(root)}
    assert "B102" in rules


def test_flags_sql_fstring(tmp_path):
    sast = _load_sast()
    root = _write(
        tmp_path,
        {"src/bad.py": "def q(db, x):\n    db.execute(f'select * from t where a={x}')\n"},
    )
    rules = {f.rule for f in sast.scan_tree(root)}
    assert "B104" in rules


def test_real_repo_has_no_high_findings():
    sast = _load_sast()
    findings = sast.scan_tree(REPO_ROOT)
    high = [f for f in findings if f.severity == "high"]
    assert not high, f"unexpected high-severity SAST findings: {high!r}"

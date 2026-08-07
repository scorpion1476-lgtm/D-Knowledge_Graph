#!/usr/bin/env python3
"""Write SHA-256 checksums for the committed files in dist/ or test-evidence/.

Committed, not merely present. Hashing everything on disk records entries for
gitignored local files: a macOS .DS_Store, the container validation logs, and a
fresh pytest log from every run. Those files are never distributed, so
`shasum -a 256 -c SHA256SUMS` passed in the tree that generated it and exited 1
for everyone else, with one more phantom entry added on every regeneration. The
checksum file exists so a third party can verify what they received, and it has
to describe exactly that.

dist/ is not a git directory, so it falls back to hashing what is there.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _tracked_under(base: Path) -> set[Path] | None:
    """Files git tracks under base, or None if git cannot answer."""
    try:
        proc = subprocess.run(
            ["git", "ls-files", "-z", "--", str(base)],
            cwd=ROOT,
            capture_output=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    out = {
        (ROOT / rel.decode("utf-8")).resolve()
        for rel in proc.stdout.split(b"\0")
        if rel.strip()
    }
    return out or None


def _digest(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    args = argv or sys.argv[1:]
    targets = [Path(a) for a in args] or [ROOT / "dist", ROOT / "test-evidence"]
    total = 0
    for base in targets:
        if not base.exists():
            continue
        out = base / "SHA256SUMS"
        tracked = _tracked_under(base)
        lines = []
        skipped = 0
        for p in sorted(base.rglob("*")):
            if not p.is_file() or p.name == "SHA256SUMS":
                continue
            if tracked is not None and p.resolve() not in tracked:
                skipped += 1
                continue
            lines.append(f"{_digest(p)}  {p.relative_to(base)}")
            total += 1
        out.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        scope = "committed files" if tracked is not None else "files present (not a git tree)"
        note = f", skipped {skipped} untracked" if skipped else ""
        print(f"wrote {out} ({len(lines)} {scope}{note})")
    if not total:
        print("no files hashed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

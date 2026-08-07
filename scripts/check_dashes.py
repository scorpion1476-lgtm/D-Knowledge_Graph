#!/usr/bin/env python3
"""Fail if an em-dash or en-dash character appears in any tracked text file.

Two defects the shell version of this gate had, both of which made it print the
word "no" while checking nothing, are why detection lives here now.

First, it drove off an explicit target list that kept falling behind the tree.
It once covered five paths, so a dash reaching a requirement row would have been
copied into hundreds of unscanned evidence logs; a later widening still missed
the very report that claimed the scan was complete. The list is gone: the file
set comes from ``git ls-files``, so it cannot fall behind what is committed.

Second, and worse, it used ``grep -P``. BSD grep, which is what ``/usr/bin/grep``
is on macOS and therefore what the gate actually ran under, does not support
``-P``: it exited 2 with "invalid option", the error went to ``/dev/null``, the
``if`` was false, and the gate reported clean on every run regardless of
content. A gate that cannot fail is not a gate. Python behaves identically on
every platform and is already a hard requirement of this repository.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: U+2013 EN DASH and U+2014 EM DASH. Written as escapes rather than as the
#: characters themselves, because this file is tracked and the scan below reads
#: every tracked file: spelling them literally would make the detector its own
#: first offender. The gate caught exactly that on its first real run.
FORBIDDEN = ("\u2013", "\u2014")

MAX_REPORTED = 50


def tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"],
        capture_output=True,
        check=True,
        timeout=120,
    ).stdout
    return [ROOT / p.decode("utf-8", "surrogateescape") for p in out.split(b"\0") if p]


def offenders(paths: list[Path]) -> tuple[list[str], int]:
    hits: list[str] = []
    scanned = 0
    for path in paths:
        try:
            data = path.read_bytes()
        except OSError:
            continue
        # Skip binaries the way `grep -I` does: a NUL byte means not text.
        if b"\0" in data:
            continue
        scanned += 1
        text = data.decode("utf-8", "replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if any(ch in line for ch in FORBIDDEN):
                rel = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
                hits.append(f"{rel}:{lineno}")
    return hits, scanned


def main() -> int:
    hits, scanned = offenders(tracked_files())
    if hits:
        print("em-dash or en-dash characters found in tracked content:", file=sys.stderr)
        for hit in hits[:MAX_REPORTED]:
            print(f"  {hit}", file=sys.stderr)
        if len(hits) > MAX_REPORTED:
            print(f"  ... and {len(hits) - MAX_REPORTED} more", file=sys.stderr)
        return 1
    print(f"no em/en dashes in any tracked text file ({scanned} scanned)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

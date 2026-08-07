#!/usr/bin/env python3
"""Emit a per-package licence inventory as JSON and gate on copyleft.

Reads from the current Python environment via ``pip show``. Classifies each
third-party package as permissive or copyleft, and fails loud if any third-party
runtime dependency is GPL, AGPL, or LGPL. The maintainer-owned Ariadne module is
recorded separately as an intentional, documented source-available exception
(covered by the repository licence), not as a third-party dependency.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Third-party runtime dependencies must be permissive. These copyleft families
# are forbidden for third-party Python-linked runtime dependencies.
_AGPL = re.compile(r"\bagpl", re.I)
_LGPL = re.compile(r"\blgpl", re.I)
_GPL = re.compile(r"\bgpl|general public license", re.I)

# One licence covers the whole repository. Declared once here so no surface this
# script generates can drift away from the others.
PROJECT_LICENSE = "LicenseRef-DKG-Source-Available-NonCommercial"
PROJECT_NAME = "d-knowledge-graph"


def _normalise(name: str) -> str:
    """PEP 503 name normalisation, so d_knowledge_graph and D-Knowledge-Graph
    are recognised as the same distribution."""
    return re.sub(r"[-_.]+", "-", (name or "").strip()).lower()

# Ariadne is maintainer-owned source, not a pip package, so it never appears in
# the environment inventory. It is recorded here explicitly so the audit reads
# it as project source rather than as an unclassified dependency. It carries no
# licence of its own: one licence covers the whole repository, and a separate
# LicenseRef here would be a machine-readable claim that a scanner would take at
# face value long after the prose was corrected.
ARIADNE = {
    "name": "dkg.ariadne",
    "role": "maintainer-owned project source (not a third-party dependency)",
    "license": PROJECT_LICENSE,
    "license_url": "LICENSE",
    "note": (
        "Covered by the repository licence like every other file, with no separate "
        "or additional terms, and shipped in the wheel."
    ),
}


def _classify(license_text: str) -> str:
    """Classify a licence string as permissive, copyleft-forbidden, or other."""
    s = (license_text or "").lower()
    if _AGPL.search(s):
        return "copyleft-forbidden:AGPL"
    if _LGPL.search(s):
        return "copyleft-forbidden:LGPL"
    if _GPL.search(s):
        return "copyleft-forbidden:GPL"
    return "permissive-or-other"


def _pip_licenses() -> list[dict]:
    try:
        raw = subprocess.check_output(
            [sys.executable, "-m", "pip", "list", "--format=json"], stderr=subprocess.DEVNULL
        )
        packages = json.loads(raw.decode("utf-8"))
    except Exception as e:
        # Fail loud: a silent empty inventory would misrepresent the licence
        # posture. The caller must know pip enumeration failed.
        raise RuntimeError(f"licence inventory failed to enumerate packages: {e}") from e
    out = []
    for p in packages:
        name = str(p["name"])
        if _normalise(name) == _normalise(PROJECT_NAME):
            # This project is not one of its own third-party dependencies, and
            # its licence is not whatever the installed dist-info happens to
            # say. An editable install made before the relicence kept reporting
            # Apache-2.0 from stale metadata, and that stale string was written
            # straight into a committed supply-chain artifact where a scanner
            # would read it as this project's licence. The declared licence in
            # pyproject is the only source of truth for it.
            out.append(
                {
                    "name": name,
                    "version": p["version"],
                    "license": PROJECT_LICENSE,
                    "note": (
                        "This project itself, seen because it is installed into the "
                        "environment being inventoried. Its licence is taken from the "
                        "repository, not from installed metadata, which can be stale."
                    ),
                }
            )
            continue
        try:
            show = subprocess.check_output(
                [sys.executable, "-m", "pip", "show", "--verbose", name],
                stderr=subprocess.DEVNULL,
            ).decode("utf-8", errors="replace")
            licence = _license_from_show(show)
        except Exception as e:
            # Per-package metadata read failed: record it visibly rather than
            # leaving a silent blank that reads as "no licence".
            licence = f"UNKNOWN (pip show failed: {e})"
        out.append({"name": name, "version": p["version"], "license": licence})
    return out


def _license_from_show(text: str) -> str:
    """Extract a licence from ``pip show --verbose`` output.

    Prefers the ``License:`` metadata field; falls back to the Trove
    ``License ::`` classifier, since many packages leave the field blank and
    declare the licence only via classifiers.
    """
    expression = ""
    field = ""
    classifier = ""
    for line in text.splitlines():
        low = line.lower()
        if low.startswith("license-expression:"):
            expression = line.split(":", 1)[1].strip()
        elif low.startswith("license:"):
            field = line.split(":", 1)[1].strip()
        elif "license ::" in low:
            classifier = line.split("::")[-1].strip()

    def _usable(value: str) -> bool:
        return bool(value) and value.upper() not in {"UNKNOWN", "UNLICENSED", "NONE"}

    # SPDX License-Expression is the most precise, then the free-text field,
    # then the Trove classifier.
    if _usable(expression):
        return expression
    if _usable(field):
        return field
    if classifier:
        return classifier
    return "UNKNOWN"


def main() -> int:
    packages = _pip_licenses()
    for p in packages:
        p["classification"] = _classify(p["license"])
    forbidden = [p for p in packages if p["classification"].startswith("copyleft-forbidden")]

    inventory = {
        "project": {"name": "d-knowledge-graph", "license": PROJECT_LICENSE},
        "ariadne_module": ARIADNE,
        "third_party_copyleft_forbidden": [
            {"name": p["name"], "version": p["version"], "license": p["license"]} for p in forbidden
        ],
        "packages": packages,
    }
    out = ROOT / "test-evidence" / "license_inventory.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(inventory, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {out}")
    print(f"packages: {len(packages)}  forbidden copyleft (third-party): {len(forbidden)}")
    if forbidden:
        # Fail loud: a third-party GPL/AGPL/LGPL runtime dependency violates the
        # Permissive-only third-party policy. Ariadne is not counted here (it is not a
        # third-party dependency); it is the intentional source-available module.
        for p in forbidden:
            print(f"  FORBIDDEN: {p['name']} {p['version']} -> {p['license']}", file=sys.stderr)
        raise SystemExit(f"licence audit failed: {len(forbidden)} forbidden copyleft dependencies")
    print("licence audit: clean (no third-party GPL/AGPL/LGPL)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

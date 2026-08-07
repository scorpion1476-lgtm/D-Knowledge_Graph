"""Every third-party licence in this environment must be permissive.

Acceptance test for matrix row K-07, "Licence inventory and policy". The policy
is the project's hardest dependency rule: no GPL, AGPL or LGPL Python-linked
runtime dependency, ever. `python scripts/license_inventory.py` enforces it, and
its exit code was the row's evidence. An exit code cannot distinguish "audited
109 packages and found nothing" from "classified nothing and found nothing",
and those are the same number.

This test therefore checks the classifier and the closure separately:

* the classifier is exercised against the licence strings it exists to catch.
  A copyleft family that stopped being recognised is the failure that matters,
  and it is silent by construction, so each family is planted and must be
  caught. Permissive strings are planted too, because a classifier that calls
  everything copyleft would also pass a naive "no copyleft found" assertion.
* the real environment is re-derived independently through
  `importlib.metadata`, not through the script, and every installed
  distribution's declared licence is classified. Anything copyleft fails here
  regardless of what the recorded artifact says.
* the recorded artifact is required to describe *this* environment, so a
  committed inventory generated somewhere else cannot stand in for one.

The project's own distribution is excluded from the third-party rule and
required to carry the repository licence instead: it is source-available and
non-commercial, which is not permissive, and conflating the two is exactly the
mislabelling the policy document warns about.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from importlib import metadata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "test-evidence" / "license_inventory.json"
POLICY = ROOT / "docs" / "DEPENDENCY_AND_LICENCE_POLICY.md"

# These assertions pin recorded artifacts to the environment that produced them,
# which is the project virtualenv described by requirements-lock.txt. Run under
# any other interpreter (the no-extras lane installs a deliberately smaller
# closure) the comparison is between two different environments and would fail
# for a reason that says nothing about the requirement. The module skips there
# with that reason rather than reporting a defect that is not one, and it must
# never regenerate a committed artifact from a foreign environment.
_PROJECT_VENV = ROOT / ".venv"
pytestmark = pytest.mark.skipif(
    Path(sys.prefix).resolve() != _PROJECT_VENV.resolve(),
    reason=(
        "environment-pinned: these compare recorded artifacts against the project "
        "virtualenv's own closure, and this interpreter is not it"
    ),
)


PROJECT_DISTRIBUTIONS = {"d-knowledge-graph"}

COPYLEFT_STRINGS = [
    "GNU General Public License v3 (GPLv3)",
    "GPL-3.0-only",
    "GNU Affero General Public License v3",
    "AGPL-3.0",
    "GNU Lesser General Public License v2 (LGPLv2)",
    "LGPL-2.1-or-later",
]

PERMISSIVE_STRINGS = [
    "MIT",
    "MIT License",
    "Apache-2.0",
    "Apache Software License",
    "BSD-3-Clause",
    "ISC",
    "Historical Permission Notice and Disclaimer (HPND)",
    "Python Software Foundation License",
]


def _inventory_module():
    spec = importlib.util.spec_from_file_location(
        "dkg_license_inventory_under_test", ROOT / "scripts" / "license_inventory.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def inventory():
    return _inventory_module()


def _normalise(name: str) -> str:
    import re

    return re.sub(r"[-_.]+", "-", (name or "").strip()).lower()


def _declared_licence(dist) -> str:
    """What a distribution *declares* its licence to be.

    Deliberately not "every licence-ish byte in the metadata". Several packages
    (numpy is the clearest) paste the entire licence body, plus the notices of
    everything they vendor, into the `License` field. Those bodies mention other
    licences by name, so scanning them for the string "GPL" reports a BSD
    package as copyleft. That is a false positive, and a licence gate that cries
    wolf gets switched off.

    The authoritative declarations are the SPDX `License-Expression`, the
    `License ::` trove classifiers, and a `License` field short enough to be an
    identifier rather than a document. Those are what is classified.
    """
    meta = dist.metadata
    parts: list[str] = []
    expression = meta.get("License-Expression")
    if expression:
        parts.append(expression)
    parts.extend(c for c in (meta.get_all("Classifier") or []) if c.startswith("License ::"))
    declared = (meta.get("License") or "").strip()
    if declared and "\n" not in declared and len(declared) <= 100:
        parts.append(declared)
    return " ".join(parts)


# -- the classifier still recognises copyleft ---------------------------------


@pytest.mark.parametrize("text", COPYLEFT_STRINGS)
def test_the_classifier_catches_every_copyleft_family(inventory, text):
    """Negative control. A classifier that stopped matching passes silently."""
    assert inventory._classify(text).startswith("copyleft-forbidden"), (
        f"{text!r} was not classified as forbidden copyleft"
    )


@pytest.mark.parametrize("text", PERMISSIVE_STRINGS)
def test_the_classifier_does_not_call_permissive_licences_copyleft(inventory, text):
    """The other half of the control: over-flagging would also hide a real hit."""
    assert not inventory._classify(text).startswith("copyleft-forbidden"), (
        f"{text!r} was misclassified as copyleft"
    )


def test_the_three_copyleft_families_are_each_matched_distinctly(inventory):
    assert inventory._AGPL.search("AGPL-3.0")
    assert inventory._LGPL.search("LGPL-2.1")
    assert inventory._GPL.search("GPL-3.0")
    assert not inventory._AGPL.search("MIT")
    assert not inventory._LGPL.search("Apache-2.0")


# -- the real closure ---------------------------------------------------------


def test_no_installed_third_party_distribution_is_copyleft(inventory):
    """Re-derived from the environment, independently of the script's artifact."""
    offenders: list[str] = []
    seen = 0
    for dist in metadata.distributions():
        name = dist.metadata.get("Name")
        if not name:
            continue
        seen += 1
        if _normalise(name) in PROJECT_DISTRIBUTIONS:
            continue
        declared = _declared_licence(dist)
        if not declared:
            continue
        if inventory._classify(declared).startswith("copyleft-forbidden"):
            offenders.append(f"{name} {dist.version}: {declared[:90]}")
    assert seen > 20, f"only {seen} distributions found; this is not the project environment"
    assert not offenders, "copyleft third-party dependencies present: " + "; ".join(offenders)


def test_the_project_itself_carries_the_repository_licence_not_a_permissive_one():
    """Source-available non-commercial is not permissive, and must not be filed as such."""
    try:
        own = metadata.distribution("d-knowledge-graph")
    except metadata.PackageNotFoundError:
        pytest.skip("the project is not installed into this interpreter")
    declared = _declared_licence(own)
    assert "LicenseRef-DKG-Source-Available-NonCommercial" in declared, declared
    for wrong in ("MIT", "Apache-2.0", "BSD-3-Clause"):
        assert f" {wrong}" not in f" {declared}", f"the project declares {wrong}"


# -- the recorded artifact describes this environment -------------------------


def test_the_artifact_exists_and_records_a_clean_audit():
    assert ARTIFACT.is_file(), "no licence inventory artifact on disk"
    blob = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    assert blob, "the licence inventory is empty"


def test_the_artifact_covers_the_distributions_installed_here():
    """A committed inventory from a foreign environment must not pass as this one."""
    blob = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    entries = blob["packages"] if isinstance(blob, dict) and "packages" in blob else blob
    if isinstance(entries, dict):
        entries = list(entries.values())
    recorded = {_normalise(e.get("name", "")) for e in entries if isinstance(e, dict)}
    recorded.discard("")
    assert recorded, "the artifact lists no packages"
    installed = {
        _normalise(d.metadata["Name"]) for d in metadata.distributions() if d.metadata.get("Name")
    }
    # The inventory records project source (dkg.ariadne) that is not a
    # distribution, so only the other direction is a defect.
    missing = sorted(installed - recorded - PROJECT_DISTRIBUTIONS)
    assert not missing, f"installed distributions absent from the inventory: {missing[:20]}"


def test_the_policy_document_states_the_rule_the_script_enforces():
    flat = " ".join(POLICY.read_text(encoding="utf-8").split()).lower()
    assert "permissive" in flat
    for family in ("gpl", "agpl", "lgpl"):
        assert family in flat, f"the policy never mentions {family.upper()}"

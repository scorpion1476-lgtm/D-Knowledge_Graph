"""The SBOM must describe this environment, and must never be silently empty.

Acceptance test for matrix row K-03, "SBOM generation". The row's acceptance was
`python scripts/sbom.py`, whose exit code proves the script ran, not that what
it wrote is true. The two ways an SBOM goes wrong are both invisible to an exit
code:

1. It is generated from the wrong environment. An SBOM listing whatever the
   invoking shell happened to have installed is worse than none, because it
   looks authoritative. The generated component set is therefore compared
   against the distributions actually importable in this interpreter.
2. It degrades quietly. If package enumeration fails and the script emits a
   valid document with an empty component list, every downstream consumer reads
   "no dependencies" as a fact. The script promises to raise instead, and that
   promise is exercised here by breaking enumeration on purpose.

The document is also validated as CycloneDX rather than as arbitrary JSON, and
the project's own component is required to carry the repository licence, which
is a LicenseRef rather than an SPDX id and has been mislabelled before.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from importlib import metadata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "test-evidence" / "sbom.cdx.json"

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



def _sbom_module():
    spec = importlib.util.spec_from_file_location(
        "dkg_sbom_under_test", ROOT / "scripts" / "sbom.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def sbom_module():
    return _sbom_module()


@pytest.fixture(scope="module")
def generated(sbom_module, tmp_path_factory) -> dict:
    """Run the real generator in this interpreter, writing outside the repository.

    The generator's output path is `ROOT / "test-evidence"`, and that file is
    committed evidence covered by `test-evidence/SHA256SUMS`. A test that
    rewrites it changes a tracked file as a side effect and breaks the checksum
    verification running later in the same suite. Redirecting the module's ROOT
    keeps the run completely real while leaving the committed artifact alone;
    regenerating evidence is `scripts/regenerate_evidence.sh`'s job, not a test's.
    """
    out_root = tmp_path_factory.mktemp("sbom-root")
    original = sbom_module.ROOT
    sbom_module.ROOT = out_root
    try:
        assert sbom_module.main() == 0
    finally:
        sbom_module.ROOT = original
    written = out_root / "test-evidence" / "sbom.cdx.json"
    assert written.is_file(), "the generator reported success but wrote no SBOM"
    return json.loads(written.read_text(encoding="utf-8"))


def test_the_committed_sbom_exists_and_is_the_same_shape():
    """The generated document is checked above; this is the one in the tree."""
    assert ARTIFACT.is_file(), "no SBOM artifact is committed"
    blob = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    assert blob.get("bomFormat") == "CycloneDX"
    assert blob.get("components"), "the committed SBOM lists no components"


# -- it is a CycloneDX document ------------------------------------------------


def test_the_document_is_cyclonedx(generated):
    assert generated.get("bomFormat") == "CycloneDX"
    assert str(generated.get("specVersion", "")).startswith("1."), generated.get("specVersion")
    assert "components" in generated


def test_the_document_records_when_it_was_made(generated):
    timestamp = generated.get("metadata", {}).get("timestamp")
    assert timestamp, "the SBOM carries no timestamp"
    assert timestamp.startswith("20"), timestamp


def test_the_project_component_carries_the_repository_licence(generated):
    component = generated.get("metadata", {}).get("component") or {}
    assert component.get("name") == "d-knowledge-graph"
    licences = component.get("licenses") or []
    names = [entry.get("license", {}).get("name") for entry in licences]
    assert "LicenseRef-DKG-Source-Available-NonCommercial" in names, names
    # A LicenseRef is not an SPDX id, and putting it in `id` makes scanners
    # report an unknown licence rather than a source-available one.
    ids = [entry.get("license", {}).get("id") for entry in licences]
    assert not any(ids), f"the repository licence is declared as an SPDX id: {ids}"


# -- it describes this environment ---------------------------------------------


def test_every_component_has_a_name_a_version_and_a_purl(generated):
    bad = [
        c.get("name")
        for c in generated["components"]
        if not c.get("name") or not c.get("version") or not c.get("purl")
    ]
    assert not bad, f"components missing name, version or purl: {bad}"


def test_the_components_match_the_distributions_actually_installed(generated):
    """The check that makes the SBOM evidence rather than decoration."""
    listed = {c["name"].replace("_", "-").lower() for c in generated["components"]}
    installed = {
        (d.metadata["Name"] or "").replace("_", "-").lower()
        for d in metadata.distributions()
        if d.metadata.get("Name")
    }
    installed.discard("")
    assert installed, "no distributions found in this interpreter"
    missing = sorted(installed - listed)
    assert not missing, f"installed distributions absent from the SBOM: {missing[:20]}"


def test_the_recorded_versions_are_the_installed_versions(generated):
    listed = {
        c["name"].replace("_", "-").lower(): c["version"] for c in generated["components"]
    }
    mismatched: list[str] = []
    for dist in metadata.distributions():
        name = (dist.metadata.get("Name") or "").replace("_", "-").lower()
        if not name or name not in listed:
            continue
        if listed[name] != dist.version:
            mismatched.append(f"{name}: sbom {listed[name]} vs installed {dist.version}")
    assert not mismatched, f"SBOM versions disagree with the environment: {mismatched[:10]}"


def test_the_component_list_is_not_trivially_small(generated):
    assert len(generated["components"]) > 20, (
        f"only {len(generated['components'])} components; that is not this environment"
    )


# -- it fails loud -------------------------------------------------------------


def test_enumeration_failure_raises_instead_of_emitting_an_empty_sbom(sbom_module, monkeypatch):
    """The negative control, and the defect the script was written against.

    An SBOM that reports no dependencies because enumeration broke is a false
    statement with a valid schema.
    """

    def _boom(*args, **kwargs):
        raise OSError("pip is not available")

    monkeypatch.setattr(sbom_module.subprocess, "check_output", _boom)
    with pytest.raises(RuntimeError) as excinfo:
        sbom_module._pip_components()
    assert "failed to enumerate" in str(excinfo.value)


def test_malformed_pip_output_also_raises(sbom_module, monkeypatch):
    monkeypatch.setattr(
        sbom_module.subprocess, "check_output", lambda *a, **k: b"this is not json"
    )
    with pytest.raises(RuntimeError):
        sbom_module._pip_components()

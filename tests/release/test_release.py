"""Release integrity: reproducible build and the signing/provenance workflow.

The workflow validation is dependency-free and runs anywhere. The reproducible
build is capability-detected on pypa/build and is marked slow (it builds the
wheel twice). The live signing and provenance run on a release in CI and are not
exercised here.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_release_workflow_exists_and_signs_and_attests():
    assert WORKFLOW.exists()
    text = _text()
    # Keyless Sigstore signing and SLSA provenance are both wired.
    assert "sigstore sign" in text
    assert "attest-build-provenance@" in text
    # The reproducible-build check runs before the release build.
    assert "reproducible_build.py" in text


def test_release_workflow_has_oidc_permissions():
    text = _text()
    assert re.search(r"^\s*id-token:\s*write\b", text, re.M), "OIDC id-token: write is required for keyless signing"
    assert re.search(r"^\s*attestations:\s*write\b", text, re.M), "attestations: write is required for provenance"


def test_release_workflow_actions_are_sha_pinned():
    text = _text()
    uses = re.findall(r"uses:\s*(\S+)", text)
    assert uses, "expected at least one action"
    for ref in uses:
        assert re.search(r"@[0-9a-f]{40}$", ref), f"action {ref} is not pinned to a 40-hex commit SHA"


def test_release_workflow_valid_yaml_when_pyyaml_present():
    try:
        import yaml
    except ImportError:
        return
    doc = yaml.safe_load(_text())
    job = doc["jobs"]["build-sign-attest"]
    assert job["permissions"]["id-token"] == "write"
    assert job["permissions"]["attestations"] == "write"


@pytest.mark.slow
def test_wheel_builds_reproducibly_and_includes_ariadne():
    sys.path.insert(0, str(ROOT / "scripts"))
    import reproducible_build

    if not reproducible_build._build_available():
        pytest.skip("pypa/build not installed (install the 'release' extra)")
    result = reproducible_build.run()
    assert result["status"] == "ran"
    assert result["reproducible"] is True, result
    # One licence now covers the whole repository, so Ariadne ships in the
    # wheel. Asserting inclusion keeps a re-introduced exclusion caught.
    assert result["ariadne_included"] is True, result
    assert result["sha256_build1"] == result["sha256_build2"]

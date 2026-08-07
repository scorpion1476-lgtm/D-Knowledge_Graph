"""The environment diagnostic must answer the questions it claims to answer.

`scripts/probe_environment.py` exists so that "the install failed" and "this
shell cannot reach anything" stop looking identical. That only works if one
report carries all of it: the interpreter, the extras, the external binaries,
the staged models, and the package-index reachability, together.

These tests cross-check the report against the things it describes rather than
against strings the report also wrote. The extras it lists are compared with the
extras `pyproject.toml` actually declares, the binary groups with the binaries
the product's own capability modules look up, and the model locations with the
paths the adapters resolve. A report that drifts from any of those fails here.

The reachability probe is never exercised over the network from a test. It is
called with the probe disabled, and the test asserts the not-attempted state is
distinguishable from a failure, which is the property that matters.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "probe_environment.py"


@pytest.fixture(scope="module")
def probe():
    """Load the script by path: scripts/ is not an importable package."""
    spec = importlib.util.spec_from_file_location("dkg_probe_environment", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def report(probe) -> dict:
    return probe.build_report(attempt_network=False)


def _declared_extras_from_pyproject() -> set[str]:
    """Read the extras independently of the script under test.

    tomllib when the interpreter has it, otherwise a deliberately different
    reading of the same file, so this is a second opinion rather than a copy of
    the parser being checked.
    """
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    try:
        import tomllib
    except ModuleNotFoundError:
        pass
    else:
        return set(tomllib.loads(text).get("project", {}).get("optional-dependencies", {}))
    inside = False
    found: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "[project.optional-dependencies]":
            inside = True
            continue
        if not inside:
            continue
        if stripped.startswith("[") and stripped.endswith("]") and "=" not in stripped:
            break
        m = re.match(r'^([A-Za-z][A-Za-z0-9._-]*)\s*=\s*\[', stripped)
        if m:
            found.add(m.group(1))
    return found


# -- the report carries every part the requirement names --------------------


def test_report_has_every_section_the_requirement_names(report):
    for key in ("interpreter", "extras", "binaries", "models", "network_egress_pypi"):
        assert key in report, key


def test_interpreter_section_describes_this_interpreter(report):
    interpreter = report["interpreter"]
    assert interpreter["executable"] == sys.executable
    assert interpreter["version"] == ".".join(str(p) for p in sys.version_info[:3])
    assert interpreter["in_virtualenv"] == (sys.prefix != sys.base_prefix)
    assert interpreter["platform"]


# -- extras -----------------------------------------------------------------


def test_extras_match_what_pyproject_declares(report):
    """A silently short list is the failure mode worth catching.

    The text fallback used on Python 3.10 originally folded each extra's
    documentation comment into the extra's own name, so the report named
    eighteen extras that no one could install. Comparing with a second reading
    of pyproject catches that class of bug.
    """
    assert set(report["extras"]["declared"]) == _declared_extras_from_pyproject()


def test_every_extra_name_is_a_plausible_extra_name(report):
    for name in report["extras"]["declared"]:
        assert re.fullmatch(r"[a-z][a-z0-9._-]*", name), name


def test_extras_are_partitioned_into_installed_and_not(report):
    extras = report["extras"]
    assert set(extras["installed"]) | set(extras["not_installed"]) == set(extras["declared"])
    assert not set(extras["installed"]) & set(extras["not_installed"])


def test_an_extra_is_installed_only_when_its_requirements_resolve(report):
    """The verdict must follow from the per-requirement detail beside it."""
    for name, detail in report["extras"]["detail"].items():
        missing = [
            dist
            for dist, req in detail["requirements"].items()
            if req["installed_version"] is None
        ]
        assert detail["missing"] == sorted(missing), name
        assert detail["installed"] == (not missing), name
        assert detail["declares_no_python_requirement"] == (not detail["requirements"]), name


def test_a_missing_distribution_makes_its_extra_not_installed(probe, monkeypatch):
    """Fake one absent distribution and the verdict must move."""
    declared, _ = probe._declared_extras()
    target = next(name for name in sorted(declared) if declared[name])
    victim = probe._requirement_name(declared[target][0])
    real = probe._installed_version
    monkeypatch.setattr(
        probe, "_installed_version", lambda dist: None if dist == victim else real(dist)
    )
    extras = probe._probe_extras()
    assert target in extras["not_installed"]
    assert victim in extras["detail"][target]["missing"]


# -- external binaries ------------------------------------------------------


def test_binary_groups_cover_both_planes(report):
    assert set(report["binaries"]) == {"development", "media", "code"}


def test_media_binaries_match_the_ones_the_media_plane_looks_up(probe):
    """The probe must not drift from the module that actually resolves them.

    This caught a real omission on its first run: the media plane falls back to
    legacy ImageMagick `convert` for HEIC decode, and the diagnostic did not
    report it, so a machine that had only `convert` read as having no HEIC
    decoder at all.
    """
    source = (ROOT / "src" / "dkg" / "media" / "capability.py").read_text(encoding="utf-8")
    looked_up = set(re.findall(r'_which\("([a-z0-9-]+)"\)', source))
    probed = {name for name, _ in probe.BINARY_GROUPS["media"]}
    assert looked_up <= probed, f"media plane looks up {sorted(looked_up - probed)} unreported"


def test_code_plane_binaries_match_the_servers_the_code_plane_resolves(probe):
    source = (ROOT / "src" / "dkg" / "code" / "lsp.py").read_text(encoding="utf-8")
    looked_up = set(re.findall(r'_resolve_binary\("([a-z0-9-]+)"', source))
    probed = {name for name, _ in probe.BINARY_GROUPS["code"]}
    assert looked_up <= probed, f"code plane resolves {sorted(looked_up - probed)} unreported"
    assert "node" in probed, "both language servers need Node; an absent Node is the first cause"


def test_every_binary_entry_says_what_it_is_needed_for(report):
    for group, block in report["binaries"].items():
        for name, detail in block["detail"].items():
            assert detail["needed_for"].strip(), f"{group}/{name}"
            assert (detail["path"] is None) == (name in block["absent"]), f"{group}/{name}"


# -- staged models ----------------------------------------------------------


def test_model_locations_match_the_paths_the_adapters_resolve():
    """Each staged path in the probe must be the one its adapter defaults to."""
    embedding = (ROOT / "src" / "dkg" / "adapters" / "embedding.py").read_text(encoding="utf-8")
    reranker = (ROOT / "src" / "dkg" / "adapters" / "reranker.py").read_text(encoding="utf-8")
    detect = (ROOT / "src" / "dkg" / "media" / "detect.py").read_text(encoding="utf-8")
    assert '"models" / "embeddings" / "potion-base-8M"' in embedding
    assert '"models" / "reranker"' in reranker
    assert '"models" / "media-detect"' in detect


def test_every_model_entry_names_its_override_and_its_consequence(report):
    for name, detail in report["models"].items():
        if name == "provenance_record":
            continue
        assert detail["env_var"].startswith("DKG_"), name
        assert detail["if_absent"].strip(), name
        assert isinstance(detail["staged"], bool), name


def test_a_staged_model_directory_is_detected(probe, monkeypatch, tmp_path):
    """Point the override at a real directory and the report must see it."""
    staged = tmp_path / "potion"
    staged.mkdir()
    (staged / "model.safetensors").write_bytes(b"not a real model")
    monkeypatch.setenv("DKG_EMBEDDING_MODEL", str(staged))
    models = probe._probe_models()
    assert models["embeddings"]["staged"] is True
    assert models["embeddings"]["from_env"] is True
    assert models["embeddings"]["files"] == 1


def test_the_asr_model_is_reported_absent_when_no_path_is_set(probe, monkeypatch):
    monkeypatch.delenv("DKG_ASR_MODEL", raising=False)
    assert probe._probe_models()["asr"]["staged"] is False


# -- reachability, disclosed and skippable ----------------------------------


def test_the_reachability_probe_names_the_host_it_contacts(probe):
    assert probe.PYPI_PROBE_URL.startswith("https://pypi.org/")
    assert probe.PYPI_PROBE_URL in probe.__doc__


def test_a_skipped_reachability_check_is_not_reported_as_a_failure(report):
    network = report["network_egress_pypi"]
    assert network["attempted"] is False
    assert network["ok"] is None, "a check that was never made must not read as unreachable"
    assert network["error"] is None
    assert network["url"] == "https://pypi.org/pypi/pip/json"


def test_the_report_discloses_its_only_outbound_request(report):
    assert "pypi.org" in report["disclosure"]
    assert "--offline" in report["disclosure"]


def test_offline_mode_makes_no_outbound_request(probe, monkeypatch):
    """Not a promise about strings: fail the moment urlopen is touched."""

    def explode(*a, **k):  # pragma: no cover - the point is that it never runs
        raise AssertionError("--offline still attempted an outbound request")

    monkeypatch.setattr(probe.urllib.request, "urlopen", explode)
    probe.build_report(attempt_network=False)


# -- it runs as a script, which is how a user will run it -------------------


def test_the_script_runs_and_prints_one_pasteable_json_document(tmp_path):
    out = tmp_path / "probe.json"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--offline", "--out", str(out)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    printed = json.loads(proc.stdout)
    written = json.loads(out.read_text(encoding="utf-8"))
    assert printed == written
    for key in ("interpreter", "extras", "binaries", "models", "network_egress_pypi"):
        assert key in printed


def test_the_script_does_not_overwrite_the_tracked_artifact_when_out_is_given(tmp_path):
    """--out must be honoured, or a diagnostic run would rewrite tracked evidence."""
    before = (ROOT / "test-evidence" / "environment_probe.json").read_bytes()
    out = tmp_path / "elsewhere.json"
    subprocess.run(
        [sys.executable, str(SCRIPT), "--offline", "--out", str(out)],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    assert out.is_file()
    assert (ROOT / "test-evidence" / "environment_probe.json").read_bytes() == before

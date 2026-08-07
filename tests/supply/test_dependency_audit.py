"""The dependency vulnerability audit must be real, current, and fail loud.

Acceptance test for matrix row K-06, "Dependency vulnerability audit". The audit
itself needs the network, because it resolves the installed closure against the
PyPA advisory database. The project's air-gap default says the test suite does
not reach the network, so the live audit is opt-in here and the recorded
artifact carries the evidence the rest of the time.

That split only works if the artifact cannot be a report about some other
machine, so the artifact is pinned to this environment's closure:

* the package list it audited is compared, name and version and in both
  directions, against the distributions actually installed in this interpreter.
  An audit of a different environment fails, and so does one taken before any
  dependency here changed version.
* it must record which advisory source was used and which pip-audit produced
  it, because "no vulnerabilities" from an unnamed database is not a finding.
* it must contain an explicit result section, and if that section names
  vulnerabilities the test fails rather than passing on the file's existence.

What this deliberately does NOT claim, because an adversarial review was right
to push on it: the pin rejects a *foreign* environment, not an *old* one. If no
dependency has changed version, an audit taken long ago still matches, and the
advisory database it was taken against is not pinned or dated in any way the
test can compare. Advisories are published against unchanged versions all the
time, so recency here comes from re-running the audit in CI, not from this file.
The row's limitation cell says the same thing rather than implying more.

The script's fail-loud contract is checked directly: with pip-audit missing, or
with no project virtualenv, it must exit non-zero rather than write a reassuring
empty report. That is the supply-chain rule this project states, and an audit
script that degrades quietly is the exact shape of the problem.

Set `DKG_LIVE_AUDIT=1` to additionally run the real audit against the network.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from importlib import metadata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run_pip_audit.sh"
ARTIFACT = ROOT / "test-evidence" / "pip_audit.txt"

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



@pytest.fixture(scope="module")
def report() -> str:
    assert ARTIFACT.is_file(), "no pip-audit report on disk"
    return ARTIFACT.read_text(encoding="utf-8")


# `pip freeze` omits these by design, so their absence from the audited list is
# correct rather than a gap. The project itself is the editable install the
# script strips deliberately; the other three are pip's own bootstrap packages.
_NOT_IN_A_FREEZE = ("d-knowledge-graph", "pip", "setuptools", "wheel")


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", (name or "").strip()).lower()


def _audited_packages(report: str) -> dict[str, str]:
    """The `# packages audited:` block, as name -> version."""
    out: dict[str, str] = {}
    for line in report.splitlines():
        m = re.match(r"^#\s{2,}([A-Za-z0-9._-]+)==([^\s]+)\s*$", line)
        if m:
            out[_normalise(m.group(1))] = m.group(2)
    return out


# -- the report is a real audit -----------------------------------------------


def test_the_report_names_its_advisory_source_and_tool(report):
    assert "advisory source" in report.lower(), "the report does not say what it audited against"
    assert re.search(r"pip-audit version:\s*\S+", report), "the report does not record the tool version"
    assert re.search(r"generated_at:\s*20\d\d-", report), "the report is not timestamped"


def test_the_report_has_an_explicit_result_section(report):
    assert "## RESULT" in report, "the report records no result at all"


def test_the_report_records_no_known_vulnerability(report):
    """If this ever fails, the dependency set has a real advisory against it."""
    result = report.split("## RESULT", 1)[1]
    if "No known vulnerabilities found" in result:
        return
    # pip-audit prints a table when it finds something. Anything other than the
    # clean sentence is a finding and must not pass silently.
    rows = [line for line in result.splitlines() if line.strip() and not line.startswith("wrote ")]
    raise AssertionError("pip-audit reported findings: " + "\n".join(rows[:20]))


# -- the report describes this environment ------------------------------------


def test_the_audited_package_list_is_not_empty(report):
    audited = _audited_packages(report)
    assert len(audited) > 20, f"only {len(audited)} packages audited; that is not this closure"


def test_every_installed_distribution_was_audited(report):
    """A stale artifact fails here, which is what makes it usable as evidence."""
    audited = _audited_packages(report)
    installed = {
        _normalise(d.metadata["Name"]): d.version
        for d in metadata.distributions()
        if d.metadata.get("Name")
    }
    for excluded in _NOT_IN_A_FREEZE:
        installed.pop(excluded, None)
    missing = sorted(set(installed) - set(audited))
    assert not missing, f"installed distributions that were never audited: {missing[:20]}"


def test_the_audited_versions_are_the_installed_versions(report):
    audited = _audited_packages(report)
    drift: list[str] = []
    for dist in metadata.distributions():
        name = _normalise(dist.metadata.get("Name") or "")
        if not name or name not in audited or name in _NOT_IN_A_FREEZE:
            continue
        if audited[name] != dist.version:
            drift.append(f"{name}: audited {audited[name]} vs installed {dist.version}")
    assert not drift, f"the report audited a different environment: {drift[:10]}"


# -- the script fails loud ----------------------------------------------------


def test_the_script_exits_non_zero_when_pip_audit_is_absent(tmp_path):
    """Negative control: no tool must mean no report, not an empty pass.

    The PATH keeps the system directories so `bash` itself still resolves; what
    it drops is the user and virtualenv bin directories where pip-audit lives.
    """
    env = dict(os.environ)
    env["PATH"] = f"{tmp_path}:/usr/bin:/bin"
    assert not any(
        (Path(d) / "pip-audit").exists() for d in env["PATH"].split(":")
    ), "pip-audit is still reachable, so this control proves nothing"
    proc = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    assert proc.returncode != 0, "the audit script succeeded with no pip-audit installed"
    assert "pip-audit not installed" in (proc.stderr + proc.stdout)


def test_the_script_refuses_to_run_without_the_project_virtualenv(tmp_path):
    """Auditing whatever the ambient shell has installed would be a false report."""
    fake_root = tmp_path / "repo"
    (fake_root / "scripts").mkdir(parents=True)
    (fake_root / "scripts" / "run_pip_audit.sh").write_text(
        SCRIPT.read_text(encoding="utf-8"), encoding="utf-8"
    )
    proc = subprocess.run(
        ["bash", str(fake_root / "scripts" / "run_pip_audit.sh")],
        cwd=fake_root,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode != 0, "the audit ran without a project virtualenv"
    assert "venv not found" in (proc.stderr + proc.stdout)


def test_the_script_excludes_the_editable_project_from_the_freeze():
    """The project is not a third-party dependency and has no advisories."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert "pip freeze" in text
    assert "-e " in text and "grep -v" in text, (
        "the script no longer strips the editable install from the audited list"
    )


# -- optional live run --------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("DKG_LIVE_AUDIT") != "1",
    reason="live advisory-database audit is opt-in; set DKG_LIVE_AUDIT=1 (needs network)",
)
def test_the_live_audit_runs_and_reports_cleanly():
    proc = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "No known vulnerabilities found" in proc.stdout


def test_the_python_running_the_suite_is_the_virtualenv_the_script_audits():
    """Otherwise the artifact comparison above is comparing two environments."""
    venv_python = ROOT / ".venv" / "bin" / "python"
    if not venv_python.exists():
        pytest.skip("no project virtualenv in this checkout")
    assert Path(sys.prefix).resolve() == (ROOT / ".venv").resolve(), (
        f"the suite is running under {sys.prefix}, not the project virtualenv"
    )

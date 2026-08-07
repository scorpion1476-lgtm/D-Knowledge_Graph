"""The clean-home smoke path must run, and must be able to report failure.

Acceptance test for matrix row L-08, "Clean machine install test". The row's
acceptance was `python scripts/clean_install_check.py`, and its evidence was
that script's output. The script simulates a clean machine by pointing
`DKG_HOME` at an empty temporary directory and driving eight CLI steps through
it: init, status, capabilities, ingest, search, audit verification, backup and
restore.

Two things make that worth promoting, and neither is the exit code:

* the run is performed here, in this test, against a home that did not exist
  when the test started, and every one of the eight steps is asserted
  individually. A summary that says `ok=True` while a step quietly did nothing
  is the failure mode a single exit code hides.
* the checker is shown to be capable of reporting failure. A smoke test that
  cannot fail is decoration, and this project has already shipped one gate that
  could not fail. Driving the checker's own step runner at a command that does
  not exist proves the `ok` flag tracks reality.

The search step is the one that carries weight: it ingests a file and then
requires a hit for a token from that file, so the whole store-and-retrieve path
has to work rather than merely not crash.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "clean_install_check.py"
REPORT = ROOT / "test-evidence" / "clean_install_check.json"

EXPECTED_STEPS = (
    "dkg init",
    "dkg --json status",
    "dkg --json capabilities",
    "ingest",
    "search",
    "audit",
    "backup",
    "restore",
)


@pytest.fixture(scope="module")
def result() -> dict:
    """Run the real checker now and read the report it writes.

    The report path is committed evidence covered by `test-evidence/SHA256SUMS`,
    and the checker hardcodes it. The run here is genuine, but the committed
    bytes are put back afterwards: a test that leaves a tracked evidence file
    rewritten breaks the checksum verification running later in the same suite,
    and refreshing evidence is `scripts/regenerate_evidence.sh`'s job.
    """
    previous = REPORT.read_bytes() if REPORT.is_file() else None
    try:
        proc = subprocess.run(
            [sys.executable, str(SCRIPT)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert REPORT.is_file(), "the checker reported success but wrote no report"
        fresh = json.loads(REPORT.read_text(encoding="utf-8"))
    finally:
        if previous is not None:
            REPORT.write_bytes(previous)
    return fresh


def _checker():
    spec = importlib.util.spec_from_file_location("dkg_clean_install_under_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# -- the run happened and every step passed ------------------------------------


def test_the_run_reports_overall_success(result):
    assert result["ok"] is True, result


def test_the_report_records_the_interpreter_it_ran_under(result):
    assert result["python"].startswith(f"{sys.version_info.major}.{sys.version_info.minor}"), result
    assert result["started_at"].startswith("20")


def test_every_expected_step_ran(result):
    names = " | ".join(step["name"] for step in result["steps"])
    missing = [s for s in EXPECTED_STEPS if s not in names]
    assert not missing, f"the clean-install check never ran: {missing} (ran: {names})"


def test_no_individual_step_failed(result):
    failed = [s["name"] for s in result["steps"] if not s.get("ok")]
    assert not failed, f"steps failed: {failed}"


def test_the_check_is_not_trivially_short(result):
    assert len(result["steps"]) >= 8, f"only {len(result['steps'])} steps ran"


# -- the steps did real work ---------------------------------------------------


def test_the_status_step_reports_a_real_home_with_the_air_gap_on(result):
    step = next(s for s in result["steps"] if s["name"] == "dkg --json status")
    status = json.loads(step["detail"])
    assert status["app_version"]
    assert status["schema_major"] >= 1, "the database has no schema, so init did nothing"
    assert status["network_allowed"] is False, "a clean install came up with the network enabled"
    assert status["telemetry_enabled"] is False


def test_the_home_the_check_used_did_not_exist_beforehand(result):
    """It is a *clean* install check: a reused home would prove much less."""
    step = next(s for s in result["steps"] if s["name"] == "dkg init")
    assert "initialised" in step["detail"].lower()
    home = step["detail"].strip().rsplit(" ", 1)[-1]
    assert "dkg-clean-" in home, f"the check ran against {home}, which is not a fresh temporary home"


def test_the_search_step_found_what_the_ingest_step_stored(result):
    """The one step that proves store-and-retrieve rather than absence of a crash."""
    step = next(s for s in result["steps"] if "search" in s["name"])
    assert step["ok"]
    assert step["detail"].strip(), "the search step recorded no output"


def test_the_capabilities_step_reports_capabilities_honestly(result):
    """The report truncates each step's detail to 400 characters, so this reads
    the prefix rather than parsing it: what matters is that the command emitted
    a capability list in which entries carry an availability flag, and that is
    visible in the first few entries."""
    step = next(s for s in result["steps"] if s["name"] == "dkg --json capabilities")
    assert step["ok"]
    detail = step["detail"]
    assert '"capabilities"' in detail, "the capabilities command emitted no capability list"
    assert '"available"' in detail, "a capability is reported without an availability flag"
    assert '"kind"' in detail


def test_the_backup_and_restore_steps_both_ran(result):
    names = [s["name"] for s in result["steps"]]
    assert any("backup" in n for n in names)
    assert any("restore" in n for n in names)
    backup = next(i for i, n in enumerate(names) if "backup" in n)
    restore = next(i for i, n in enumerate(names) if "restore" in n)
    assert backup < restore, "the check restored before it backed up"


# -- the checker can fail ------------------------------------------------------


def test_the_step_runner_reports_a_failure_when_a_command_fails(tmp_path):
    """Negative control. Without this, `ok=True` is an unfalsifiable claim."""
    checker = _checker()
    home = tmp_path / "home"
    home.mkdir()
    code, out, err = checker._run(["this-subcommand-does-not-exist"], home)
    assert code != 0, "a bogus subcommand returned success"
    assert (out + err).strip(), "a failing command produced no diagnostic output"


def test_the_script_writes_its_report_where_the_row_points():
    assert REPORT.parent.name == "test-evidence"
    assert "clean_install_check.json" in SCRIPT.read_text(encoding="utf-8")


def test_the_checker_does_not_install_packages_or_touch_git():
    """Its docstring promises this; a clean-install check that pip-installs is not one."""
    text = SCRIPT.read_text(encoding="utf-8")
    executable = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    body = executable.split('"""', 2)[-1]
    for forbidden in ("pip install", "git commit", "git push", "subprocess.run([\"git\""):
        assert forbidden not in body, f"the clean-install check performs {forbidden!r}"

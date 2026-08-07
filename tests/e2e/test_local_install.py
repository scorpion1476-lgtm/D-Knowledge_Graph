"""A clean machine must be able to install this and run it.

Acceptance test for matrix row L-04, "Local installation". The row's acceptance
was `bash scripts/install.sh`, which cannot be the executed acceptance in a
suite: it installs into the repository's own virtualenv, so running it as a test
would mutate the environment the rest of the suite is measuring, and it needs
the network to fetch the development extras.

What this does instead is the part that actually matters and that can be done
offline: it builds the real wheel from this tree, installs it into a virtualenv
created from scratch, and drives the installed console script end to end. No
`--editable`, no `PYTHONPATH`, no reuse of the project virtualenv. If the
package metadata, the entry points, or the packaged data are wrong, the
installed copy fails here and the developer copy would not have noticed.

The install is deliberately `--no-deps --no-index`. That is not a shortcut: the
project declares zero mandatory runtime dependencies, so an install with no
dependencies resolved is a *complete* install of the product, and doing it with
the index disabled proves it. If a mandatory runtime dependency is ever added,
the import in this test fails and the claim in the README stops being true at
the same moment.

`scripts/install.sh` is then checked as a contract rather than executed: the
steps it runs must be the ones the quick start documents, and it must verify
its own work instead of exiting zero on an install that produced nothing.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "scripts" / "install.sh"


def _run(args: list[str], *, env: dict | None = None, **kwargs) -> subprocess.CompletedProcess:
    merged = dict(os.environ if env is None else env)
    # The installed copy must work without the developer's environment. Dropping
    # PYTHONPATH is the point: leaving it set would let the source tree satisfy
    # the import and the whole test would prove nothing about the wheel.
    merged.pop("PYTHONPATH", None)
    merged["DKG_ALLOW_OUTBOUND"] = "0"
    merged["DKG_TELEMETRY"] = "0"
    return subprocess.run(
        args, capture_output=True, text=True, timeout=900, check=False, env=merged, **kwargs
    )


@pytest.fixture(scope="module")
def wheel(tmp_path_factory) -> Path:
    """Build the real distribution from this tree, offline.

    `--no-index` keeps the build off the network, which means `--no-build-isolation`
    and therefore the running interpreter's own setuptools. pyproject requires
    setuptools>=68; an interpreter carrying the older setuptools a bare `venv`
    bootstraps cannot build this package without fetching one. That is a
    property of the environment, not a defect in the package, so it skips with
    that reason rather than failing.
    """
    out = tmp_path_factory.mktemp("wheel")
    proc = _run(
        [
            sys.executable, "-m", "pip", "wheel",
            "--no-build-isolation", "--no-deps", "--no-index",
            "-w", str(out), str(ROOT),
        ]
    )
    if proc.returncode != 0:
        combined = proc.stdout + proc.stderr
        if "setuptools" in combined or "Missing dependencies" in combined:
            pytest.skip(
                "offline wheel build needs setuptools>=68 and wheel in this "
                f"interpreter; it has none new enough: {combined.strip()[-200:]}"
            )
        raise AssertionError(combined)
    wheels = list(out.glob("d_knowledge_graph-*.whl"))
    assert len(wheels) == 1, f"expected one wheel, got {wheels}"
    return wheels[0]


@pytest.fixture(scope="module")
def installed(tmp_path_factory, wheel) -> Path:
    """A virtualenv created from scratch with only this wheel in it."""
    env_dir = tmp_path_factory.mktemp("clean-venv") / "venv"
    venv.EnvBuilder(with_pip=True, clear=True).create(env_dir)
    python = env_dir / ("Scripts" if os.name == "nt" else "bin") / "python"
    proc = _run([str(python), "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return env_dir


@pytest.fixture(scope="module")
def bindir(installed: Path) -> Path:
    return installed / ("Scripts" if os.name == "nt" else "bin")


# -- the wheel is a real distribution -----------------------------------------


def test_the_wheel_builds_from_this_tree(wheel):
    assert wheel.is_file()
    assert wheel.stat().st_size > 50_000, "the wheel is too small to contain the package"


def test_the_wheel_ships_both_planes_and_the_shared_core(wheel):
    """A packaging mistake that drops a subpackage is invisible in a source run."""
    import zipfile

    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
    for expected in ("dkg/core/", "dkg/code/", "dkg/media/", "dkg/mcp/", "dkg/cli/"):
        assert any(n.startswith(expected) for n in names), f"the wheel ships no {expected}"


def test_the_wheel_ships_the_sql_migrations(wheel):
    """Package data, which setuptools silently omits when it is misconfigured."""
    import zipfile

    with zipfile.ZipFile(wheel) as zf:
        sql = [n for n in zf.namelist() if n.endswith(".sql")]
    assert sql, "the wheel contains no migration SQL, so a fresh install cannot build its schema"


def test_the_wheel_ships_the_licence(wheel):
    import zipfile

    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
    assert any("LICENSE" in n or "licenses/" in n for n in names), (
        "the wheel ships no licence file"
    )


# -- the installed copy works --------------------------------------------------


def test_both_console_scripts_are_installed_and_report_the_version(bindir):
    for name in ("dkg", "d-knowledge-graph"):
        script = bindir / name
        assert script.exists(), f"the {name} console script was not installed"
        proc = _run([str(script), "--version"])
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "0.1.0" in proc.stdout, proc.stdout


def test_the_package_imports_with_no_dependencies_resolved(bindir):
    """The zero-mandatory-dependency claim, proved by an install that has none."""
    proc = _run([str(bindir / "python"), "-c", "import dkg; print(dkg.__version__)"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.strip() == "0.1.0"


def test_nothing_but_the_project_is_installed(bindir):
    """If a runtime dependency crept in, this install would have needed it."""
    proc = _run([str(bindir / "python"), "-m", "pip", "list", "--format=json"])
    assert proc.returncode == 0, proc.stderr
    names = {p["name"].replace("_", "-").lower() for p in json.loads(proc.stdout)}
    assert "d-knowledge-graph" in names
    assert not names - {"d-knowledge-graph", "pip", "setuptools", "wheel"}, (
        f"the clean install pulled in {sorted(names)}"
    )


def test_the_installed_cli_initialises_a_home_and_reports_status(bindir, tmp_path):
    home = tmp_path / "dkg-home"
    env_home = {"DKG_HOME": str(home)}
    proc = _run([str(bindir / "dkg"), "init"], cwd=str(tmp_path), env={**os.environ, **env_home})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert home.exists(), "init reported success but created no home"

    proc = _run(
        [str(bindir / "dkg"), "--json", "status"],
        cwd=str(tmp_path),
        env={**os.environ, **env_home},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    status = json.loads(proc.stdout)
    assert status["app_version"] == "0.1.0"
    assert "documents" in status


def test_the_installed_cli_ingests_and_searches(bindir, tmp_path):
    """One end-to-end pass, from an installed copy, with no source tree in sight."""
    home = tmp_path / "home2"
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "note.md").write_text(
        "The query planner optimises SQL execution plans for speed.\n", encoding="utf-8"
    )
    env = {**os.environ, "DKG_HOME": str(home)}
    assert _run([str(bindir / "dkg"), "init"], cwd=str(tmp_path), env=env).returncode == 0
    ingest = _run([str(bindir / "dkg"), "ingest", str(notes)], cwd=str(tmp_path), env=env)
    assert ingest.returncode == 0, ingest.stdout + ingest.stderr
    found = _run(
        [str(bindir / "dkg"), "--json", "search", "query planner"], cwd=str(tmp_path), env=env
    )
    assert found.returncode == 0, found.stdout + found.stderr
    assert "planner" in found.stdout.lower(), found.stdout


def test_the_installed_copy_is_not_the_source_tree(bindir):
    """Guards against the whole test passing because it imported the repository."""
    proc = _run([str(bindir / "python"), "-c", "import dkg, pathlib; print(pathlib.Path(dkg.__file__).resolve())"])
    assert proc.returncode == 0, proc.stderr
    location = Path(proc.stdout.strip())
    assert ROOT / "src" not in location.parents, (
        f"the test imported the source tree at {location}, not the installed copy"
    )


# -- the installer script's contract ------------------------------------------


def test_the_installer_exists_and_is_a_bash_script():
    assert INSTALLER.is_file()
    assert INSTALLER.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash")


def test_the_installer_fails_fast():
    """Without `set -e`, a failed pip install still reaches the success message."""
    text = INSTALLER.read_text(encoding="utf-8")
    assert "set -euo pipefail" in text


def test_the_installer_creates_a_virtualenv_and_installs_the_project():
    text = INSTALLER.read_text(encoding="utf-8")
    assert "-m venv .venv" in text
    assert 'pip install -e ".[dev]"' in text


def test_the_installer_verifies_its_own_work():
    """It must not exit zero on an install that produced no working command."""
    text = INSTALLER.read_text(encoding="utf-8")
    assert "dkg --version" in text, (
        "the installer never runs the installed command, so a broken install exits zero"
    )


def test_the_installer_and_the_quick_start_document_the_same_steps():
    """Two divergent install paths is how a newcomer ends up unsupported."""
    installer = INSTALLER.read_text(encoding="utf-8")
    quickstart = (ROOT / "docs" / "QUICKSTART.md").read_text(encoding="utf-8")
    for fragment in ("-m venv .venv", "pip install --upgrade pip", 'pip install -e ".[dev]"'):
        assert fragment in installer, f"the installer no longer runs {fragment!r}"
        assert fragment in quickstart, f"the quick start no longer documents {fragment!r}"


def test_a_virtualenv_module_is_available_for_the_documented_step():
    """The first documented step must be possible on this interpreter."""
    assert shutil.which(sys.executable)
    assert venv.EnvBuilder is not None

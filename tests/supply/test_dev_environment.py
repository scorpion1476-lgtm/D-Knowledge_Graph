"""The development environment must be reproducible and its versions constrained.

Acceptance test for two matrix rows:

* **K-01**, reproducible local development setup,
* **K-02**, locked or constrained dependency strategy.

Both were accepted on a manual review, and the thing a manual review of a
dependency strategy cannot do is check the strategy against the environment it
claims to describe. A lockfile is only a lockfile if it pins what is actually
installed; a "reproducible setup" is only reproducible if the installer, the
quick start and the contributor documentation agree on the same commands.

K-01 is checked as agreement plus completeness: one installer, referenced by the
documents that tell a newcomer how to start, with an interpreter floor declared
and every script it points at present on disk.

K-02 is checked as a two-tier strategy, because that is what this project
actually operates and both tiers can go wrong independently:

* the declared tier: every optional extra constrains its dependencies with an
  explicit floor. An unconstrained extra resolves differently on every machine
  and is the reason "works here" happens.
* the locked tier: `requirements-lock.txt` pins exact versions, records the
  interpreter that resolved them, is sorted so regeneration produces a reviewable
  diff, and matches the distributions installed in this interpreter. A lockfile
  that has drifted from the environment is worse than none, because it is
  consulted and believed.

The row's stored limitation said "no lockfile committed yet". One is committed
now, and this is the test that keeps it honest.
"""

from __future__ import annotations

import re
import sys
from importlib import metadata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"
LOCKFILE = ROOT / "requirements-lock.txt"
DEV_REQUIREMENTS = ROOT / "requirements-dev.txt"
INSTALLER = ROOT / "scripts" / "install.sh"

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


# `pip freeze` omits these by design, so their absence from a lockfile is correct.
NOT_IN_A_FREEZE = {"d-knowledge-graph", "pip", "setuptools", "wheel"}


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", (name or "").strip()).lower()


@pytest.fixture(scope="module")
def pyproject() -> str:
    return PYPROJECT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def lock_lines() -> list[str]:
    return [
        line.strip()
        for line in LOCKFILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


@pytest.fixture(scope="module")
def locked(lock_lines) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in lock_lines:
        name, _, version = line.partition("==")
        out[_normalise(name)] = version
    return out


# -- K-01: one reproducible setup ----------------------------------------------


def test_the_interpreter_floor_is_declared(pyproject):
    m = re.search(r'^requires-python\s*=\s*"([^"]+)"', pyproject, re.M)
    assert m, "pyproject declares no requires-python, so any interpreter looks supported"
    assert m.group(1).startswith(">="), m.group(1)


def test_a_single_installer_exists_and_is_the_documented_one():
    assert INSTALLER.is_file(), "scripts/install.sh does not exist"
    referencing = [
        path
        for path in (
            ROOT / "docs" / "QUICKSTART.md",
            ROOT / "docs" / "DEVELOPER_GUIDE.md",
            ROOT / "CONTRIBUTING.md",
        )
        if path.is_file() and re.search(r"install\.sh|venv \.venv|-m venv", path.read_text(encoding="utf-8"))
    ]
    assert len(referencing) >= 2, (
        f"only {[p.name for p in referencing]} describe how to set up; a newcomer "
        "should not have to guess which document is current"
    )


def test_the_documented_setup_commands_agree_with_the_installer():
    installer = INSTALLER.read_text(encoding="utf-8")
    quickstart = (ROOT / "docs" / "QUICKSTART.md").read_text(encoding="utf-8")
    for fragment in ("-m venv .venv", 'pip install -e ".[dev]"'):
        assert fragment in installer and fragment in quickstart, (
            f"{fragment!r} is not in both the installer and the quick start"
        )


def test_the_dev_extra_exists_and_supplies_the_gate_tools(pyproject):
    """A setup that cannot run the gates is not a development setup."""
    block = re.search(r"^dev\s*=\s*\[(.*?)\]", pyproject, re.M | re.S)
    assert block, "pyproject declares no dev extra"
    body = block.group(1).lower()
    for tool in ("pytest", "ruff", "mypy"):
        assert tool in body, f"the dev extra does not install {tool}"


def test_every_script_the_developer_guide_names_exists():
    text = (ROOT / "docs" / "DEVELOPER_GUIDE.md").read_text(encoding="utf-8")
    named = sorted(set(re.findall(r"(scripts/[A-Za-z0-9_./-]+\.(?:py|sh))", text)))
    missing = [s for s in named if not (ROOT / s).is_file()]
    assert not missing, f"the developer guide names scripts that do not exist: {missing}"


def test_the_test_runner_the_setup_promises_is_present():
    assert (ROOT / "scripts" / "run_tests.sh").is_file()


# -- K-02, declared tier: every extra is constrained ---------------------------


def _extras(pyproject: str) -> dict[str, list[str]]:
    section = re.search(
        r"^\[project\.optional-dependencies\]\s*\n(.*?)(?=^\[)", pyproject, re.M | re.S
    )
    assert section, "pyproject declares no optional dependencies"
    body = re.sub(r"#[^\n]*", "", section.group(1))
    out: dict[str, list[str]] = {}
    for m in re.finditer(r"^([A-Za-z0-9_-]+)\s*=\s*\[(.*?)\]", body, re.M | re.S):
        out[m.group(1)] = re.findall(r'"([^"]+)"', m.group(2))
    return out


def test_every_optional_extra_constrains_every_dependency(pyproject):
    """An unpinned extra resolves differently on every machine."""
    unconstrained: list[str] = []
    for extra, requirements in _extras(pyproject).items():
        for requirement in requirements:
            if not re.search(r"[><=~!]=|>|<", requirement):
                unconstrained.append(f"{extra}: {requirement}")
    assert not unconstrained, f"extras with no version constraint: {unconstrained}"


def test_the_extras_that_matter_are_declared(pyproject):
    extras = _extras(pyproject)
    for expected in ("dev", "code", "embeddings", "reranker"):
        assert expected in extras, f"the {expected} extra is gone"


def test_the_development_requirements_file_constrains_its_tools():
    lines = [
        line.strip()
        for line in DEV_REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert lines, "requirements-dev.txt is empty"
    unconstrained = [line for line in lines if not re.search(r"[><=~!]=|>|<", line)]
    assert not unconstrained, f"unconstrained development requirements: {unconstrained}"


# -- K-02, locked tier: the lockfile is real -----------------------------------


def test_a_lockfile_is_committed():
    assert LOCKFILE.is_file(), "requirements-lock.txt is not committed"


def test_every_locked_line_is_an_exact_pin(lock_lines):
    loose = [line for line in lock_lines if "==" not in line]
    assert not loose, f"lockfile lines that are not exact pins: {loose}"


def test_the_lockfile_records_the_interpreter_that_resolved_it():
    header = LOCKFILE.read_text(encoding="utf-8").split("\n\n", 1)[0]
    assert re.search(r"#\s*python:\s*3\.\d+", header), "the lockfile does not record a Python version"
    assert re.search(r"#\s*generated:\s*20\d\d-", header), "the lockfile is not dated"


def test_the_lockfile_is_sorted_so_a_regeneration_diffs_cleanly(lock_lines):
    """Sorted by the generator's own key: the raw name, case-insensitively.

    Not the PEP 503 normalised name. `py-serializable` and `py_rust_stemmers`
    order differently under the two, and asserting the wrong key would fail on a
    lockfile that is in fact perfectly sorted.
    """
    names = [line.split("==", 1)[0].lower() for line in lock_lines]
    assert names == sorted(names), "the lockfile is not sorted; regenerating it churns the diff"
    assert names == sorted(names, key=lambda n: n), names[:5]


def test_the_lockfile_has_no_duplicate_pins(lock_lines):
    names = [_normalise(line.split("==", 1)[0]) for line in lock_lines]
    duplicates = {n for n in names if names.count(n) > 1}
    assert not duplicates, f"a package is pinned twice: {duplicates}"


def test_the_lockfile_pins_what_is_actually_installed(locked):
    """The assertion that makes the lockfile evidence rather than a wish."""
    installed = {
        _normalise(d.metadata["Name"]): d.version
        for d in metadata.distributions()
        if d.metadata.get("Name")
    }
    for name in NOT_IN_A_FREEZE:
        installed.pop(name, None)
    assert len(installed) > 20, "this does not look like the project environment"
    missing = sorted(set(installed) - set(locked))
    assert not missing, f"installed distributions absent from the lockfile: {missing[:20]}"
    drift = [
        f"{name}: locked {locked[name]} vs installed {version}"
        for name, version in installed.items()
        if name in locked and locked[name] != version
    ]
    assert not drift, f"the lockfile has drifted from the environment: {drift[:10]}"


def test_the_lockfile_pins_no_package_that_is_not_installed(locked):
    """A pin for something absent means the lockfile came from elsewhere."""
    installed = {
        _normalise(d.metadata["Name"]) for d in metadata.distributions() if d.metadata.get("Name")
    }
    extra = sorted(set(locked) - installed - NOT_IN_A_FREEZE)
    assert not extra, f"the lockfile pins packages this environment does not have: {extra[:20]}"


def test_the_lockfile_generator_exists_so_it_can_be_regenerated():
    assert (ROOT / "scripts" / "lockfile.py").is_file(), (
        "a lockfile with no generator goes stale and cannot be reproduced"
    )

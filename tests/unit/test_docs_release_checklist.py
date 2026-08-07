"""The release checklist must be followable, in order, with real commands.

Acceptance test for matrix row K-10, "Release checklist". A checklist is the one
document where a manual review is least useful: it reads fine right up to the
step that names a script somebody renamed, and the person who finds out is
mid-release.

So every step is checked mechanically:

* the checklist is numbered and the numbering is contiguous, because a
  checklist people tick off cannot skip from 3 to 5,
* every repository path any step names exists,
* every `python scripts/...` and `bash scripts/...` command names a script that
  is really there, and every `dkg` command is a subcommand this build
  registers,
* the order is sound where order matters: the suite runs before the wheel is
  built, and the checksums are computed after the build rather than over the
  previous one. Getting that pair backwards is what produced a committed
  SHA256SUMS that failed against its own repository once already.
* the signing step stays honest. This build has never signed a release, and the
  checklist has to say so at the point where somebody would otherwise add the
  label.

Nothing here runs a release step. The commands are validated as real, not
executed: a test that tagged a commit or built a wheel as a side effect would be
a worse problem than the one it checks for.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

from dkg.cli.entry import _mk_parser

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "OPERATIONS_RUNBOOK.md"

# Things a release checklist has to cover at all.
REQUIRED_STAGES = {
    "version": r"__version__|CHANGELOG",
    "tests": r"run_tests\.sh|pytest",
    "supply chain": r"secret_scan\.py|sbom\.py|license_inventory\.py",
    "build": r"python -m build|build the wheel",
    "checksums": r"checksum\.py",
    "publish": r"publish|tag",
}


@pytest.fixture(scope="module")
def doc() -> str:
    assert DOC.is_file(), "docs/OPERATIONS_RUNBOOK.md does not exist"
    return DOC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def checklist(doc: str) -> str:
    body = doc.split("## Release checklist", 1)
    assert len(body) == 2, "the runbook has no release checklist"
    return body[1].split("\n## ", 1)[0]


@pytest.fixture(scope="module")
def steps(checklist: str) -> list[tuple[int, str]]:
    """Each numbered step with everything indented beneath it."""
    out: list[tuple[int, str]] = []
    current: tuple[int, list[str]] | None = None
    for line in checklist.splitlines():
        m = re.match(r"^(\d+)\.\s+(.*)$", line)
        if m:
            if current:
                out.append((current[0], "\n".join(current[1])))
            current = (int(m.group(1)), [m.group(2)])
        elif current is not None and line.strip():
            current[1].append(line)
    if current:
        out.append((current[0], "\n".join(current[1])))
    return out


# -- shape ---------------------------------------------------------------------


def test_the_checklist_is_numbered_and_contiguous(steps):
    assert steps, "the release checklist has no numbered steps"
    numbers = [n for n, _ in steps]
    assert numbers == list(range(1, len(numbers) + 1)), f"steps are not 1..n: {numbers}"


@pytest.mark.parametrize(("stage", "pattern"), sorted(REQUIRED_STAGES.items()))
def test_every_required_stage_appears(checklist, stage, pattern):
    assert re.search(pattern, checklist, re.I), f"the checklist never covers {stage}"


# -- the commands are real ------------------------------------------------------


def test_every_script_the_checklist_names_exists(checklist):
    named = sorted(set(re.findall(r"(?:python|bash)\s+(scripts/[A-Za-z0-9_./-]+)", checklist)))
    assert named, "the checklist names no scripts at all"
    missing = [s for s in named if not (ROOT / s).is_file()]
    assert not missing, f"the checklist names scripts that do not exist: {missing}"


def test_every_repository_path_the_checklist_cites_exists(checklist):
    missing: list[str] = []
    for candidate in sorted(set(re.findall(r"`([A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+)`", checklist))):
        if not (ROOT / candidate.split("/", 1)[0]).exists():
            continue
        if not (ROOT / candidate).exists():
            missing.append(candidate)
    assert not missing, f"the checklist cites paths that do not exist: {missing}"


def test_every_dkg_command_in_the_runbook_is_a_real_subcommand(doc):
    action = next(
        a for a in _mk_parser()._actions if isinstance(a, argparse._SubParsersAction)
    )
    registered = set(action.choices)
    used: set[str] = set()
    for block in re.findall(r"```bash\n(.*?)```", doc, re.S):
        for line in block.splitlines():
            m = re.match(r"^(?:\./)?(?:[\w./-]*/)?dkg\s+([a-z-]+)", line.strip())
            if m:
                used.add(m.group(1))
    unknown = sorted(used - registered)
    assert not unknown, f"the runbook names subcommands this build does not register: {unknown}"


def test_the_version_file_the_checklist_names_really_holds_the_version(checklist):
    assert "__version__" in checklist
    source = (ROOT / "src" / "dkg" / "__init__.py").read_text(encoding="utf-8")
    assert re.search(r'^__version__\s*=\s*["\']', source, re.M), (
        "the checklist points at src/dkg/__init__.py for the version, which does not set one"
    )


# -- the order that matters -----------------------------------------------------


def _index_of(steps: list[tuple[int, str]], pattern: str) -> int:
    for position, (_, body) in enumerate(steps):
        if re.search(pattern, body, re.I):
            return position
    raise AssertionError(f"no step matches {pattern!r}")


def test_the_suite_runs_before_the_wheel_is_built(steps):
    assert _index_of(steps, r"run_tests\.sh|pytest") < _index_of(steps, r"python -m build")


def test_the_checksums_are_computed_after_the_build(steps):
    """Otherwise the recorded hash is the hash of the previous artefact."""
    assert _index_of(steps, r"python -m build") < _index_of(steps, r"checksum\.py")


def test_publication_is_the_last_gate(steps):
    publish = _index_of(steps, r"publish")
    assert publish == len(steps) - 1, "publication is not the final step"
    assert re.search(r"only after all checks pass", steps[publish][1], re.I)


# -- honesty --------------------------------------------------------------------


def test_the_signing_step_refuses_the_label_until_signing_has_run(steps):
    _, body = next((n, b) for n, b in steps if re.search(r"\bsign\b", b, re.I))
    flat = re.sub(r"\s+", " ", body).lower()
    assert "out of scope for this build" in flat
    assert "do not add a signed-release label until a real signing step has run" in flat

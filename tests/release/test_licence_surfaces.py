"""One licence, declared the same way on every surface that declares one.

The failure mode this exists to stop is narrow and has already happened twice:
the prose gets corrected and the machine-readable trail keeps telling the old
story, because nobody reads an SBOM by eye. A licence scanner, a package index,
and a container registry all read the second kind and none of them read the
README.

The sweep below enumerates the surfaces that make a claim about *this project's*
licence and checks each one exactly. Third-party Apache-2.0 is legitimate and is
not touched: dependency licences appear all over the inventory, the SBOM package
list, and the model provenance, and they must.
"""

from __future__ import annotations

import configparser
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

LICENCE_ID = "LicenseRef-DKG-Source-Available-NonCommercial"

# Every way this project has previously mis-declared its own licence.
_FORBIDDEN_SELF_CLAIMS = (
    "Apache-2.0",
    "Apache 2.0",
    "Apache License",
    "LicenseRef-PolyForm-Internal-Use-1.0.0",
    "polyformproject.org/licenses/internal-use",
)


def _assert_no_self_claim(where: str, blob: str) -> None:
    hits = [c for c in _FORBIDDEN_SELF_CLAIMS if c in blob]
    assert not hits, f"{where} declares {hits} for this project"


# -- machine-readable surfaces ----------------------------------------------


def test_sbom_root_component_declares_the_repository_licence() -> None:
    sbom = json.loads((ROOT / "test-evidence" / "sbom.cdx.json").read_text(encoding="utf-8"))
    root = sbom["metadata"]["component"]
    blob = json.dumps(root["licenses"])
    assert LICENCE_ID in blob
    _assert_no_self_claim("SBOM root component", blob)


def test_licence_inventory_project_and_ariadne_agree_with_the_repository_licence() -> None:
    inv = json.loads((ROOT / "test-evidence" / "license_inventory.json").read_text(encoding="utf-8"))
    assert inv["project"]["license"] == LICENCE_ID
    ariadne = inv["ariadne_module"]
    assert ariadne["license"] == LICENCE_ID, (
        "Ariadne carries no licence of its own; a separate LicenseRef here is a "
        "machine-readable claim that outlives any prose correction"
    )
    _assert_no_self_claim("licence inventory", json.dumps({"p": inv["project"], "a": ariadne}))


def test_licence_inventory_generator_has_one_source_of_truth() -> None:
    text = (ROOT / "scripts" / "license_inventory.py").read_text(encoding="utf-8")
    assert f'PROJECT_LICENSE = "{LICENCE_ID}"' in text
    # The generator must not hardcode any other identifier for this project.
    for claim in _FORBIDDEN_SELF_CLAIMS:
        assert f'"{claim}"' not in text, f"generator hardcodes {claim}"


def test_pyproject_declares_the_licence_and_no_osi_classifier() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    head = text.split("[project.optional-dependencies]")[0]
    assert f'license = {{ text = "{LICENCE_ID}" }}' in head
    _assert_no_self_claim("pyproject [project]", head)
    assert "OSI Approved" not in text


def test_container_label_declares_the_licence() -> None:
    text = (ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
    labels = [ln for ln in text.splitlines() if "image.licenses" in ln]
    assert labels, "the container image declares no licence at all"
    for line in labels:
        assert LICENCE_ID in line
        _assert_no_self_claim("Dockerfile label", line)


def test_installed_package_metadata_declares_the_licence() -> None:
    pkg_info = ROOT / "src" / "d_knowledge_graph.egg-info" / "PKG-INFO"
    if not pkg_info.exists():
        pytest.skip("package metadata not built in this environment")
    declared = [
        ln for ln in pkg_info.read_text(encoding="utf-8").splitlines()
        if ln.startswith("License:") or ln.startswith("Classifier: License")
    ]
    assert declared
    assert any(LICENCE_ID in ln for ln in declared)
    for line in declared:
        _assert_no_self_claim("PKG-INFO", line)


def test_no_build_or_supply_chain_evidence_declares_apache_for_this_project() -> None:
    for name in ("reproducible_build.json", "clean_install_check.json", "EVIDENCE_BUNDLE.json"):
        path = ROOT / "test-evidence" / name
        if not path.exists():
            continue
        blob = path.read_text(encoding="utf-8")
        assert "Apache" not in blob, f"{name} mentions Apache; this project is not Apache licensed"


# -- workflows --------------------------------------------------------------


def test_release_workflow_carries_no_ariadne_exclusion() -> None:
    text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "Ariadne" in text, "the workflow should state that Ariadne ships"
    assert not re.search(r"exclude.{0,40}ariadne", text, re.I | re.S), (
        "the wheel-excludes-Ariadne assumption is stale and must not return"
    )
    _assert_no_self_claim("release workflow", text)


def test_ci_workflow_makes_no_licence_claim_of_its_own() -> None:
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    _assert_no_self_claim("ci workflow", text)


def test_packaging_config_does_not_exclude_ariadne() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'exclude = ["dkg.ariadne*"]' not in text
    # No packages.find exclude anywhere may name the module.
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "exclude" not in stripped:
            continue
        assert "ariadne" not in stripped.lower(), f"packaging excludes Ariadne: {stripped}"


# -- prose ------------------------------------------------------------------

# The sweep runs over every tracked text file, not over a hand-kept list. A list
# is the wrong shape for this check: an adversarial review found two live
# Apache-2.0 self-claims that a seven-document list did not cover, one of them a
# module docstring that shipped inside the wheel. The detector was sound; the
# file list was the hole. Anything the repository tracks can make a claim, so
# everything the repository tracks is scanned.
# Every text suffix the repository tracks. The first version of this list left
# out .log and .csv, and a second review found a live Apache-2.0 self-claim
# sitting in a tracked .log file, invisible to the sweep. A filter that happens
# to exclude the file the bug is in is not a filter, it is a blind spot, so the
# only things omitted now are genuinely binary.
_BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".xlsx",
                    ".docx", ".pptx", ".zip", ".woff", ".woff2", ".ttf", ".onnx",
                    ".bin", ".so", ".dylib", ".wav", ".mp3", ".mp4", ".webm"}
# These two files quote self-claims as test data. Excluding them by name keeps
# the check honest: nothing else is excluded, and a new exclusion is a visible
# edit to this list.
# Files whose SUBJECT is the forbidden claim. A test that exists to prove no
# document calls this project open source has to name the phrase it forbids, in
# a function name, a regex, or an assertion. Scanning those names would flag the
# guard for doing its job, exactly as scanning this module would. The exclusion
# is deliberately a short, explicit list of guard modules and never a directory
# or a pattern, so ordinary prose can never fall into it by accident.
_SELF_REFERENTIAL = {
    "tests/release/test_licence_surfaces.py",
    "tests/unit/test_licence_and_detectors.py",
    "tests/unit/test_docs_faq.py",
    "tests/unit/test_docs_contributing.py",
    "tests/unit/test_readme_translations.py",
    # Forbids the README from calling itself open source, FOSS or Apache
    # licensed, so it has to spell those phrases out to search for them.
    "tests/unit/test_docs_readme_quality.py",
    # Asserts the project's own distribution declares the repository licence and
    # not a permissive one, so it names the permissive identifiers it rejects.
    "tests/supply/test_licence_inventory.py",
}


def _tracked_text_files() -> list[str]:
    import subprocess

    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, timeout=120
    )
    assert out.returncode == 0, out.stderr
    files = []
    for rel in out.stdout.splitlines():
        rel = rel.strip()
        if not rel or rel in _SELF_REFERENTIAL:
            continue
        if Path(rel).suffix.lower() not in _BINARY_SUFFIXES:
            files.append(rel)
    assert len(files) > 1000, f"only {len(files)} files enumerated; the sweep is not covering the tree"
    return files

# Two rules, because the two families of term behave differently in this repo.
#
# "open source", "FOSS" and "free software" are never used here to describe a
# dependency: dependencies are named by licence identifier. So any occurrence is
# about this project and has to be a negation or a prohibition.
#
# "Apache-2.0" is the opposite. It names a dependency on nearly every line it
# appears on, legitimately and necessarily, so requiring each of those to carry
# a disclaimer would be noise. It is a self-claim only when the sentence is
# about this project, which means the sentence has to say so.
_OPEN_SOURCE_TERM = re.compile(r"open.source|FOSS|free software", re.I)
_APACHE_TERM = re.compile(r"apache", re.I)
_SELF_SUBJECT = re.compile(
    r"\bthis (?:project|software|repository|package|product|licence|license|version)\b"
    # Deliberately not "the product" or "the platform": both are used here to
    # mean the shipped dependency closure, which is a statement about somebody
    # else's licence, not about this one.
    r"|\bthe (?:project|repository|wheel|whole repository|entire repository)\b"
    r"|\bd[-_ ]?knowledge[-_ ]?graph\b"
    # Both detectors are this project's own code, so a licence claim next to
    # either name is a claim about this project. Omitting Mnemosyne is how a
    # tracked evidence log kept declaring it Apache-2.0.
    r"|\bariadne\b|\bmnemosyne\b"
    # Phrasings a real declaration uses that the first version missed.
    r"|\bdkg\b|\bthe software\b|\bspdx-license-identifier\b"
    r"|^\s*licen[cs]ed under\b|\breleased under\b|\bdistributed under\b",
    re.I | re.M,
)

# A mention is not a self-claim when it is negated, prohibited, or placed in the
# past. Prose wraps, so this is judged over a small window of surrounding lines.
_EXCUSED_IN_WINDOW = re.compile(
    r"not an open.source|not open source|not a free.software|never describe|prohibit"
    r"|unaffected|before 2026-08-05|earlier|previously|originally|superseded"
    r"|remains in force|is not revoked|records the Apache"
    # The negation also has to be recognised in the languages the README ships
    # in. Otherwise a faithful translation is punished for saying the same thing
    # the English says: the French rendering literally negates the English term
    # ("ceci n'est pas une licence open source"), and the sweep flagged it as a
    # self-claim purely because the phrase it negates is spelled in English. The
    # other three translations use a native term and never tripped it, which is
    # exactly the kind of inconsistency that hides a real problem.
    r"|n.est pas une licence open source|pas une licence open source"
    r"|no es una licencia de c\w+digo abierto"
    r"|keine quelloffene lizenz",
    re.I,
)

# Naming the thing as somebody else's software is also a disambiguation, but it
# only speaks for the line it is on. Allowing it to excuse a three-line window
# blinded 1462 lines, 80 percent of NOTICE among them, so a planted
# "D-Knowledge Graph is licensed under the Apache License" three lines from the
# word "dependencies" passed the sweep. Same line only.
_THIRD_PARTY_ON_THIS_LINE = re.compile(
    r"third.part|dependenc|upstream package|external binary", re.I
)

def _live_self_claims(text: str, label: str = "") -> list[str]:
    """Lines that assert this project is open source, FOSS, or Apache licensed."""
    lines = text.splitlines()
    offenders: list[str] = []
    for i, line in enumerate(lines):
        window = " ".join(lines[max(0, i - 3) : i + 4])
        if _EXCUSED_IN_WINDOW.search(window):
            continue
        if _THIRD_PARTY_ON_THIS_LINE.search(line):
            continue
        if _OPEN_SOURCE_TERM.search(line):
            offenders.append(f"{label}:{i + 1}: {line.strip()[:110]}")
            continue
        if _APACHE_TERM.search(line) and _SELF_SUBJECT.search(" ".join(lines[max(0, i - 1) : i + 2])):
            offenders.append(f"{label}:{i + 1}: {line.strip()[:110]}")
    return offenders


def test_a_self_claim_next_to_an_excusing_word_is_still_caught() -> None:
    """The blind spot a third review demonstrated.

    Letting "dependency" or "third-party" excuse a three-line window blinded
    1462 lines, 80 percent of NOTICE among them, so a planted "D-Knowledge Graph
    is licensed under the Apache License" three lines away from the word
    "dependencies" passed the sweep. Those words now speak only for the line
    they are on.
    """
    planted = (
        "Optional extras use unmodified permissive third-party packages.\n"
        "\n"
        "\n"
        "D-Knowledge Graph is licensed under the Apache License, Version 2.0.\n"
    )
    assert _live_self_claims(planted, "NOTICE")


def test_the_self_claim_detector_is_not_vacuous() -> None:
    """A check that never fires is worse than no check, because it reads green."""
    assert _live_self_claims("D-Knowledge Graph is released under Apache-2.0.")
    assert _live_self_claims("This project is licensed under the Apache License 2.0.")
    assert _live_self_claims("The Ariadne module is Apache-2.0.")
    assert _live_self_claims("This is an open-source project.")
    assert _live_self_claims("The platform is free software.")
    assert _live_self_claims("A FOSS-first knowledge graph.")
    assert _live_self_claims("SPDX-License-Identifier: Apache-2.0")
    assert _live_self_claims("DKG is Apache-2.0 licensed.")
    assert _live_self_claims("The software is Apache-2.0.")
    assert _live_self_claims("Licensed under Apache-2.0")
    assert _live_self_claims("Mnemosyne is released under Apache-2.0.")
    # ... and does not fire on the shapes that are legitimate.
    assert not _live_self_claims("fastembed (Apache-2.0) is an optional dependency.")
    assert not _live_self_claims("| typescript-language-server | Apache-2.0 |")
    assert not _live_self_claims("third-party only: fastembed Apache-2.0; weights Apache-2.0")
    assert not _live_self_claims("This is NOT an open-source licence.")
    # The shipped translations negate the same claim in their own languages, and
    # excusing those must not have excused the positive form along with them.
    assert not _live_self_claims("Ceci n'est pas une licence open source, ni libre.")
    assert _live_self_claims("Ceci est une licence open source.")
    assert not _live_self_claims(
        "Versions distributed before 2026-08-05 were released under Apache-2.0."
    )


def test_the_self_referential_exclusion_only_ever_covers_guard_modules() -> None:
    """The one way this sweep could be defeated is by growing its own blind spot.

    Excluding a guard module is legitimate: it has to name the phrase it forbids.
    Excluding a document is not, because a document is exactly where a false
    claim would reach a reader. So the list is pinned to test modules that exist
    to police licence wording, and every entry must still be tracked, or a
    rename would silently reopen the file to no scrutiny at all.
    """
    for rel in sorted(_SELF_REFERENTIAL):
        assert rel.startswith("tests/"), f"only a guard module may be excluded, not {rel}"
        assert rel.endswith(".py"), f"only a guard module may be excluded, not {rel}"
        assert ("licence" in rel) or ("docs" in rel) or ("translations" in rel), (
            f"{rel} does not look like a licence-wording guard; justify it or drop it"
        )
        assert (ROOT / rel).is_file(), f"excluded path {rel} no longer exists; the exclusion is stale"


def test_no_tracked_file_makes_a_live_open_source_or_apache_self_claim() -> None:
    offenders: list[str] = []
    for rel in _tracked_text_files():
        path = ROOT / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        offenders.extend(_live_self_claims(text, rel))
    assert not offenders, offenders


def test_no_tracked_asset_calls_the_project_foss() -> None:
    """Brand assets and test corpora describe the project too, and are the last
    place anyone looks."""
    for rel in ("docs/brand/dkg-wordmark.svg", "tests/media/corpus/ocr_ground_truth.txt"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert not re.search(r"FOSS|open.source", text, re.I), f"{rel} still calls the project FOSS"


def test_readme_badge_advertises_the_real_licence() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    badges = [ln for ln in text.splitlines() if "img.shields.io" in ln and "licen" in ln.lower()]
    assert badges, "the README has no licence badge"
    for badge in badges:
        assert "source--available" in badge
        _assert_no_self_claim("README licence badge", badge)


def test_third_party_apache_is_still_allowed_and_still_present() -> None:
    """The sweep must not have been implemented by deleting true statements."""
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert "Apache" in notices, "third-party Apache-2.0 notices are required and must stay"
    inv = json.loads((ROOT / "test-evidence" / "license_inventory.json").read_text(encoding="utf-8"))
    assert any("Apache" in str(p.get("license", "")) for p in inv["packages"])


def test_setup_cfg_absent_or_consistent() -> None:
    cfg_path = ROOT / "setup.cfg"
    if not cfg_path.exists():
        pytest.skip("no setup.cfg")
    cfg = configparser.ConfigParser()
    cfg.read(cfg_path)
    if cfg.has_option("metadata", "license"):
        assert cfg.get("metadata", "license") == LICENCE_ID


def test_licence_discloses_every_divergence_from_the_canonical_polyform_text() -> None:
    """The document says it states its modifications. It has to state all of them.

    Scope, stated plainly: this reads LICENSE and checks that its own disclosure
    names both divergences and that the operative text really carries them. It
    does NOT fetch or diff the canonical PolyForm text, so it cannot catch a
    divergence the disclosure fails to mention. The comparison against the
    canonical wording was made by hand on 2026-08-05, against
    <https://polyformproject.org/licenses/noncommercial/1.0.0>, and found
    exactly the two divergences named below; vendoring that text to make the
    check mechanical would mean shipping a third party's licence document for
    a licence this project does not use.
    """
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    disclosure = text.split("ADDITIONAL TERM")[0]
    assert "MODIFIED PolyForm text" in disclosure
    assert "Distribution License" in disclosure
    assert "New Works License" in disclosure.replace("\n", " ")
    assert "Notices" in disclosure
    # The added clause has to be disclosed, not just present further down.
    assert "carve-out" in disclosure
    assert "other than\n   distributing the software" in disclosure or (
        "other than distributing the software" in disclosure.replace("\n   ", " ")
    )
    # And it has to actually be in the operative section.
    operative = text.split("## Copyright License")[1].split("##")[0]
    assert "other than distributing the software" in " ".join(operative.split())


def test_the_three_removed_sections_are_really_absent_from_the_operative_text() -> None:
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    operative = text.split("PolyForm Noncommercial License 1.0.0, as modified above")[1]
    for heading in ("## Distribution License", "## Changes and New Works License", "## Notices"):
        assert heading not in operative, f"{heading} is present but declared removed"
    # The sections that are kept must still be there.
    for heading in ("## Acceptance", "## Copyright License", "## Patent License", "## Definitions"):
        assert heading in operative, f"{heading} is missing"


def test_the_project_is_not_inventoried_as_one_of_its_own_dependencies() -> None:
    """An editable install made before the relicence kept reporting Apache-2.0
    from stale dist-info, and the generator wrote that string into the committed
    inventory as though it were this project's licence. The generator now takes
    the project's own licence from the repository, never from pip metadata."""
    inv = json.loads((ROOT / "test-evidence" / "license_inventory.json").read_text(encoding="utf-8"))
    normalise = lambda n: re.sub(r"[-_.]+", "-", str(n or "").strip()).lower()  # noqa: E731
    own = [p for p in inv["packages"] if normalise(p["name"]) == "d-knowledge-graph"]
    assert own, (
        "the inventory does not list this project at all, so this check would pass "
        "without examining anything; regenerate it from an environment with the "
        "project installed"
    )
    for entry in own:
        assert entry["license"] == LICENCE_ID, (
            f"the inventory lists this project as {entry['license']!r}; "
            "installed metadata is not a source of truth for our own licence"
        )


def test_the_generator_never_reads_this_projects_licence_from_pip() -> None:
    text = (ROOT / "scripts" / "license_inventory.py").read_text(encoding="utf-8")
    assert 'PROJECT_NAME = "d-knowledge-graph"' in text
    assert "_normalise(PROJECT_NAME)" in text

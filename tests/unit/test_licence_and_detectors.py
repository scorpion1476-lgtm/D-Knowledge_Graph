"""The licence is stated consistently, and both detectors run by default.

Two things this project has previously got wrong and must not get wrong again:
describing itself as open source when its licence forbids commercial use and
modification, and shipping a second detector that never actually ran.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LICENSE = ROOT / "LICENSE"
NOTICE = ROOT / "NOTICE"
README = ROOT / "README.md"
PYPROJECT = ROOT / "pyproject.toml"


def _tracked_markdown() -> list[Path]:
    """Every tracked markdown document, asked of git rather than of the disk.

    The open-source check used to run over the README and one working-rules file
    that has since been made local-only. Reading the disk would have quietly gone
    on passing here while failing for anyone who cloned the repository, so the
    list comes from the index and covers every document that is actually
    published, which is a wider net than the two files it replaces.
    """
    out = subprocess.run(
        ["git", "ls-files", "*.md"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    assert out, "git reported no tracked markdown at all"
    return [ROOT / p for p in sorted(out)]


TRACKED_MARKDOWN = _tracked_markdown()

try:
    import tree_sitter  # noqa: F401

    _TS = True
except Exception:
    _TS = False

requires_ts = pytest.mark.skipif(not _TS, reason="tree-sitter not installed (the 'code' extra)")

if _TS:
    from dkg.code.graph import write_code_graph
    from dkg.code.parser import parse_source


# -- licence ----------------------------------------------------------------


def test_licence_is_the_source_available_non_commercial_one():
    text = LICENSE.read_text(encoding="utf-8")
    assert "Source-Available Non-Commercial Licence" in text
    assert "PolyForm Noncommercial License 1.0.0" in text
    assert "NOT an open-source licence" in text
    # The no-modification term is the project-specific addition and must be present.
    assert "NO MODIFICATION" in text
    assert "you may NOT modify this software" in text
    # The Apache text must be gone.
    assert "Apache License" not in text.split("THIRD-PARTY SOFTWARE")[0]


def test_licence_covers_the_whole_repository_including_ariadne():
    text = LICENSE.read_text(encoding="utf-8")
    assert "ENTIRE repository, including the Ariadne module" in text
    notice = NOTICE.read_text(encoding="utf-8")
    assert "entire repository, including the Ariadne module" in notice
    assert "no separately licensed component" in notice


def test_the_earlier_apache_grant_is_acknowledged_not_hidden():
    """Relicensing forward is legitimate; pretending it is retroactive is not."""
    for path in (LICENSE, NOTICE):
        text = path.read_text(encoding="utf-8")
        assert "Apache" in text, f"{path.name} must acknowledge the earlier grant"
        assert "2026-08-05" in text


def test_no_separate_ariadne_licence_files_remain():
    assert not (ROOT / "src" / "dkg" / "ariadne" / "LICENSE").exists()
    assert not (ROOT / "src" / "dkg" / "ariadne" / "NOTICE").exists()
    readme = (ROOT / "src" / "dkg" / "ariadne" / "README.md").read_text(encoding="utf-8")
    assert "Covered by the repository licence" in readme
    assert "PolyForm Internal Use" not in readme


def test_ariadne_is_no_longer_excluded_from_the_build():
    text = PYPROJECT.read_text(encoding="utf-8")
    assert 'exclude = ["dkg.ariadne*"]' not in text
    assert "Apache-2.0" not in text.split("[project.optional-dependencies]")[0], (
        "the project's own licence declaration must not say Apache"
    )
    assert "LicenseRef-DKG-Source-Available-NonCommercial" in text
    assert "OSI Approved" not in text, "there is no OSI classifier for this licence"


# The claim this guard is looking for, in every language the repository ships a
# README in. An English-only term list would have let a translation make the
# forbidden claim in its own words, which is the reading most of that
# translation's audience would actually take.
OPEN_SOURCE_TERM = re.compile(
    r"open.source|FOSS|free software"  # English
    r"|c[oó]digo abierto|software libre"  # Spanish
    r"|logiciel libre"  # French, which uses "open source" verbatim otherwise
    r"|quelloffen|freie software"  # German
    r"|开源|自由软件",  # Simplified Chinese
    re.I,
)

# A mention is permitted only next to a denial or a prohibition. Each language
# needs both of its own, or a correct translated negation reads as a claim. The
# prohibition verbs carry most of the weight, because the FAQ rows answer the
# question with a bare "No" and then say what is forbidden.
NEGATED = re.compile(
    # Denials.
    r"not an open.source|not open source|never describe"  # English
    r"|no es una? licencia|no es de c[oó]digo abierto|no es software libre"  # Spanish
    r"|n'est pas une? licence|n'est pas (?:un |de l')?logiciel libre"  # French
    r"|ist keine|keine quelloffene|ist kein freie"  # German
    r"|不是"  # Simplified Chinese
    # Prohibitions.
    r"|prohibit|prohibid"  # English, Spanish
    r"|interdit"  # French
    r"|untersagt"  # German
    r"|禁止",  # Simplified Chinese
    re.I,
)


@pytest.mark.parametrize("path", TRACKED_MARKDOWN, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_document_calls_the_project_open_source(path):
    """Only a negation or a prohibition may mention open source or FOSS.

    Checked over a window rather than a single line, because these documents
    hard-wrap and the negation often sits on the line before the term.

    Both patterns are multilingual. Widening this test from two files to every
    tracked document showed why: the French README's denial reads "Ceci n'est
    pas une licence open source", which an English-only negation list scored as
    a claim, while the Spanish, German, and Chinese denials were not being read
    at all because they never use the Latin phrase.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    offenders = []
    for i, line in enumerate(lines):
        if not OPEN_SOURCE_TERM.search(line):
            continue
        window = " ".join(lines[max(0, i - 2) : i + 3])
        if not NEGATED.search(window):
            offenders.append(line.strip()[:100])
    assert not offenders, f"{path.name} still claims open source: {offenders}"


# A claim of the forbidden kind, phrased naturally in each shipped language and
# stripped of any denial. Nothing here appears in the repository; it exists so
# the guard above cannot pass by matching nothing.
PLANTED_CLAIMS = [
    ("en", "D-Knowledge Graph is open source and free software."),
    ("es", "D-Knowledge Graph es de código abierto y software libre."),
    ("fr", "D-Knowledge Graph est un logiciel libre et open source."),
    ("de", "D-Knowledge Graph ist quelloffene und freie Software."),
    ("zh-CN", "D-Knowledge Graph 是开源和自由软件。"),
]


@pytest.mark.parametrize("lang,claim", PLANTED_CLAIMS, ids=[c[0] for c in PLANTED_CLAIMS])
def test_the_open_source_guard_fires_on_a_planted_claim(lang, claim, tmp_path):
    """Negative control: the guard must fail on a document that does claim it.

    A guard that scanned for a term no document contains would pass over every
    file forever and report a clean sweep. This plants the claim in each
    language the repository ships and asserts the term matches and the denial
    list does not rescue it.
    """
    assert OPEN_SOURCE_TERM.search(claim), f"{lang}: the term matcher missed a plain claim"
    assert not NEGATED.search(claim), f"{lang}: a claim with no denial was treated as denied"

    # And the check as a whole rejects the file, not just the two patterns.
    planted = tmp_path / f"README.{lang}.md"
    planted.write_text(f"# Licence\n\n{claim}\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="still claims open source"):
        test_no_document_calls_the_project_open_source(planted)


def test_readme_states_the_licence_as_a_table_over_the_whole_repository():
    text = README.read_text(encoding="utf-8")
    assert "| Component | Licence | Terms |" in text
    assert "The entire repository, Ariadne included" in text
    assert "No commercial use" in text
    # The badge must not advertise an OSI licence.
    assert "License-Apache" not in text
    assert "source--available" in text


def test_third_party_licences_are_explicitly_unaffected():
    for path in (LICENSE, NOTICE):
        assert "unaffected" in path.read_text(encoding="utf-8").lower()


# -- detectors --------------------------------------------------------------


def test_both_detectors_are_the_default_on_every_surface():
    cli = (ROOT / "src" / "dkg" / "cli" / "entry.py").read_text(encoding="utf-8")
    assert '"--detector",\n        default="both"' in cli.replace("\r", "")
    tools = (ROOT / "src" / "dkg" / "mcp" / "tools.py").read_text(encoding="utf-8")
    assert 'args.get("detector", "both")' in tools
    assert '"enum": ["both", "mnemosyne", "ariadne"]' in tools


@requires_ts
def test_the_default_path_actually_runs_both_detectors(db):
    from dkg.graph.community import communities_combined

    code = (
        "a.py",
        "def a1():\n    return a2()\ndef a2():\n    return 1\n"
        "def b1():\n    return b2()\ndef b2():\n    return 2\n",
        "python",
    )
    p, t, lang = code
    write_code_graph(db, [parse_source(p, t, language=lang)], {p: t}, source_uri="test://det")

    result = communities_combined(db)
    detectors = {entry["detector"]: entry for entry in result["passes"]}
    assert set(detectors) == {"mnemosyne", "ariadne"}
    assert detectors["mnemosyne"]["ran"] is True
    assert detectors["ariadne"]["ran"] is True, "the refinement pass must actually run"
    assert detectors["mnemosyne"]["role"] == "base"
    assert detectors["ariadne"]["role"] == "refinement"
    assert result["algorithm"] == "mnemosyne+ariadne"


@requires_ts
def test_selection_is_by_measured_modularity_not_preference(db):
    from dkg.graph.community import communities_combined

    p, t, lang = ("a.py", "def a1():\n    return a2()\ndef a2():\n    return 1\n", "python")
    write_code_graph(db, [parse_source(p, t, language=lang)], {p: t}, source_uri="test://det")
    result = communities_combined(db)

    passes = {e["detector"]: e for e in result["passes"]}
    base_q = passes["mnemosyne"]["modularity"]
    refined_q = passes["ariadne"]["modularity"]
    if refined_q > base_q:
        assert result["selected_detector"] == "ariadne"
        assert result["refinement_applied"] is True
    else:
        # A tie must deterministically keep the base pass, and say why.
        assert result["selected_detector"] == "mnemosyne"
        assert result["refinement_applied"] is False
        assert "did not beat" in result["selection_reason"]


@requires_ts
def test_the_default_path_is_deterministic(db):
    from dkg.graph.community import communities_combined

    p, t, lang = ("a.py", "def a1():\n    return a2()\ndef a2():\n    return 1\n", "python")
    write_code_graph(db, [parse_source(p, t, language=lang)], {p: t}, source_uri="test://det")
    assert communities_combined(db) == communities_combined(db)


def test_the_core_still_works_when_the_refinement_detector_is_absent(db, monkeypatch):
    """Capability detection, not an assumption that Ariadne is importable."""
    import builtins

    from dkg.graph.community import communities_combined

    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if "ariadne" in name:
            raise ImportError("simulated: ariadne not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    result = communities_combined(db)
    passes = {e["detector"]: e for e in result["passes"]}
    assert passes["ariadne"]["ran"] is False
    assert result["selected_detector"] == "mnemosyne"
    assert result["refinement_applied"] is False
    assert "not installed" in result["selection_reason"]


# -- machine-readable surfaces, added after the adversarial audit ------------


def test_no_generated_supply_chain_artifact_declares_the_project_apache():
    """The audit found the human-readable docs clean and the machine-readable
    trail still telling the old story. Scanners read the latter."""
    import json as _json

    inv = _json.loads((ROOT / "test-evidence" / "license_inventory.json").read_text(encoding="utf-8"))
    assert inv["project"]["license"] == "LicenseRef-DKG-Source-Available-NonCommercial"
    assert "Apache" not in _json.dumps(inv.get("ariadne_module", {}))

    sbom = _json.loads((ROOT / "test-evidence" / "sbom.cdx.json").read_text(encoding="utf-8"))
    root_licences = _json.dumps(sbom["metadata"]["component"]["licenses"])
    assert "Apache" not in root_licences
    assert "LicenseRef-DKG-Source-Available-NonCommercial" in root_licences


def test_the_container_label_does_not_declare_apache():
    text = (ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
    assert 'image.licenses="Apache-2.0"' not in text
    assert "LicenseRef-DKG-Source-Available-NonCommercial" in text


def test_the_generators_do_not_hardcode_apache_for_this_project():
    for rel in ("scripts/license_inventory.py", "scripts/sbom.py"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert '"Apache-2.0"' not in text, f"{rel} hardcodes an Apache self-claim"


def test_the_matrix_carries_no_project_self_apache_or_premium_claim():
    import csv as _csv

    with (ROOT / "docs" / "REQUIREMENTS_TRACEABILITY_MATRIX.csv").open(encoding="utf-8") as fh:
        for row in _csv.DictReader(fh):
            assert row["licence_impact"].strip() not in ("Apache-2.0", "Apache 2.0"), row["id"]
            assert "PolyForm Internal Use" not in row["licence_impact"], row["id"]
            blob = row["requirement"] + row["remaining_limitation"]
            assert "premium" not in blob.lower(), row["id"]
            assert "Apache-2.0 wheel" not in blob, row["id"]


def test_licence_does_not_claim_to_reproduce_polyform_unmodified():
    """Three canonical sections are deliberately removed, so claiming the text
    is reproduced in full would misdescribe the document."""
    text = LICENSE.read_text(encoding="utf-8")
    assert "reproduced in full below" not in text
    assert "MODIFIED PolyForm text" in text
    # Whitespace-insensitive: the disclosure is prose and gets rewrapped.
    flat = " ".join(text.split())
    assert "Distribution License" in flat
    assert "Changes and New Works License" in flat

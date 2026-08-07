"""The FAQ has to stay honest, and honest here is checkable.

A FAQ is marketing copy unless something holds it to the repository. So each
check below compares the document with something real: the measured numbers come
from `test-evidence/`, the verification commands are checked against the actual
argument parser, and the licence wording is checked against the terms the
project is under.

The comparison sections are checked for presence because the requirement names
them, but the substance checks are the ones that bite: a number that drifts from
its artifact, a verification step that names a command this build does not have,
or a sentence that quietly calls this open source.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pytest

from dkg.cli.entry import _mk_parser

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "FAQ.md"
EVIDENCE = ROOT / "test-evidence"


@pytest.fixture(scope="module")
def doc() -> str:
    assert DOC.is_file(), f"{DOC} does not exist"
    return DOC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def flat(doc: str) -> str:
    """The document with every run of whitespace collapsed to one space.

    Checks for a phrase must not depend on where the paragraph happens to wrap.
    """
    return re.sub(r"\s+", " ", doc)


@pytest.fixture(scope="module")
def subcommands() -> set[str]:
    parser = _mk_parser()
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return set(action.choices)


def _headings(doc: str) -> list[str]:
    return [line.lstrip("#").strip() for line in doc.splitlines() if line.startswith("#")]


# -- the comparisons the requirement names -----------------------------------


def test_the_faq_compares_against_a_language_server(doc):
    assert "### Against a language server" in doc
    body = re.sub(r"\s+", " ", _section(doc, "Against a language server"))
    assert "over-approximate" in body, "the comparison must state the default is structural"
    assert "--resolve" in body, "the comparison must name the path that improves it"


def test_the_faq_compares_against_similarity_retrieval(doc):
    assert "### Against similarity retrieval" in doc
    body = re.sub(r"\s+", " ", _section(doc, "Against similarity retrieval"))
    assert "vector" in body.lower()
    assert "advisory" in body, "the contradiction scanner's limit must not be dropped here"


def test_the_faq_compares_against_plain_search(doc):
    assert "### Against plain search" in doc
    body = _section(doc, "Against plain search")
    assert "grep" in body.lower()


def test_the_comparisons_do_not_only_flatter_the_platform(doc):
    """Each comparison must name a case where the other tool wins."""
    for heading, phrase in (
        ("Against a language server", "does not replace"),
        ("Against similarity retrieval", "simpler"),
        ("Against plain search", "nothing here beats"),
    ):
        body = re.sub(r"\s+", " ", _section(doc, heading))
        assert phrase in body, f"{heading}: no concession to the alternative"


# -- adjacent tools it does not replace --------------------------------------


def test_the_faq_names_the_adjacent_tools_it_does_not_replace(doc):
    body = _section(doc, "What it does not replace")
    for tool in (
        "compiler",
        "language server",
        "linter",
        "test runner",
        "debugger",
        "version control",
        "vector database",
        "large language model",
    ):
        assert tool.lower() in body.lower(), f"'{tool}' is not named as unreplaced"


def test_the_faq_states_plainly_when_not_to_use_it(doc):
    body = _section(doc, "When not to use this")
    bullets = [line for line in body.splitlines() if line.startswith("- ")]
    assert len(bullets) >= 6, f"only {len(bullets)} reasons not to use it"
    assert "commercial" in body.lower(), "the licence limit is a real reason not to use it"


# -- verifying an install, checked against the real parser -------------------


def test_every_command_the_verification_section_names_is_registered(doc, subcommands):
    """The install check must not tell a reader to run something that does not exist."""
    body = _section(doc, "How to verify a working install")
    named = set(re.findall(r"\bdkg ([a-z][a-z-]*)\b", body))
    unknown = sorted(n for n in named if n not in subcommands)
    assert not unknown, f"the FAQ tells a reader to run commands this build does not have: {unknown}"


def test_the_verification_section_covers_install_then_data_then_query(doc, subcommands):
    body = _section(doc, "How to verify a working install")
    for command in ("--version", "dkg init", "dkg capabilities", "dkg doctor", "dkg ingest", "dkg search"):
        assert command in body, f"{command} missing from the verification steps"


def test_the_verification_section_says_what_a_healthy_result_looks_like(doc):
    body = re.sub(r"\s+", " ", _section(doc, "How to verify a working install"))
    assert "available" in body
    assert "is correct, not broken" in body, (
        "a fresh install shows many unavailable capabilities; the FAQ must say that is normal"
    )


def test_the_verification_section_points_at_the_diagnostic(doc):
    body = _section(doc, "How to verify a working install")
    assert "scripts/probe_environment.py" in body
    assert "--offline" in body, "the outbound probe must be disclosed where it is recommended"


# -- measured numbers must match their artifacts -----------------------------


def _number(doc: str, fragment: str) -> float:
    assert fragment in doc, f"the FAQ no longer states {fragment!r}; update this test"
    return float(fragment.replace(",", "").rstrip("."))


def test_the_structural_precision_matches_the_resolution_artifact(doc):
    blob = json.loads((EVIDENCE / "resolution_accuracy.json").read_text(encoding="utf-8"))
    measured = blob["per_language"]["python"]["blast_radius"]["structural"]["precision"]
    assert "precision 0.1081" in doc, "the FAQ no longer states the structural precision"
    assert measured == pytest.approx(0.1081, abs=5e-5), measured


def test_the_resolved_precision_and_recall_match_the_resolution_artifact(doc):
    blob = json.loads((EVIDENCE / "resolution_accuracy.json").read_text(encoding="utf-8"))
    resolved = blob["per_language"]["python"]["blast_radius"]["resolved"]
    assert "precision 1.0 with recall 1.0" in doc
    assert resolved["precision"] == 1.0 and resolved["recall"] == 1.0


def test_the_token_cost_figures_match_the_token_cost_artifact(doc, flat):
    blob = json.loads((EVIDENCE / "token_cost.json").read_text(encoding="utf-8"))
    aggregate = blob["aggregate"]
    assert _number(doc, "71,088") == aggregate["graph"]["tokens"]
    assert _number(doc, "34,744") == aggregate["strong"]["tokens"]
    assert "mean correctness 1.0 against 0.6206" in flat
    assert aggregate["graph"]["mean_correctness"] == 1.0
    assert aggregate["strong"]["mean_correctness"] == pytest.approx(0.6206, abs=5e-5)


def test_the_faq_reports_the_same_number_of_wins_the_artifact_records(flat):
    blob = json.loads((EVIDENCE / "token_cost.json").read_text(encoding="utf-8"))
    won = blob["graph_vs_strong"]["won_count"]
    total = blob["graph_vs_strong"]["task_count"]
    assert "two of the four tasks" in flat
    assert (won, total) == (2, 4), (won, total)


def test_the_faq_does_not_present_the_token_comparison_as_a_win(doc):
    body = re.sub(r"\s+", " ", _section(doc, "Against plain search"))
    assert "costs about twice the tokens" in body
    assert "loses on the other two" in body


# -- licensing wording -------------------------------------------------------


def test_the_faq_never_calls_the_project_open_source(doc):
    """Only ever in the negative, and the negation has to be on the same line.

    Heading lines are exempt because a heading can legitimately ask the
    question; the answer under it is checked separately and has to be "No".
    """
    offenders = [
        line.strip()
        for line in doc.splitlines()
        if not line.startswith("#")
        and re.search(r"open[ -]source|FOSS|free software", line, re.I)
        and not re.search(r"\bnot\b", line, re.I)
    ]
    assert not offenders, "the FAQ describes the project as open source: " + "; ".join(offenders)


def test_the_faq_answers_the_open_source_question_with_no(doc):
    body = _section(doc, "Is this open source?").strip()
    assert body.startswith("No."), body[:60]
    assert "source-available and non-commercial" in re.sub(r"\s+", " ", body)


def test_the_faq_states_the_licence_terms_accurately(doc):
    body = re.sub(r"\s+", " ", _section(doc, "Licensing"))
    assert "PolyForm Noncommercial 1.0.0" in body
    assert "no-modification" in body
    assert "Commercial use is not permitted" in body
    assert "Modification is not permitted" in body


def test_the_faq_says_nothing_is_separately_licensed(doc):
    body = re.sub(r"\s+", " ", _section(doc, "Is any part of it licensed separately?"))
    assert "No." in body
    assert "nothing is excluded" in body.lower()


def test_the_faq_records_the_earlier_apache_grant_as_irrevocable(doc):
    body = re.sub(r"\s+", " ", _section(doc, "What about the version I already have?"))
    assert "2026-08-05" in body
    assert "irrevocable" in body


# -- air gap -----------------------------------------------------------------


def test_the_faq_states_the_air_gap_default(doc):
    body = re.sub(r"\s+", " ", _section(doc, "Privacy and network"))
    assert "off by default" in body
    assert "--allow-network" in body
    assert "Never at runtime" in body, "the no-runtime-download rule must be stated"


# -- helper ------------------------------------------------------------------


def _section(doc: str, heading: str) -> str:
    """Body under a heading of any level, up to the next heading of that level or higher."""
    lines = doc.splitlines()
    start = level = None
    for i, line in enumerate(lines):
        m = re.match(r"^(#{2,4})\s+(.*)$", line)
        if m and m.group(2).strip() == heading:
            start, level = i, len(m.group(1))
            break
    assert start is not None, f"docs/FAQ.md has no heading {heading!r}"
    body: list[str] = []
    for line in lines[start + 1 :]:
        m = re.match(r"^(#{1,6})\s", line)
        if m and len(m.group(1)) <= level:
            break
        body.append(line)
    return "\n".join(body)

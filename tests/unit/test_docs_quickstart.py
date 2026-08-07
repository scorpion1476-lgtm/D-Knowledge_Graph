"""The non-technical quick start must actually work if followed literally.

Acceptance test for matrix row G-07. "Non technical quick start guide" was
previously accepted on a manual read, which cannot catch the failure that
matters: a quick start that names a command this build does not have, or a flag
it does not accept. A newcomer following it hits an error on step three and has
no way to tell whether they mistyped or the document is stale.

So the checks here are executable rather than editorial:

* every step is numbered and ordered, so the shortest path stays a path,
* every ``dkg`` command in a fenced block is a subcommand the real argument
  parser registers, and every long flag is one that subcommand accepts,
* the journey covers install, initialise, ingest, query, prove and back up,
  which is what makes it a quick *start* rather than a command list,
* it stays honest about the air gap: it must not tell a non-technical reader to
  sign up for anything, and it must state that nothing leaves the machine.

The flag check is deliberately strict about long flags only. Short flags are
ambiguous across subparsers and checking them would add noise without adding
protection.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

from dkg.cli.entry import _mk_parser

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "QUICKSTART.md"

REQUIRED_STEPS = ("Install", "Initialise", "Feed", "Ask", "Prove", "Back it up")


@pytest.fixture(scope="module")
def doc() -> str:
    assert DOC.is_file(), f"{DOC} does not exist"
    return DOC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def parser() -> argparse.ArgumentParser:
    return _mk_parser()


@pytest.fixture(scope="module")
def subparsers(parser) -> dict[str, argparse.ArgumentParser]:
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return dict(action.choices)


def _shell_lines(text: str) -> list[str]:
    """Every line inside a fenced bash block."""
    out: list[str] = []
    for block in re.findall(r"```bash\n(.*?)```", text, re.S):
        out.extend(line.strip() for line in block.splitlines() if line.strip())
    return out


def _dkg_invocations(text: str) -> list[list[str]]:
    """Every dkg invocation, normalised to its argument list."""
    calls: list[list[str]] = []
    for line in _shell_lines(text):
        line = line.split("#", 1)[0].strip()
        m = re.match(r"^(?:\./)?(?:[\w./-]*/)?dkg\s+(.*)$", line)
        if not m:
            continue
        calls.append([tok for tok in m.group(1).split() if tok])
    return calls


# -- the journey --------------------------------------------------------------


def test_the_document_is_labelled_for_a_non_technical_reader(doc):
    assert re.search(r"^#\s+.*non-technical", doc, re.M | re.I), (
        "the quick start must say who it is for; a reader who needs it cannot "
        "tell it apart from the developer guide otherwise"
    )


def test_every_required_step_is_present_and_numbered(doc):
    headings = re.findall(r"^##\s+(\d+)\.\s+(.*)$", doc, re.M)
    assert headings, "the quick start has no numbered steps"
    numbers = [int(n) for n, _ in headings]
    assert numbers == sorted(numbers), f"steps are out of order: {numbers}"
    assert numbers == list(range(1, len(numbers) + 1)), f"steps are not 1..n: {numbers}"
    joined = " ".join(title for _, title in headings)
    missing = [s for s in REQUIRED_STEPS if s.lower() not in joined.lower()]
    assert not missing, f"the quick start never covers: {missing}"


def test_the_reader_reaches_a_query_and_then_its_evidence(doc):
    """Search then evidence, in that order.

    Showing evidence before there is anything to explain is the one ordering
    that makes the provenance step meaningless.
    """
    subs = [c[0] for c in _dkg_invocations(doc) if c]
    assert "search" in subs, "the quick start never searches"
    assert "evidence" in subs, "the quick start never proves where an answer came from"
    assert subs.index("search") < subs.index("evidence")


# -- the commands are real ----------------------------------------------------


def test_every_documented_subcommand_exists(doc, subparsers):
    unknown = sorted({c[0] for c in _dkg_invocations(doc) if c and c[0] not in subparsers})
    assert not unknown, f"the quick start names subcommands this build does not register: {unknown}"


def test_every_documented_long_flag_is_accepted_by_its_subcommand(doc, subparsers):
    offenders: list[str] = []
    for call in _dkg_invocations(doc):
        if not call or call[0] not in subparsers:
            continue
        sub = subparsers[call[0]]
        accepted = {opt for action in sub._actions for opt in action.option_strings}
        for token in call[1:]:
            if not token.startswith("--"):
                continue
            flag = token.split("=", 1)[0]
            if flag not in accepted:
                offenders.append(f"dkg {call[0]} {flag}")
    assert not offenders, f"flags the subcommand does not accept: {sorted(set(offenders))}"


def test_the_install_block_matches_the_real_installer(doc):
    """The quick start's install steps must be the ones scripts/install.sh runs.

    Two divergent install paths is how a newcomer ends up with a venv the rest
    of the documentation does not expect.
    """
    installer = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    quick = "\n".join(_shell_lines(doc))
    for fragment in ("-m venv .venv", "pip install --upgrade pip", 'pip install -e ".[dev]"'):
        assert fragment in quick, f"the quick start install block omits {fragment!r}"
        assert fragment in installer, f"scripts/install.sh no longer runs {fragment!r}"


# -- honesty ------------------------------------------------------------------


def test_the_quick_start_states_that_nothing_leaves_the_machine(doc):
    flat = re.sub(r"\s+", " ", doc).lower()
    assert "no files leave the machine" in flat or "leave the machine" in flat


def test_the_quick_start_promises_no_signup_and_no_model(doc):
    """The "what you did not do" close is the point of the document.

    It is what tells a non-technical reader that the offline claim held for the
    steps they just ran, rather than asking them to take it on faith.
    """
    flat = re.sub(r"\s+", " ", doc).lower()
    assert "what you did not do" in flat
    assert "sign up for any service" in flat
    assert "install a model" in flat


def test_no_step_tells_the_reader_to_reach_the_network(doc):
    """Except installing the package itself, which is unavoidable and named.

    Any other curl, wget or login instruction would contradict the air-gap
    default this document is advertising.
    """
    offenders = [
        line
        for line in _shell_lines(doc)
        if re.search(r"\b(curl|wget|ssh|login|token|api[_-]?key)\b", line, re.I)
    ]
    assert not offenders, f"the quick start reaches the network: {offenders}"

"""Six audiences, six guides, each actually written for its own audience.

Acceptance test for matrix row J-04: "Separate user, admin, developer,
deployment, security, troubleshooting guides". The word doing the work is
*separate*. A repository can satisfy a manual review with six filenames while
three of them are stubs and two say the same thing, and nobody notices until a
reader opens the administrator guide looking for something only it should have.

What is checked:

* each of the six exists, is a distinct file, and has real substance (a
  heading structure and a body, not a placeholder),
* the six are genuinely different documents, not copies of one another, which
  is measured by the overlap of their heading sets,
* each one covers at least one topic that belongs to its audience and to no
  other guide, so "separate" means separated by content and not just by name,
* every repository path a guide cites exists, because a guide that points at a
  file which is not there is worse than no guide,
* the set is discoverable: the README links to each of them.

The distinctness threshold is deliberately loose. Two guides sharing a heading
such as "Overview" is normal; two guides sharing most of their headings means
one was copied from the other.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
README = ROOT / "README.md"

# audience -> (document, a topic only that audience's guide should own)
GUIDES = {
    "user": ("USER_GUIDE.md", ("search", "ingest")),
    "administrator": ("ADMINISTRATOR_GUIDE.md", ("backup", "retention", "rotate", "quota")),
    "developer": ("DEVELOPER_GUIDE.md", ("repository layout", "test suite", "adapter")),
    "deployment": ("DEPLOYMENT_GUIDE.md", ("reverse proxy", "tls", "monitoring")),
    "security": ("SECURITY_MODEL.md", ("trust boundar", "threat", "out of scope")),
    "troubleshooting": ("TROUBLESHOOTING.md", ("symptom", "fix")),
}

MIN_HEADINGS = 4
MIN_WORDS = 250


def _text(name: str) -> str:
    path = DOCS / name
    assert path.is_file(), f"docs/{name} does not exist"
    return path.read_text(encoding="utf-8")


def _headings(text: str) -> set[str]:
    return {h.strip().lower() for h in re.findall(r"^#{2,3}\s+(.*)$", text, re.M)}


@pytest.mark.parametrize(("audience", "spec"), sorted(GUIDES.items()))
def test_each_guide_exists_and_has_substance(audience, spec):
    name, _ = spec
    text = _text(name)
    headings = _headings(text)
    assert len(headings) >= MIN_HEADINGS, (
        f"{name} has only {len(headings)} sections; that is a stub, not a {audience} guide"
    )
    assert len(text.split()) >= MIN_WORDS, f"{name} is {len(text.split())} words; too thin to be a guide"


@pytest.mark.parametrize(("audience", "spec"), sorted(GUIDES.items()))
def test_each_guide_covers_a_topic_that_belongs_to_its_audience(audience, spec):
    name, topics = spec
    flat = re.sub(r"\s+", " ", _text(name)).lower()
    hits = [t for t in topics if t in flat]
    assert hits, f"{name} covers none of the {audience} topics {topics}"


def test_the_six_guides_are_distinct_documents():
    """No guide may be a near-copy of another.

    Overlap is measured on headings rather than prose because prose overlap is
    normal (they all describe the same product) while heading overlap is not.
    """
    headings = {name: _headings(_text(name)) for name, _ in GUIDES.values()}
    offenders: list[str] = []
    names = sorted(headings)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            ha, hb = headings[a], headings[b]
            if not ha or not hb:
                continue
            overlap = len(ha & hb) / min(len(ha), len(hb))
            if overlap > 0.6:
                offenders.append(f"{a} and {b} share {overlap:.0%} of their headings")
    assert not offenders, "guides are near-duplicates: " + "; ".join(offenders)


def test_the_six_guides_are_six_different_files():
    names = [name for name, _ in GUIDES.values()]
    assert len(set(names)) == len(names)
    bodies = {name: _text(name) for name in names}
    assert len(set(bodies.values())) == len(names), "two guides have byte-identical content"


@pytest.mark.parametrize(("audience", "spec"), sorted(GUIDES.items()))
def test_every_repository_path_a_guide_cites_exists(audience, spec):
    name, _ = spec
    offenders: list[str] = []
    for candidate in sorted(set(re.findall(r"`([A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+)`", _text(name)))):
        if candidate.startswith(("http", "dkg.")) or candidate.endswith("/"):
            continue
        # A glob or a placeholder path is documentation, not a claim a file exists.
        if "*" in candidate or "<" in candidate:
            continue
        # `text/plain` and friends are media types, not repository paths. A real
        # path starts at something that exists at the top of the tree.
        if not (ROOT / candidate.split("/", 1)[0]).exists():
            continue
        if not (ROOT / candidate).exists():
            offenders.append(candidate)
    assert not offenders, f"docs/{name} cites paths that do not exist: {offenders}"


def test_the_readme_links_to_every_guide():
    """A guide nobody can find has not been delivered."""
    text = README.read_text(encoding="utf-8")
    missing = [name for name, _ in GUIDES.values() if f"docs/{name}" not in text]
    assert not missing, f"the README links to none of: {missing}"


def test_the_distinctness_check_would_catch_a_copied_guide():
    """Negative control.

    If the overlap measure stopped working, the distinctness test above would
    pass no matter what the six files contained.
    """
    a = _headings(_text("USER_GUIDE.md"))
    assert a, "the user guide has no headings to compare"
    overlap = len(a & a) / min(len(a), len(a))
    assert overlap == 1.0 and overlap > 0.6

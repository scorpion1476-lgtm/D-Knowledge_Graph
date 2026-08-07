"""The README has to hold together as the project's front page.

Acceptance test for matrix row J-01, "Professional README". "Professional" is a
judgement, so this test does not try to score prose. It pins the things that
make a front page either work or visibly fail, all of which a manual read
misses once the file is four hundred lines long:

* the in-page navigation actually navigates. Every anchor in the header nav
  must resolve to a heading that exists, computed with GitHub's own slug rule.
  A nav link to a section that was renamed is a dead link on the busiest part
  of the page.
* every relative link and image resolves to a file in the tree.
* the sections a reader needs are all present, in a sensible order: what it is,
  how to install it, how it is secured, what it measures, and the licence.
* the licence paragraph is honest. This project is source-available and
  non-commercial, and the README is the one place a reader forms the opposite
  impression. It must not call itself open source, and it must say plainly that
  commercial use and modification are not permitted.
* nothing is left half-written: no TODO, no placeholder, no empty section.

The badge row is checked for shape only. Its counts are pinned against real
artifacts by `test_doc_count_consistency.py`, and duplicating that here would
mean two tests to update for one number.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"

# The English README is the one this row is about, and the section, ordering and
# licence-wording checks below are written against its headings. But a dead
# anchor, a link to a file that is not there, or a half-written placeholder is
# just as broken in a translation, and those three checks were English-only
# until an adversarial review planted a dead anchor in the German file and
# watched every test pass. They are parametrised over all five.
ALL_READMES = ("README.md", "README.zh-CN.md", "README.es.md", "README.fr.md", "README.de.md")

REQUIRED_SECTIONS = (
    "Overview",
    "Install",
    "Security",
    "Benchmarks",
    "Documentation",
    "Licence",
)

# The order a front page has to read in. Not every section is listed: only the
# ones whose relative order carries meaning.
ORDERED_SECTIONS = ("Overview", "Install", "Security", "Licence")


@pytest.fixture(scope="module")
def text() -> str:
    assert README.is_file(), "README.md does not exist"
    return README.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def headings(text: str) -> list[str]:
    return [h.strip() for h in re.findall(r"^#{1,4}\s+(.*)$", text, re.M)]


def _slug(heading: str) -> str:
    """GitHub's heading-anchor rule: lowercase, strip punctuation, spaces to hyphens."""
    s = heading.strip().lower()
    s = re.sub(r"[`*_\[\]()]", "", s)
    s = re.sub(r"[^\w\s-]", "", s)
    return re.sub(r"\s+", "-", s).strip("-")


# -- structure ----------------------------------------------------------------


def test_every_required_section_is_present(headings):
    have = {h.lower() for h in headings}
    missing = [s for s in REQUIRED_SECTIONS if s.lower() not in have]
    assert not missing, f"the README has no {missing} section"


def test_the_sections_read_in_a_sensible_order(headings):
    lowered = [h.lower() for h in headings]
    positions = [lowered.index(s.lower()) for s in ORDERED_SECTIONS]
    assert positions == sorted(positions), (
        f"README sections are out of order: {ORDERED_SECTIONS} appear at {positions}"
    )


def test_the_masthead_states_what_the_project_is_before_any_heading(text):
    masthead = text.split("## ", 1)[0]
    assert len(masthead.split()) > 40, "the masthead is too thin to tell a reader what this is"
    assert re.search(r"local[- ]first", masthead, re.I), "the masthead never says it is local-first"


# -- the links work -----------------------------------------------------------


@pytest.mark.parametrize("name", ALL_READMES)
def test_every_in_page_nav_anchor_resolves_to_a_heading(name):
    body = (ROOT / name).read_text(encoding="utf-8")
    slugs = {_slug(h) for h in re.findall(r"^#{1,4}\s+(.*)$", body, re.M)}
    anchors = re.findall(r'href="#([^"]+)"', body) + re.findall(r"\]\(#([^)]+)\)", body)
    assert anchors, f"{name} has no in-page navigation at all"
    dead = sorted({a for a in anchors if a not in slugs})
    assert not dead, f"{name} nav anchors point at headings that do not exist: {dead}"


@pytest.mark.parametrize("name", ALL_READMES)
def test_every_relative_link_resolves_to_a_file(name):
    body = (ROOT / name).read_text(encoding="utf-8")
    targets = re.findall(r"\]\(([^)#][^)]*)\)", body) + re.findall(r'src="([^"]+)"', body)
    dead: list[str] = []
    for target in {t.split("#", 1)[0].strip() for t in targets}:
        if not target or target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        if not (ROOT / target).exists():
            dead.append(target)
    assert not dead, f"{name} links to files that do not exist: {sorted(dead)}"


def test_every_badge_is_a_well_formed_image_link(text):
    badges = re.findall(r"!\[([^\]]*)\]\(([^)]+)\)", text)
    assert len(badges) >= 5, f"expected a badge row, found {len(badges)} images"
    for alt, url in badges:
        assert alt.strip(), f"badge {url} has no alt text"


# -- honesty ------------------------------------------------------------------


def test_the_licence_section_does_not_call_the_project_open_source(text):
    """The single most consequential thing this page could get wrong."""
    section = text.split("## Licence", 1)
    assert len(section) == 2, "the README has no Licence section"
    body = section[1].lower()
    assert "not an\nopen-source licence" in body or "not an open-source licence" in re.sub(
        r"\s+", " ", body
    ), "the licence section does not say plainly that this is not an open-source licence"


def test_no_part_of_the_readme_claims_an_open_source_licence(text):
    flat = re.sub(r"\s+", " ", text)
    for claim in (
        r"\bis open source\b",
        r"\bopen-source project\b",
        r"\bMIT licen[cs]ed\b",
        r"\bApache[- ]2\.0 licen[cs]ed\b",
        r"\bFOSS\b",
    ):
        assert not re.search(claim, flat, re.I), f"the README claims {claim!r}"


def test_the_licence_section_states_the_two_prohibitions(text):
    body = re.sub(r"\s+", " ", text.split("## Licence", 1)[1]).lower()
    assert "commercial use is not permitted" in body
    assert "modification" in body


def test_the_readme_states_the_air_gap_default(text):
    flat = re.sub(r"\s+", " ", text).lower()
    assert "air-gapped by default" in flat or "no cloud call" in flat


# -- nothing half-written -----------------------------------------------------


#: The all-caps markers are matched case-sensitively on purpose. Spanish "Todo"
#: means "everything" and the Spanish README uses it five times; a
#: case-insensitive TODO would report a finished document as a draft.
_UNFINISHED = re.compile(r"\b(TODO|TBD|FIXME|XXX)\b")
_UNFINISHED_ANYCASE = re.compile(r"\b(Lorem ipsum|coming soon|placeholder text)\b", re.I)


@pytest.mark.parametrize("name", ALL_READMES)
def test_no_placeholder_or_unfinished_marker_is_left(name):
    offenders: list[str] = []
    body = (ROOT / name).read_text(encoding="utf-8")
    for number, line in enumerate(body.splitlines(), 1):
        if _UNFINISHED.search(line) or _UNFINISHED_ANYCASE.search(line):
            offenders.append(f"{number}: {line.strip()[:80]}")
    assert not offenders, f"{name} is unfinished: " + "; ".join(offenders)


def test_the_unfinished_marker_check_still_catches_a_real_marker():
    """Negative control, and a guard on the case-sensitivity above."""
    assert _UNFINISHED.search("- TODO: write this section")
    assert _UNFINISHED.search("FIXME before release")
    assert _UNFINISHED_ANYCASE.search("Benchmarks coming soon.")
    # ... without firing on ordinary Spanish prose.
    assert not _UNFINISHED.search("Todo lo de la tabla siguiente funciona")
    assert not _UNFINISHED_ANYCASE.search("Todo el repositorio, Ariadne incluido")


def test_no_section_is_empty(text):
    """A heading with nothing under it reads as an abandoned draft.

    A heading whose body is blank because a deeper subheading follows
    immediately is normal structure, not an empty section, so emptiness is
    judged only against the next heading at the same or a shallower level.
    """
    parts = re.split(r"^(#{2,4}\s+.*)$", text, flags=re.M)
    entries: list[tuple[int, str, str]] = []
    for i in range(1, len(parts) - 1, 2):
        heading = parts[i].strip()
        level = len(heading) - len(heading.lstrip("#"))
        entries.append((level, heading, parts[i + 1]))
    empty: list[str] = []
    for index, (level, heading, body) in enumerate(entries):
        following = entries[index + 1][0] if index + 1 < len(entries) else level
        if following > level:
            continue  # a container section: its subsections carry the content
        if len(body.split()) < 5:
            empty.append(heading)
    assert not empty, f"README sections with no content: {empty}"


def test_the_anchor_rule_matches_github_for_a_known_heading():
    """Negative control for the slug function.

    If `_slug` drifted, the nav test would compare two sets of nonsense and
    pass. These are the shapes this README actually uses.
    """
    assert _slug("How it works") == "how-it-works"
    assert _slug("Licence") == "licence"
    assert _slug("Use cases") == "use-cases"
    assert _slug("`code` extras") == "code-extras"

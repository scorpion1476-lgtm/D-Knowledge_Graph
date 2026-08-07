"""A translated README must carry the same claims and the same numbers.

This is the requirement most likely to be met dishonestly, because a translation
is the easiest place in a repository to soften a limitation: drop the word
"advisory", round a figure, quietly lose the sentence that says a corpus does
not represent natural photographs, and nobody who reads only English ever knows.

So the checks below are mechanical rather than editorial.

* **Numbers.** Every numeric token in the English prose is extracted, and the
  multiset must be identical in every translation. A dropped figure, an added
  one, or a figure written twice fails. URLs are stripped first (a badge URL
  carries digits that are not claims) and so are fenced code blocks, which are
  covered by their own stricter check.
* **Code blocks.** Every fenced block must be byte-identical to the English one,
  in the same order. That covers the commands, and it covers the Mermaid
  diagrams, which carry the precision figures. Keeping them in English is the
  documented convention: they stay executable and cannot drift.
* **Structure.** The sequence of heading levels, the number of table rows, and
  the set of URLs must all match, so a translation cannot silently omit a
  section, a table row, or a link.
* **It is a translation, not a copy.** The prose must actually be in another
  language, and it must say it is a translation and that the English is
  authoritative.
* **No em dash and no en dash**, which is a repository-wide rule and is very
  easy to break when writing prose in a language whose typography prefers them.

What these checks cannot do is verify that the meaning is faithful. That is
stated plainly here rather than implied away: the mechanical checks catch a
dropped number, a dropped section, a dropped link, and a dropped code block, and
they do not catch a mistranslated sentence.
"""

from __future__ import annotations

import collections
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ENGLISH = ROOT / "README.md"

NUMBER = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?%?")
FENCE = re.compile(r"^\s*```")
HEADING = re.compile(r"^(#{1,6})\s")
URL = re.compile(r"https?://[^\s)\"<>]+")
WORD = re.compile(r"[A-Za-zÀ-ɏ一-鿿]{3,}")

# The requirement asks for at least four further languages.
MINIMUM_TRANSLATIONS = 4

# A translation whose prose shares most of its vocabulary with the English is
# not a translation. Measured share on the four shipped translations is between
# 0.12 and 0.19; the bound below is far above all of them and far below what an
# untranslated copy would score.
MAX_SHARED_VOCABULARY = 0.5


def _translations() -> list[Path]:
    return sorted(p for p in ROOT.glob("README.*.md") if p.name != "README.md")


TRANSLATIONS = _translations()


def _split(text: str) -> tuple[str, list[str]]:
    """Prose outside fenced blocks, and the fenced blocks themselves in order."""
    prose: list[str] = []
    blocks: list[str] = []
    current: list[str] | None = None
    for line in text.splitlines():
        if FENCE.match(line):
            if current is None:
                current = []
            else:
                blocks.append("\n".join(current))
                current = None
            continue
        (current if current is not None else prose).append(line)
    assert current is None, "unclosed fenced code block"
    return "\n".join(prose), blocks


def _numbers(prose: str) -> collections.Counter:
    return collections.Counter(NUMBER.findall(URL.sub("", prose)))


def _heading_levels(prose: str) -> list[int]:
    return [len(m.group(1)) for line in prose.splitlines() if (m := HEADING.match(line))]


def _table_rows(prose: str) -> int:
    return len([ln for ln in prose.splitlines() if ln.strip().startswith("|")])


def _vocabulary(prose: str) -> set[str]:
    stripped = URL.sub("", re.sub(r"`[^`]*`", "", prose))
    return {w.lower() for w in WORD.findall(stripped)}


@pytest.fixture(scope="module")
def english() -> dict:
    text = ENGLISH.read_text(encoding="utf-8")
    prose, blocks = _split(text)
    return {
        "text": text,
        "prose": prose,
        "blocks": blocks,
        "numbers": _numbers(prose),
        "levels": _heading_levels(prose),
        "rows": _table_rows(prose),
        "urls": collections.Counter(URL.findall(text)),
        "vocabulary": _vocabulary(prose),
    }


def _loaded(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    prose, blocks = _split(text)
    return {
        "text": text,
        "prose": prose,
        "blocks": blocks,
        "numbers": _numbers(prose),
        "levels": _heading_levels(prose),
        "rows": _table_rows(prose),
        "urls": collections.Counter(URL.findall(text)),
        "vocabulary": _vocabulary(prose),
    }


# -- the set of translations -------------------------------------------------


def test_at_least_four_further_languages_are_published():
    assert len(TRANSLATIONS) >= MINIMUM_TRANSLATIONS, (
        f"the requirement asks for at least {MINIMUM_TRANSLATIONS} further languages, "
        f"found {[p.name for p in TRANSLATIONS]}"
    )


def test_the_english_readme_links_to_every_translation():
    """An unlinked translation is a file nobody will ever open."""
    text = ENGLISH.read_text(encoding="utf-8")
    missing = [p.name for p in TRANSLATIONS if p.name not in text]
    assert not missing, f"README.md does not link to {missing}"


@pytest.mark.parametrize("path", TRANSLATIONS, ids=lambda p: p.name)
def test_each_translation_links_back_to_english_and_to_its_siblings(path):
    text = path.read_text(encoding="utf-8")
    assert "README.md" in text, f"{path.name} does not link back to the English original"
    for other in TRANSLATIONS:
        if other == path:
            continue
        assert other.name in text, f"{path.name} does not link to {other.name}"


# -- the drift check the requirement asks for --------------------------------


@pytest.mark.parametrize("path", TRANSLATIONS, ids=lambda p: p.name)
def test_no_stated_number_drifts_from_the_english_original(path, english):
    """Every measured figure, in the same quantity, in every language."""
    theirs = _loaded(path)["numbers"]
    missing = english["numbers"] - theirs
    extra = theirs - english["numbers"]
    assert not missing, (
        f"{path.name} drops numbers the English README states: {dict(missing)}"
    )
    assert not extra, (
        f"{path.name} states numbers the English README does not: {dict(extra)}"
    )


@pytest.mark.parametrize("path", TRANSLATIONS, ids=lambda p: p.name)
def test_every_code_block_is_identical_to_the_english_one(path, english):
    """Commands and diagrams are kept in English so they cannot drift.

    The Mermaid diagrams carry the structural and resolved precision figures, so
    this is a numeric check as much as a formatting one.
    """
    theirs = _loaded(path)["blocks"]
    assert len(theirs) == len(english["blocks"]), (
        f"{path.name} has {len(theirs)} code blocks, the English README has "
        f"{len(english['blocks'])}"
    )
    for i, (mine, other) in enumerate(zip(english["blocks"], theirs)):
        assert mine == other, f"{path.name}: code block {i} differs from the English one"


@pytest.mark.parametrize("path", TRANSLATIONS, ids=lambda p: p.name)
def test_the_section_structure_matches(path, english):
    theirs = _loaded(path)["levels"]
    assert theirs == english["levels"], (
        f"{path.name} has a different heading structure: {len(theirs)} headings at levels "
        f"{theirs} against {len(english['levels'])} at {english['levels']}"
    )


@pytest.mark.parametrize("path", TRANSLATIONS, ids=lambda p: p.name)
def test_no_table_row_is_dropped(path, english):
    """A dropped row is a dropped claim, most likely a dropped caveat."""
    theirs = _loaded(path)["rows"]
    assert theirs == english["rows"], (
        f"{path.name} has {theirs} table rows, the English README has {english['rows']}"
    )


@pytest.mark.parametrize("path", TRANSLATIONS, ids=lambda p: p.name)
def test_every_link_the_english_readme_carries_is_carried_here(path, english):
    theirs = _loaded(path)["urls"]
    missing = english["urls"] - theirs
    assert not missing, f"{path.name} drops links: {sorted(missing)}"


# -- it is a translation, and it says so --------------------------------------


@pytest.mark.parametrize("path", TRANSLATIONS, ids=lambda p: p.name)
def test_the_prose_is_actually_in_another_language(path, english):
    """Guard against a copy of the English file with a new name."""
    loaded = _loaded(path)
    assert loaded["prose"] != english["prose"], f"{path.name} is a copy of the English prose"
    shared = loaded["vocabulary"] & english["vocabulary"]
    share = len(shared) / max(len(loaded["vocabulary"]), 1)
    assert share < MAX_SHARED_VOCABULARY, (
        f"{path.name} shares {share:.2f} of its vocabulary with the English README, "
        "which is what an untranslated copy looks like"
    )


@pytest.mark.parametrize("path", TRANSLATIONS, ids=lambda p: p.name)
def test_each_translation_says_the_english_version_is_authoritative(path):
    """A reader must know which document wins when they disagree."""
    flat = re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))
    assert "README.md" in flat
    markers = (
        "The English version is the authoritative",  # placeholder for future languages
        "英文原版具有权威性",
        "La versión en inglés es la autoritativa",
        "La version anglaise fait foi",
        "Die englische Fassung ist maßgeblich",
    )
    assert any(m in flat for m in markers), (
        f"{path.name} does not state that the English original is authoritative"
    )


@pytest.mark.parametrize("path", TRANSLATIONS, ids=lambda p: p.name)
def test_each_translation_records_the_number_format_convention(path):
    """Numbers are kept in the English format so they can be compared literally."""
    flat = re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))
    markers = ("国际格式", "formato internacional", "format international", "internationale Format")
    assert any(m in flat for m in markers), (
        f"{path.name} does not record that the figures keep the English number format"
    )


# -- repository-wide rules that translations break most easily ---------------


@pytest.mark.parametrize("path", TRANSLATIONS, ids=lambda p: p.name)
def test_no_translation_contains_an_em_dash_or_an_en_dash(path):
    # Built from codepoints rather than written literally. Spelling them out
    # puts them in a tracked file and trips the repository-wide scan this test
    # exists to support, which is exactly how it first failed that scan.
    text = path.read_text(encoding="utf-8")
    pairs = (("en dash", chr(0x2013)), ("em dash", chr(0x2014)))
    found = {name: text.count(char) for name, char in pairs if char in text}
    assert not found, f"{path.name} contains {found}"


#: The licence sentences that must survive translation, per language.
#:
#: Asserting that ASCII tokens are PRESENT cannot detect the claim being
#: reversed: an adversarial review flipped "this is NOT an open-source licence"
#: to "this IS an open-source licence" in German and in Chinese and every test
#: still passed, because "PolyForm Noncommercial 1.0.0" and the rest were all
#: still there. The English equivalent was caught and the translations were not,
#: which is the worst possible split. These are the actual negations, in the
#: actual languages, so removing or inverting one fails.
#:
#: The second entry in each list is the legacy-grant sentence. The German
#: rendering said "danach" (afterwards), which extended the old permissive grant
#: to future recipients instead of limiting it to those who received a copy
#: under it. That is a broadened licence claim, and nothing detected it.
REQUIRED_LICENCE_CLAIMS: dict[str, tuple[str, ...]] = {
    "README.zh-CN.md": (
        "这不是一个开源许可",
        "据此获得副本的人",
    ),
    "README.es.md": (
        "Esta no es una licencia de código abierto",
        "recibiera una copia bajo ella",
    ),
    "README.fr.md": (
        "Ceci n'est pas une licence open source",
        "à ce titre",
    ),
    "README.de.md": (
        "Dies ist keine quelloffene Lizenz",
        "unter dieser Lizenz eine Kopie erhalten haben",
    ),
}


@pytest.mark.parametrize("path", TRANSLATIONS, ids=lambda p: p.name)
def test_no_translation_softens_the_licence(path):
    """The licence terms are the claim most costly to get wrong in translation."""
    text = path.read_text(encoding="utf-8")
    assert "PolyForm Noncommercial 1.0.0" in text
    assert "D-Knowledge Graph Source-Available Non-Commercial Licence" in text
    assert "Apache-2.0" in text
    assert "2026-08-05" in text


@pytest.mark.parametrize("path", TRANSLATIONS, ids=lambda p: p.name)
def test_every_translation_still_denies_that_this_is_open_source(path):
    """The negation itself, in the target language, not a token near it."""
    required = REQUIRED_LICENCE_CLAIMS.get(path.name)
    assert required, f"no licence claims registered for {path.name}; add them before shipping it"
    text = path.read_text(encoding="utf-8")
    missing = [claim for claim in required if claim not in text]
    assert not missing, (
        f"{path.name} no longer carries the licence claim(s) {missing}. Either the negation was "
        "removed or reversed, or the legacy Apache grant was broadened beyond those who received "
        "a copy under it."
    )


def test_every_translation_has_registered_licence_claims():
    """A new translation must not slip in with nothing guarding its licence text."""
    registered = set(REQUIRED_LICENCE_CLAIMS)
    shipped = {p.name for p in TRANSLATIONS}
    assert shipped == registered, (
        f"translations without registered licence claims: {sorted(shipped - registered)}; "
        f"registered claims for absent translations: {sorted(registered - shipped)}"
    )


@pytest.mark.parametrize("path", TRANSLATIONS, ids=lambda p: p.name)
def test_no_translation_drops_a_caveat_bearing_document_reference(path, english):
    """The documents a caveat points at must survive translation."""
    cited = set(re.findall(r"`(docs/[A-Za-z_]+\.md)`", english["text"]))
    cited |= set(re.findall(r"\((docs/[A-Za-z_]+\.md)\)", english["text"]))
    assert cited, "the English README cites no documents; update this test"
    text = path.read_text(encoding="utf-8")
    missing = sorted(c for c in cited if c not in text)
    assert not missing, f"{path.name} drops references to {missing}"


# -- the gate itself must be able to fail ------------------------------------


def test_the_number_extractor_sees_the_figures_that_matter(english):
    """A guard on the guard: these must be figures the English README states.

    The list is deliberately specific rather than generic. When a measured
    figure changes, this failing is the intended signal to update it, which is
    also a reminder that the same figure has to move in four translations.
    """
    for figure in ("0.9375", "0.9473", "0.6206", "0.6667", "0.982", "71,088", "34,744"):
        assert english["numbers"][figure] >= 1, f"{figure} is not being extracted"


def test_the_number_extractor_ignores_badge_urls():
    """A badge URL carries digits that are not claims."""
    prose = "![x](https://img.shields.io/badge/tests-1331%20passing-2c7a3f.svg) and 42 nodes"
    assert _numbers(prose) == collections.Counter({"42": 1})


def test_a_dropped_number_is_detected(english):
    """The mutation the drift check exists to catch, in miniature."""
    damaged = english["prose"].replace("0.6206", "", 1)
    assert (english["numbers"] - _numbers(damaged))["0.6206"] == 1


def test_a_changed_number_is_detected(english):
    damaged = english["prose"].replace("0.9375", "0.99", 1)
    theirs = _numbers(damaged)
    assert (english["numbers"] - theirs)["0.9375"] == 1
    assert (theirs - english["numbers"])["0.99"] == 1

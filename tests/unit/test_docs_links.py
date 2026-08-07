"""Documentation cross-references must resolve. All of them, in every document.

The README navigation carried two dead anchors, `#faq` and `#troubleshooting`,
for as long as it had those entries, and nothing noticed: the only cross-
reference check in this repository looked at cited repository paths, in one
document, the requirements matrix. This file closes that.

Three checks, with deliberately different scopes, each stated rather than left
to be inferred.

**Anchors and relative links: every tracked markdown document, no exceptions.**
An in-document anchor must match a heading in the same file; a relative link must
resolve to a file that exists; and a relative link that carries a fragment must
find that heading in the target document. Anchor slugs follow the forge's rules:
lowercase, spaces to hyphens, punctuation dropped, duplicates suffixed. Fenced
code blocks are skipped, because a link inside a fence is a sample and not a
link.

**Cited repository paths: rooted paths only.** A backticked path counts as a
citation of this repository when its first segment is a tracked top-level entry,
because that is what makes it a claim about this tree rather than a mention of
some other one. `origin/main` and `text/plain` are not paths in this repository
and are not treated as though they were. A path the citing line explicitly says
is absent is allowed to be absent: a document is permitted to name something it
is telling you does not exist.

**Scope of the path check.** It runs over every tracked markdown document with
no exceptions: the README and its translations, `docs/`, the root governance
documents, and the package documentation. It once carried an exclusion list for
a set of internal build-journey reports at the repository root, which quoted
historical git refs and other projects' directory layouts, so a path in them was
not a claim about this tree. Those reports are no longer tracked, so the
exclusion is gone with them and the check has no exemptions left.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
MD_LINK = re.compile(r"\[(?:[^\]\\]|\\.)*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
HTML_HREF = re.compile(r"<a\s[^>]*href=\"([^\"]+)\"", re.I)
HTML_SRC = re.compile(r"<(?:img|source)\s[^>]*src=\"([^\"]+)\"", re.I)
HTML_ANCHOR_NAME = re.compile(r"<a\s[^>]*(?:name|id)=\"([^\"]+)\"", re.I)
CITED_PATH = re.compile(r"`([A-Za-z0-9_.][A-Za-z0-9_./+-]*/[A-Za-z0-9_./+-]+)`")

# A cited path may be missing when the citing line says it is missing.
ABSENCE = re.compile(
    r"\b(?:intentionally not|deliberately not|not bundled|not shipped|not vendored|"
    r"does not exist|is not present|never created|no longer exists)\b",
    re.I,
)

def _tracked_markdown() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "*.md"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    assert out, "git reported no tracked markdown at all"
    return sorted(out)


def _tracked_top_level() -> set[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    return {line.split("/", 1)[0] for line in out}


TRACKED_MARKDOWN = _tracked_markdown()
TOP_LEVEL = _tracked_top_level()
PATH_CHECKED = TRACKED_MARKDOWN


def _body_lines(text: str):
    """Lines outside fenced code blocks."""
    in_fence = False
    marker = ""
    for line in text.splitlines():
        m = FENCE.match(line)
        if m:
            if not in_fence:
                in_fence, marker = True, m.group(1)
            elif line.strip().startswith(marker):
                in_fence = False
            continue
        if not in_fence:
            yield line


def _slug(text: str) -> str:
    """The forge's heading slug: lowercase, spaces to hyphens, punctuation dropped."""
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = text.replace("`", "")
    text = text.strip().lower().replace(" ", "-")
    return re.sub(r"[^0-9a-zÀ-￿_\-]", "", text)


_ANCHOR_CACHE: dict[Path, set[str]] = {}


def _anchors(path: Path) -> set[str]:
    """Every anchor a document offers: heading slugs plus explicit HTML anchors."""
    if path in _ANCHOR_CACHE:
        return _ANCHOR_CACHE[path]
    found: set[str] = set()
    seen: dict[str, int] = {}
    for line in _body_lines(path.read_text(encoding="utf-8")):
        m = HEADING.match(line)
        if m:
            base = _slug(m.group(2))
            n = seen.get(base, 0)
            found.add(base if n == 0 else f"{base}-{n}")
            seen[base] = n + 1
        found |= set(HTML_ANCHOR_NAME.findall(line))
    _ANCHOR_CACHE[path] = found
    return found


def _targets(text: str) -> list[str]:
    out: list[str] = []
    for line in _body_lines(text):
        out += MD_LINK.findall(line)
        out += HTML_HREF.findall(line)
        out += HTML_SRC.findall(line)
    return out


# -- anchors and relative links, every tracked document ----------------------


@pytest.mark.parametrize("rel", TRACKED_MARKDOWN)
def test_every_in_document_anchor_resolves(rel):
    path = ROOT / rel
    own = _anchors(path)
    dead = [
        t
        for t in _targets(path.read_text(encoding="utf-8"))
        if t.startswith("#") and len(t) > 1 and t[1:] not in own
    ]
    assert not dead, (
        f"{rel} links to sections that do not exist in it: {sorted(set(dead))}. "
        "Either add the section or fix the link."
    )


@pytest.mark.parametrize("rel", TRACKED_MARKDOWN)
def test_every_relative_link_resolves(rel):
    path = ROOT / rel
    broken: list[str] = []
    for target in _targets(path.read_text(encoding="utf-8")):
        if target.startswith(("http://", "https://", "mailto:", "tel:", "#")):
            continue
        head = target.split("#", 1)[0]
        if not head:
            continue
        if not (path.parent / head).exists():
            broken.append(target)
    assert not broken, f"{rel} links to files that do not exist: {sorted(set(broken))}"


@pytest.mark.parametrize("rel", TRACKED_MARKDOWN)
def test_every_cross_document_fragment_resolves(rel):
    """A link into another document must find the section it names."""
    path = ROOT / rel
    broken: list[str] = []
    for target in _targets(path.read_text(encoding="utf-8")):
        if target.startswith(("http://", "https://", "mailto:", "tel:", "#")):
            continue
        head, _, fragment = target.partition("#")
        if not (head and fragment):
            continue
        dest = path.parent / head
        if dest.suffix != ".md" or not dest.is_file():
            continue
        if fragment not in _anchors(dest):
            broken.append(target)
    assert not broken, f"{rel} links to sections that do not exist: {sorted(set(broken))}"


# -- cited repository paths --------------------------------------------------


@pytest.mark.parametrize("rel", PATH_CHECKED)
def test_every_cited_repository_path_resolves(rel):
    path = ROOT / rel
    missing: list[str] = []
    for line in _body_lines(path.read_text(encoding="utf-8")):
        for cited in CITED_PATH.findall(line):
            if cited.split("/", 1)[0] not in TOP_LEVEL:
                continue
            if "*" in cited or "?" in cited:
                continue
            if (ROOT / cited).exists():
                continue
            if ABSENCE.search(line):
                continue
            missing.append(cited)
    assert not missing, (
        f"{rel} cites repository paths that do not exist: {sorted(set(missing))}"
    )


# -- the gate itself must be able to fail ------------------------------------


def test_the_slug_rule_matches_the_forge_conventions():
    assert _slug("How it works") == "how-it-works"
    assert _slug("What is `dkg doctor`?") == "what-is-dkg-doctor"
    assert _slug("Supply chain, hardened") == "supply-chain-hardened"
    assert _slug("### Against a language server".lstrip("# ")) == "against-a-language-server"


def test_duplicate_headings_get_the_numbered_suffix(tmp_path):
    doc = tmp_path / "d.md"
    doc.write_text("## Notes\n\n## Notes\n\n## Notes\n", encoding="utf-8")
    assert _anchors(doc) == {"notes", "notes-1", "notes-2"}


def test_a_dead_anchor_is_detected(tmp_path):
    """The exact bug this gate exists for, in miniature."""
    doc = tmp_path / "d.md"
    doc.write_text("[go](#nowhere)\n\n## Somewhere\n", encoding="utf-8")
    own = _anchors(doc)
    assert [t for t in _targets(doc.read_text(encoding="utf-8")) if t[1:] not in own] == [
        "#nowhere"
    ]


def test_a_link_inside_a_code_fence_is_not_treated_as_a_link(tmp_path):
    doc = tmp_path / "d.md"
    doc.write_text("```\n[sample](#not-real)\n```\n\n## Real\n", encoding="utf-8")
    assert _targets(doc.read_text(encoding="utf-8")) == []


def test_a_path_the_line_says_is_absent_is_allowed(tmp_path):
    assert ABSENCE.search("`scripts/dep_audit.py` is intentionally not bundled because")
    assert not ABSENCE.search("see `scripts/sbom.py` for the generator")


def test_the_scope_of_each_check_is_not_silently_empty():
    """A parametrised gate over an empty list passes and checks nothing."""
    assert len(TRACKED_MARKDOWN) >= 20, TRACKED_MARKDOWN
    assert len(PATH_CHECKED) >= 20, PATH_CHECKED
    assert "README.md" in TRACKED_MARKDOWN and "README.md" in PATH_CHECKED
    assert {"docs", "src", "scripts", "tests"} <= TOP_LEVEL


def test_the_path_check_has_no_exemptions():
    """The exclusion list is gone, and must not creep back unrecorded.

    This test used to assert the opposite: that an exclusion list existed, that
    every file on it was still present, and that the docstring said so, because
    an exclusion nobody can see is a quiet weakening. The internal build-journey
    reports it covered are no longer tracked, so the list was removed rather
    than left pointing at nothing. The guard is kept, inverted: the path check
    now runs over every tracked markdown document, and anyone narrowing it again
    has to change this test and say why in the docstring.
    """
    assert __doc__ is not None
    assert PATH_CHECKED == TRACKED_MARKDOWN, (
        "the cited-path check no longer covers every tracked document; "
        f"missing: {sorted(set(TRACKED_MARKDOWN) - set(PATH_CHECKED))}"
    )
    assert "no exceptions" in __doc__

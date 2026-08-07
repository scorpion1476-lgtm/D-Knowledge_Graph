"""A project-owned ignore file for code-graph indexing.

Tracked and indexed are not the same question. A repository legitimately tracks
vendored dependencies, generated clients, fixture corpora, and minified bundles,
and every one of those puts thousands of symbols into the graph that nobody will
ever ask about. They crowd out the real answers, they move every threshold that
is derived from the graph's own distribution, and they cost tokens on each read.

``.dkgignore`` is the project's own statement about which of its tracked paths
should stay out of the code graph. It is deliberately NOT ``.gitignore``: a file
can be worth versioning and not worth indexing, and conflating the two would
mean deleting history to fix an index.

The syntax is the familiar one, and the supported subset is stated here rather
than implied:

    # comment                a line starting with # is a comment
    build/                   a trailing slash matches a directory and everything under it
    *.min.js                 a pattern without a slash matches the BASENAME at any depth
    vendor/**/*.go           ** crosses directory separators; a single * does not
    src/generated.py         a pattern containing a slash is anchored at the repository root
    !src/keep.py             a leading ! re-includes a path an earlier pattern excluded

Later patterns win, which is what makes negation useful. Anything not listed
above (character classes, escaping) is not interpreted specially, and a pattern
using it simply will not match rather than matching something unintended.

The effective exclusion set is reported by the ingest, because an index that
silently omitted files would be indistinguishable from one that failed to find
them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

IGNORE_FILENAME = ".dkgignore"

# A rules file is small. The cap stops a mistaken or hostile file from becoming
# an unbounded read during ingest.
MAX_IGNORE_BYTES = 1_000_000
MAX_PATTERNS = 10000


@dataclass(frozen=True)
class Rule:
    """One compiled pattern and what it does."""

    pattern: str
    regex: re.Pattern[str]
    negated: bool
    directory_only: bool
    line: int


def _compile(pattern: str) -> tuple[re.Pattern[str], bool]:
    """Translate one glob to a regex, honouring ** across separators.

    Returns the regex and whether the pattern was anchored at the root. Built by
    hand rather than with fnmatch because fnmatch's ``*`` crosses separators,
    which would make ``vendor/*`` match ``vendor/a/b/c`` and silently ignore far
    more than the pattern says.
    """
    anchored = "/" in pattern.rstrip("/")
    out = ["^"]
    i = 0
    n = len(pattern)
    while i < n:
        ch = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
            continue
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        if ch == "*":
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(ch))
        i += 1
    out.append("$")
    return re.compile("".join(out)), anchored


def parse_rules(text: str) -> list[Rule]:
    """Compile the lines of an ignore file into ordered rules."""
    rules: list[Rule] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if len(rules) >= MAX_PATTERNS:
            break
        negated = line.startswith("!")
        if negated:
            line = line[1:].strip()
        if not line:
            continue
        directory_only = line.endswith("/")
        body = line.rstrip("/")
        if not body:
            continue
        regex, anchored = _compile(body)
        if not anchored:
            # A pattern with no slash matches the basename at any depth, which
            # is what makes "*.min.js" behave as everyone expects.
            regex, _ = _compile(f"**/{body}")
            basename_regex = re.compile(f"^(?:.*/)?{regex.pattern[1:]}")
            regex = basename_regex
        rules.append(
            Rule(
                pattern=raw.strip(),
                regex=regex,
                negated=negated,
                directory_only=directory_only,
                line=number,
            )
        )
    return rules


class IgnoreRules:
    """The compiled contents of one ignore file, and which paths it excluded."""

    def __init__(self, rules: list[Rule], *, source: str = "", error: str = "") -> None:
        self.rules = rules
        self.source = source
        self.error = error
        self._excluded: dict[str, str] = {}

    @property
    def present(self) -> bool:
        return bool(self.source) and not self.error

    def match(self, rel_path: str) -> Rule | None:
        """The LAST rule that decides this path, or None if none does.

        Last rather than first, because that is what makes a negation able to
        re-include something an earlier broad pattern swept up.
        """
        candidate = rel_path.replace("\\", "/").lstrip("./")
        decision: Rule | None = None
        for rule in self.rules:
            if rule.directory_only:
                # A directory rule matches the directory itself and everything
                # beneath it.
                if not (
                    rule.regex.match(candidate)
                    or any(
                        rule.regex.match(candidate.rsplit("/", k)[0])
                        for k in range(1, candidate.count("/") + 1)
                    )
                ):
                    continue
            elif not rule.regex.match(candidate):
                continue
            decision = rule
        return decision

    def excludes(self, rel_path: str) -> bool:
        """Whether this path stays out of the graph, recording why if it does."""
        rule = self.match(rel_path)
        if rule is None or rule.negated:
            return False
        self._excluded[rel_path] = rule.pattern
        return True

    def filter(self, paths: list[str]) -> tuple[list[str], list[str]]:
        """Split paths into kept and excluded, in the order they arrived."""
        kept: list[str] = []
        dropped: list[str] = []
        for path in paths:
            (dropped if self.excludes(path) else kept).append(path)
        return kept, dropped

    def report(self, *, max_listed: int = 200) -> dict:
        """The effective exclusion set, for the ingest result.

        The list is capped and the cap is reported, because an ignore file that
        excluded ten thousand paths should not put ten thousand strings into
        every ingest result.
        """
        excluded = dict(sorted(self._excluded.items()))
        listed = list(excluded)[:max_listed]
        by_pattern: dict[str, int] = {}
        for pattern in excluded.values():
            by_pattern[pattern] = by_pattern.get(pattern, 0) + 1
        return {
            "source": self.source or None,
            "error": self.error or None,
            "patterns": [r.pattern for r in self.rules],
            "pattern_count": len(self.rules),
            "excluded_count": len(excluded),
            "excluded": listed,
            "excluded_truncated": len(excluded) > len(listed),
            "excluded_by_pattern": dict(sorted(by_pattern.items())),
        }


def load_ignore_rules(repo: str | Path, *, filename: str = IGNORE_FILENAME) -> IgnoreRules:
    """Read ``.dkgignore`` from a repository root.

    An absent file yields empty rules, which is not an error: most repositories
    have none.
    """
    path = Path(repo) / filename
    if not path.is_file():
        return IgnoreRules([])
    try:
        raw = path.read_bytes()
    except OSError as e:
        return IgnoreRules([], source=filename, error=f"unreadable: {e}")
    if len(raw) > MAX_IGNORE_BYTES:
        return IgnoreRules(
            [], source=filename, error=f"larger than the {MAX_IGNORE_BYTES} byte cap"
        )
    return IgnoreRules(parse_rules(raw.decode("utf-8", errors="replace")), source=filename)

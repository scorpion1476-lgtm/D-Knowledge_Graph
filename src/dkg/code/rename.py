"""Preview and apply a symbol rename.

Two capabilities that deliberately do not sit at the same trust level.

``preview_rename`` is READ-ONLY. It reports every occurrence of the old name in
the ingested files, split into the ones a rename would change, the ones that need
a human first, and the ones that are text rather than code. Nothing is silently
included: an occurrence reaches the applicable list only when the graph
attributes it, the name is unique in the graph, and the language's lexical rules
say it is code rather than a comment or a string.

``apply_rename`` WRITES. It is command-line only and is never registered on the
MCP surface. That is a deliberate divergence: the MCP surface is the boundary
against an agent acting on injected content, and a rename tool behind it is an
arbitrary source edit driven by whatever text the agent just read. It refuses
without an explicit confirmation, and its default is a dry run that prints the
unified diff it would write.

Reads are confined to the repository root and capped per file and by file count.
No network.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path

from ..core.db import Database
from ..core.errors import ValidationError
from .analysis import DEFAULT_MAX_NODES, CodeGraphView, load_code_graph
from .model import TIER_INFERRED_MIN, confidence_tier

# A rename is an identifier substitution, so an occurrence only counts when it is
# a whole identifier. Word boundaries alone would match inside ``a.old_name`` in
# a way we want (attribute access is a real reference) but also inside
# ``old_name2``, which this excludes.
_IDENT_TAIL = r"[A-Za-z0-9_]"

# Per-file and whole-run bounds. A preview must not become an unbounded read of
# a repository just because someone asked about a common name.
DEFAULT_MAX_FILE_BYTES = 1_000_000
DEFAULT_MAX_FILES = 5000

# Occurrence classifications.
KIND_DEFINITION = "definition"
KIND_REFERENCE = "reference"

# Why an occurrence is not applicable.
REASON_NAME_NOT_UNIQUE = "the old name matches more than one definition in the graph"
REASON_NO_EDGE = "no reference edge in the graph attributes this file to the target"
REASON_AMBIGUOUS_EDGE = "the only edge attributing this occurrence is ambiguous-tier"
REASON_UNKNOWN_LANGUAGE = "no lexical profile for this language, so code and commentary cannot be told apart"


@dataclass(frozen=True)
class LexicalProfile:
    """How one language family writes comments and strings.

    Only what is needed to tell code from commentary. Keyed per language rather
    than defaulted, because guessing wrong is worse than not knowing: treating
    Python's ``//`` floor division as a comment would silently drop real code
    from a rename.
    """

    line_comments: tuple[str, ...] = ()
    block_comment: tuple[str, str] | None = None
    quotes: tuple[str, ...] = ('"', "'")
    triple_quotes: bool = False


_HASH = LexicalProfile(line_comments=("#",))
_C_STYLE = LexicalProfile(line_comments=("//",), block_comment=("/*", "*/"))

LEXICAL_PROFILES: dict[str, LexicalProfile] = {
    "python": LexicalProfile(line_comments=("#",), triple_quotes=True),
    "ruby": _HASH,
    "shell": _HASH,
    "bash": _HASH,
    "perl": _HASH,
    "r": _HASH,
    "elixir": _HASH,
    "toml": _HASH,
    "yaml": _HASH,
    "javascript": LexicalProfile(
        line_comments=("//",), block_comment=("/*", "*/"), quotes=('"', "'", "`")
    ),
    "typescript": LexicalProfile(
        line_comments=("//",), block_comment=("/*", "*/"), quotes=('"', "'", "`")
    ),
    "tsx": LexicalProfile(
        line_comments=("//",), block_comment=("/*", "*/"), quotes=('"', "'", "`")
    ),
    "go": _C_STYLE,
    "java": _C_STYLE,
    "rust": _C_STYLE,
    "c": _C_STYLE,
    "cpp": _C_STYLE,
    "csharp": _C_STYLE,
    "kotlin": _C_STYLE,
    "swift": _C_STYLE,
    "scala": _C_STYLE,
    "dart": _C_STYLE,
    "php": LexicalProfile(line_comments=("//", "#"), block_comment=("/*", "*/")),
}


def mask_non_code(text: str, language: str) -> str | None:
    """Return ``text`` with comment and string content replaced by spaces.

    The result is the same length as the input, so an offset into the mask is an
    offset into the original. Returns None when the language has no profile,
    which the caller reports rather than guessing: an unmasked comment would
    otherwise be offered as an applicable edit.

    The scanner is deliberately small. It tracks one state at a time (code,
    inside a quote, inside a line comment, inside a block comment), honours
    backslash escapes inside quotes, and understands Python's triple quotes. It
    does not model raw strings, here-documents, or nested block comments; an
    occurrence those would hide is reported as code, which is the direction that
    asks a human rather than the direction that edits silently.
    """
    profile = LEXICAL_PROFILES.get(language)
    if profile is None:
        return None
    out = list(text)
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        # Block comment.
        if profile.block_comment and text.startswith(profile.block_comment[0], i):
            end = text.find(profile.block_comment[1], i + len(profile.block_comment[0]))
            stop = n if end == -1 else end + len(profile.block_comment[1])
            _blank(out, text, i, stop)
            i = stop
            continue
        # Line comment.
        marker = next((m for m in profile.line_comments if text.startswith(m, i)), None)
        if marker is not None:
            end = text.find("\n", i)
            stop = n if end == -1 else end
            _blank(out, text, i, stop)
            i = stop
            continue
        # Triple-quoted string (Python), checked before the single quote so the
        # opening delimiter is not mistaken for an empty string.
        if profile.triple_quotes and (text.startswith('"""', i) or text.startswith("'''", i)):
            delim = text[i : i + 3]
            end = text.find(delim, i + 3)
            stop = n if end == -1 else end + 3
            _blank(out, text, i, stop)
            i = stop
            continue
        if ch in profile.quotes:
            i = _skip_quoted(text, out, i, ch)
            continue
        i += 1
    return "".join(out)


def _blank(out: list[str], text: str, start: int, stop: int) -> None:
    """Replace a span with spaces, keeping newlines so line numbers survive."""
    for j in range(start, stop):
        out[j] = "\n" if text[j] == "\n" else " "


def _skip_quoted(text: str, out: list[str], start: int, quote: str) -> int:
    """Blank a quoted run and return the offset just past its closing quote."""
    n = len(text)
    j = start + 1
    while j < n:
        if text[j] == "\\":
            j += 2
            continue
        if text[j] == quote:
            j += 1
            break
        if text[j] == "\n":
            # An unterminated single-line string. Stop at the newline rather
            # than blanking the rest of the file.
            break
        j += 1
    stop = min(j, n)
    _blank(out, text, start, stop)
    return stop


def _resolve_target(view: CodeGraphView, symbol: str) -> tuple[str, list[str]]:
    """Resolve a canonical name or a short name to one node id.

    Returns (node_id, alternatives). When a short name matches several
    definitions the alternatives are returned and the caller reports them
    instead of picking one.
    """
    for nid in view.node_ids():
        if view.nodes[nid].canonical == symbol:
            return nid, []
    matches = [nid for nid in view.node_ids() if view.nodes[nid].display == symbol]
    if len(matches) == 1:
        return matches[0], []
    return "", [view.nodes[m].canonical for m in matches]


def preview_rename(
    db: Database,
    symbol: str,
    new_name: str,
    *,
    repo_root: str | Path,
    tenant_id: str = "local",
    max_nodes: int = DEFAULT_MAX_NODES,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_files: int = DEFAULT_MAX_FILES,
) -> dict:
    """Report every occurrence a rename would touch. Reads only.

    ``symbol`` is a canonical name (``path::Class.method``) or a short name that
    is unique in the graph. ``repo_root`` confines every file read.
    """
    validate_identifier(new_name)
    root = Path(repo_root).resolve()
    view = load_code_graph(db, tenant_id=tenant_id, max_nodes=max_nodes)
    target_id, alternatives = _resolve_target(view, symbol)
    if not target_id:
        return {
            "target": None,
            "new_name": new_name,
            "applicable": [],
            "ambiguous": [],
            "commentary": [],
            "resolved": False,
            "alternatives": sorted(alternatives),
            "why": {
                "unresolved": (
                    f"{symbol!r} matched {len(alternatives)} definitions; pass a "
                    "canonical name to choose one"
                    if alternatives
                    else f"{symbol!r} is not a code entity in this graph"
                )
            },
        }

    target = view.nodes[target_id]
    old_name = target.display
    if old_name == new_name:
        raise ValidationError("the new name is the same as the old name")

    # Is the short name unique across definitions? If it is not, no textual
    # occurrence can be attributed to this definition with confidence.
    same_name = [
        nid for nid in view.node_ids() if view.nodes[nid].display == old_name and nid != target_id
    ]
    name_unique = not same_name

    # Files the graph attributes to the target: the definition's own file, and
    # the file of every symbol with a reference edge INTO the target.
    inbound = view.in_adjacency()
    attributions: dict[str, float] = {}
    for referrer_id in inbound.get(target_id, ()):  # subject ids
        path = view.path_of(referrer_id)
        if not path:
            continue
        weight = max(
            (e.weight for e in view.edges if e.subject_id == referrer_id and e.object_id == target_id),
            default=0.0,
        )
        attributions[path] = max(attributions.get(path, 0.0), weight)
    attributions.setdefault(target.path, 1.0)

    pattern = re.compile(rf"(?<!{_IDENT_TAIL}){re.escape(old_name)}(?!{_IDENT_TAIL})")

    files = sorted({view.nodes[n].path for n in view.node_ids() if view.nodes[n].path})
    files_truncated = len(files) > max_files
    files = files[:max_files]

    applicable: list[dict] = []
    ambiguous: list[dict] = []
    commentary: list[dict] = []
    unreadable: list[dict] = []
    languages = {view.nodes[n].path: view.nodes[n].language for n in view.node_ids()}

    for rel in files:
        full = _confined(root, rel)
        if full is None:
            unreadable.append({"path": rel, "reason": "path escapes the repository root"})
            continue
        # The cap is checked from the file's SIZE, before any of it is read. A
        # cap applied after read_bytes() still pulls the whole file into memory
        # first, which is most of what the cap exists to prevent.
        try:
            size = full.stat().st_size
        except OSError as e:
            unreadable.append({"path": rel, "reason": f"unreadable: {e}"})
            continue
        if size > max_file_bytes:
            unreadable.append(
                {"path": rel, "reason": f"larger than the {max_file_bytes} byte cap"}
            )
            continue
        try:
            raw = full.read_bytes()
        except OSError as e:
            unreadable.append({"path": rel, "reason": f"unreadable: {e}"})
            continue
        text = raw.decode("utf-8", errors="replace")
        if not pattern.search(text):
            continue
        language = languages.get(rel, "")
        masked = mask_non_code(text, language)
        line_starts = _line_starts(text)
        for match in pattern.finditer(text):
            line_no, column = _position(line_starts, match.start())
            record = {
                "path": rel,
                "line": line_no,
                "column": column,
                "text": _line_text(text, line_starts, line_no),
                "kind": KIND_DEFINITION
                if rel == target.path and line_no == target.start_line
                else KIND_REFERENCE,
            }
            if masked is None:
                ambiguous.append({**record, "reason": REASON_UNKNOWN_LANGUAGE})
                continue
            if masked[match.start() : match.end()].strip() != old_name:
                commentary.append({**record, "reason": "inside a comment or a string literal"})
                continue
            if not name_unique:
                ambiguous.append({**record, "reason": REASON_NAME_NOT_UNIQUE})
                continue
            attributed: float | None = attributions.get(rel)
            if attributed is None:
                ambiguous.append({**record, "reason": REASON_NO_EDGE})
                continue
            weight = attributed
            if weight < TIER_INFERRED_MIN:
                ambiguous.append(
                    {**record, "reason": REASON_AMBIGUOUS_EDGE, "edge_tier": confidence_tier(weight)}
                )
                continue
            applicable.append({**record, "edge_tier": confidence_tier(weight)})

    key = lambda o: (o["path"], o["line"], o["column"])  # noqa: E731
    applicable.sort(key=key)
    ambiguous.sort(key=key)
    commentary.sort(key=key)

    return {
        "target": {
            "canonical": target.canonical,
            "display": old_name,
            "kind": target.kind,
            "path": target.path,
            "start_line": target.start_line,
        },
        "old_name": old_name,
        "new_name": new_name,
        "resolved": True,
        "applicable": applicable,
        "ambiguous": ambiguous,
        "commentary": commentary,
        "unreadable": unreadable,
        "counts": {
            "applicable": len(applicable),
            "ambiguous": len(ambiguous),
            "commentary": len(commentary),
            "files_touched": len({o["path"] for o in applicable}),
            "files_scanned": len(files),
            "same_name_definitions": len(same_name),
        },
        "files_truncated": files_truncated,
        "graph_truncated": view.truncated,
        "why": {
            "read_only": "this preview never writes; applying is a separate command-line-only step",
            "applicable_rule": (
                "an occurrence is applicable only when all three hold: the old "
                "name matches exactly one definition in the graph, a reference "
                "edge at inferred tier or better attributes the file to the "
                "target, and the language's lexical rules put the occurrence in "
                "code rather than in a comment or string. Anything else is "
                "reported separately and is never applied."
            ),
            "matching": (
                "whole-identifier textual matching over the ingested files. It "
                "cannot see a reference built by string concatenation or "
                "reflection, and it does not resolve scope, so a local variable "
                "sharing the name in an attributed file is reported as applicable."
            ),
            "bounds": (
                f"files are read under {root} only, capped at {max_file_bytes} "
                f"bytes each and {max_files} files in total"
            ),
            "scope": (
                "only files present in the code graph are scanned; a file that "
                "was never ingested is not searched and is not reported"
            ),
        },
    }


def render_diff(preview: dict, *, repo_root: str | Path) -> str:
    """The unified diff applying this preview would produce. Writes nothing."""
    root = Path(repo_root).resolve()
    old_name = preview.get("old_name", "")
    new_name = preview.get("new_name", "")
    chunks: list[str] = []
    for rel, edits in _by_file(preview).items():
        full = _confined(root, rel)
        if full is None or not full.exists():
            continue
        before = full.read_text(encoding="utf-8", errors="replace")
        after = _rewrite(before, edits, old_name, new_name)
        if before == after:
            continue
        chunks.extend(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"a/{rel}",
                tofile=f"b/{rel}",
            )
        )
    return "".join(chunks)


def apply_rename(
    preview: dict,
    *,
    repo_root: str | Path,
    confirm: bool = False,
    dry_run: bool = True,
) -> dict:
    """Apply the applicable occurrences of a preview. Command line only.

    Refuses unless ``confirm`` is true AND ``dry_run`` is false. A dry run
    returns the diff and touches nothing, which is the default, so the only way
    to write is to ask for it twice.
    """
    root = Path(repo_root).resolve()
    if not preview.get("resolved"):
        raise ValidationError("cannot apply a preview whose target did not resolve")
    old_name = preview["old_name"]
    new_name = preview["new_name"]
    validate_identifier(new_name)
    diff = render_diff(preview, repo_root=root)

    if dry_run or not confirm:
        return {
            "applied": False,
            "dry_run": True,
            "files_changed": 0,
            "occurrences": preview["counts"]["applicable"],
            "diff": diff,
            "why": (
                "nothing was written. Applying requires confirm=true AND "
                "dry_run=false, because a rename is an irreversible edit to "
                "source the caller may not have read."
            ),
        }

    changed = 0
    written: list[str] = []
    for rel, edits in _by_file(preview).items():
        full = _confined(root, rel)
        if full is None or not full.exists():
            continue
        before = full.read_text(encoding="utf-8")
        after = _rewrite(before, edits, old_name, new_name)
        if before == after:
            continue
        full.write_text(after, encoding="utf-8")
        changed += 1
        written.append(rel)
    return {
        "applied": True,
        "dry_run": False,
        "files_changed": changed,
        "files": sorted(written),
        "occurrences": preview["counts"]["applicable"],
        "diff": diff,
        "why": (
            "only the applicable occurrences were rewritten. Ambiguous "
            "occurrences and occurrences in comments or strings were left alone "
            "and are listed in the preview; the graph is now stale until the "
            "repository is re-ingested."
        ),
    }


def validate_identifier(name: str) -> str:
    """Reject a new name that is not a plain identifier."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name or ""):
        raise ValidationError(
            f"new name {name!r} is not a plain identifier; a rename writes source"
        )
    return name


def _by_file(preview: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for occurrence in preview.get("applicable", []):
        out.setdefault(occurrence["path"], []).append(occurrence)
    return {k: sorted(v, key=lambda o: (o["line"], o["column"])) for k, v in sorted(out.items())}


def _rewrite(text: str, edits: list[dict], old_name: str, new_name: str) -> str:
    """Replace exactly the named (line, column) occurrences, right to left.

    Right to left so an earlier replacement cannot move a later offset, and by
    exact position so a second occurrence on the same line that the preview
    classified differently is not swept up with the first.
    """
    line_starts = _line_starts(text)
    positions: list[int] = []
    for edit in edits:
        line = int(edit["line"])
        if line < 1 or line > len(line_starts):
            continue
        offset = line_starts[line - 1] + int(edit["column"]) - 1
        if text[offset : offset + len(old_name)] == old_name:
            positions.append(offset)
    out = text
    for offset in sorted(positions, reverse=True):
        out = out[:offset] + new_name + out[offset + len(old_name) :]
    return out


def _confined(root: Path, rel: str) -> Path | None:
    """Resolve a repository-relative path under the root, or None if it escapes."""
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _line_starts(text: str) -> list[int]:
    starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            starts.append(i + 1)
    return starts


def _position(line_starts: list[int], offset: int) -> tuple[int, int]:
    """1-based (line, column) for a character offset."""
    lo, hi = 0, len(line_starts) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if line_starts[mid] <= offset:
            lo = mid
        else:
            hi = mid - 1
    return lo + 1, offset - line_starts[lo] + 1


def _line_text(text: str, line_starts: list[int], line_no: int) -> str:
    start = line_starts[line_no - 1]
    end = text.find("\n", start)
    return text[start : end if end != -1 else len(text)].strip()

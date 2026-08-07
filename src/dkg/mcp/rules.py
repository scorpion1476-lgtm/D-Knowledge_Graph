"""Inject graph-aware guidance into an AI coding tool's rules file.

The file being edited is usually one a human wrote and keeps editing, so the
only acceptable edit is a surgical one. That is what the markers are for:

*   The managed region is exactly the bytes from the ``BEGIN`` marker line to
    the newline that ends the ``END`` marker line. Re-injection replaces that
    slice and nothing else, so text a user typed immediately above the ``BEGIN``
    line or immediately below the ``END`` line survives byte for byte.
*   Injecting the same guidance twice produces a byte-identical file, because
    the block is rendered from constants and the slice it replaces is exactly
    the slice it produced last time.
*   Removal deletes exactly that slice. When the remaining text is empty and
    the file carries nothing but our block, the file itself is removed, so an
    install-then-uninstall round trip leaves the tree as it was found.

The one documented departure from byte-exact symmetry: appending to a file
whose last line has no terminating newline adds that newline, because a block
that started mid-line would not be a line-anchored region and could not be
found again reliably. A file that ends with a newline (the normal case) round
trips exactly.

Nothing here reads ``Path.home()``, ``expanduser``, or the environment. Every
path comes from a caller-supplied config root, exactly as in ``configure``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..core.errors import ValidationError
from .configure import OWNER_MARKER, SERVER_NAME, atomic_write

#: Opening delimiter of the managed region. An HTML comment, because every
#: rules and instructions file this project writes to is markdown, and an HTML
#: comment is inert there.
BEGIN_MARKER = f"<!-- BEGIN {OWNER_MARKER} -->"

#: Closing delimiter. Paired with :data:`BEGIN_MARKER`.
END_MARKER = f"<!-- END {OWNER_MARKER} -->"

#: Rendered guidance. Deliberately a module constant rather than a template
#: with a timestamp or a machine name: a re-injection has to be byte-identical
#: to the previous one, and anything varying per run would break that.
_GUIDANCE_LINES: tuple[str, ...] = (
    "## D-Knowledge Graph is available in this workspace",
    "",
    "A local knowledge graph over documents, media, and source code is registered as the",
    f"MCP server `{SERVER_NAME}`. Prefer asking it before reading files one by one.",
    "",
    "What to reach for:",
    "",
    "- `dkg.search` and `dkg.code.search` to find where something lives.",
    "- `dkg.code.impact` for the blast radius of a change to a symbol or a file.",
    "- `dkg.code.flow` to follow the call chain forward from an entry symbol.",
    "- `dkg.graph.neighbourhood` and `dkg.evidence.claim` to read a neighbourhood",
    "  and the evidence behind a claim.",
    "- `dkg.code.architecture`, `dkg.code.hubs`, and `dkg.code.gaps` for orientation.",
    "",
    "Bounds you must respect when you quote it:",
    "",
    "- The server is read-only. It never writes to the graph and has no write tool.",
    "- It runs offline. There is no network call and no telemetry.",
    "- Code edges are structural and over-approximate, so impact and flow results",
    "  over-flag rather than under-flag. Treat them as advisory and verify before",
    "  acting on them.",
    "- Results are bounded by node caps and report when they were truncated.",
    "- Community numbers are per-run labels. Never compare them across runs.",
    "",
    "Rebuild or refresh the graph with `dkg code-ingest` for a full pass and",
    "`dkg update` for the incremental one.",
)


def guidance_block() -> str:
    """Return the managed region, marker lines included, newline terminated."""
    body = "\n".join(_GUIDANCE_LINES)
    return f"{BEGIN_MARKER}\n\n{body}\n\n{END_MARKER}\n"


def _span(text: str) -> tuple[int, int] | None:
    """Return the half-open byte span of the managed region, or ``None``.

    A file holding a begin marker with no end marker is a file somebody edited
    by hand into a state we cannot repair safely, so it is refused rather than
    guessed at.
    """
    begin = text.find(BEGIN_MARKER)
    if begin < 0:
        if END_MARKER in text:
            raise ValidationError(
                f"found {END_MARKER!r} with no matching {BEGIN_MARKER!r}; "
                "refusing to edit a half-open managed block"
            )
        return None
    end = text.find(END_MARKER, begin)
    if end < 0:
        raise ValidationError(
            f"found {BEGIN_MARKER!r} with no matching {END_MARKER!r}; "
            "refusing to edit a half-open managed block"
        )
    if text.find(BEGIN_MARKER, begin + len(BEGIN_MARKER)) >= 0:
        raise ValidationError(
            f"found more than one {BEGIN_MARKER!r} block; refusing to guess which one is ours"
        )
    stop = end + len(END_MARKER)
    if stop < len(text) and text[stop] == "\n":
        stop += 1
    return begin, stop


def inject_text(existing: str, block: str) -> str:
    """Return ``existing`` with ``block`` written into the managed region."""
    span = _span(existing)
    if span is not None:
        start, stop = span
        return existing[:start] + block + existing[stop:]
    if existing == "":
        return block
    head = existing if existing.endswith("\n") else existing + "\n"
    return head + block


def strip_text(existing: str) -> tuple[str, bool]:
    """Return ``(text without the managed region, whether one was found)``."""
    span = _span(existing)
    if span is None:
        return existing, False
    start, stop = span
    return existing[:start] + existing[stop:], True


@dataclass(frozen=True)
class RulesResult:
    """What one rules file operation did or would do."""

    path: str
    existed: bool
    changed: bool
    written: bool
    removed_file: bool
    reason: str

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "existed": self.existed,
            "changed": self.changed,
            "written": self.written,
            "removed_file": self.removed_file,
            "reason": self.reason,
        }


def install_rules(path: Path, *, dry_run: bool = False) -> RulesResult:
    """Write the managed guidance block into ``path``.

    ``path`` is computed by the caller from its config root; nothing is derived
    from the environment here.
    """
    existed = path.exists()
    existing = path.read_text(encoding="utf-8") if existed else ""
    updated = inject_text(existing, guidance_block())
    changed = updated != existing
    if not changed:
        return RulesResult(str(path), existed, False, False, False, "guidance already current")
    if dry_run:
        return RulesResult(str(path), existed, True, False, False, "dry run; guidance would be written")
    atomic_write(path, updated)
    reason = "guidance updated" if existed else "rules file created with the guidance block"
    return RulesResult(str(path), existed, True, True, False, reason)


def uninstall_rules(path: Path, *, dry_run: bool = False) -> RulesResult:
    """Remove the managed guidance block from ``path``, and nothing else.

    A file with no block of ours is left alone and reported as such: this
    refuses to touch a rules file this project did not write into, which is the
    same rule the server-entry uninstall applies to an unmarked entry.
    """
    if not path.exists():
        return RulesResult(str(path), False, False, False, False, f"no rules file at {path}")
    existing = path.read_text(encoding="utf-8")
    updated, found = strip_text(existing)
    if not found:
        return RulesResult(
            str(path),
            True,
            False,
            False,
            False,
            f"{path} carries no {BEGIN_MARKER} block; refusing to change a file this project did not write into",
        )
    removes_file = updated.strip() == ""
    if dry_run:
        reason = "dry run; file would be removed" if removes_file else "dry run; block would be removed"
        return RulesResult(str(path), True, True, False, removes_file, reason)
    if removes_file:
        path.unlink()
        return RulesResult(str(path), True, True, True, True, "block removed; the file held nothing else and was deleted")
    atomic_write(path, updated)
    return RulesResult(str(path), True, True, True, False, "block removed")

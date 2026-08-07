"""Single-file component parsing for the source-code plane.

Vue, Svelte, and Astro put a component's markup, styling, and behaviour in one
file. The behaviour is ordinary JavaScript or TypeScript inside a script block,
so this module lifts the script blocks out and hands them to the ordinary code
parser. That is deliberately preferred over a component-specific Tree-sitter
grammar: a component grammar parses the template syntax, not the JavaScript
semantics, so it would not give the functions, classes, imports, and call sites
the code graph is built from.

Extraction is a small tolerant scanner rather than a full HTML parser, because
the file is not HTML: an Astro file opens with a code fence, and a Vue or Svelte
file mixes a template with script and style blocks. The scanner is bounded, does
no network access, and never executes anything.

Line numbers are reported against the extracted script, not against the
component file, and the offset of each block is returned so a caller can map
back. Template expressions are not parsed and are reported as not extracted
rather than silently ignored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..core.errors import IngestError, UnsupportedFormatError

SFC_EXTENSIONS = {
    ".vue": "vue",
    ".svelte": "svelte",
    ".astro": "astro",
}

_SCRIPT_OPEN = re.compile(r"<script\b([^>]*)>", re.IGNORECASE)
_SCRIPT_CLOSE = re.compile(r"</script\s*>", re.IGNORECASE)
_LANG_ATTR = re.compile(r"""\blang\s*=\s*["']?([A-Za-z]+)""", re.IGNORECASE)
# An Astro component opens with a fenced frontmatter block of TypeScript.
_ASTRO_FENCE = "---"

_MAX_BYTES = 4 * 1024 * 1024


@dataclass
class ScriptBlock:
    """One script block lifted out of a single-file component."""

    language: str
    source: str
    # One-based line in the component file where this block's first line sits,
    # so a symbol found at script line N is at component line offset + N - 1.
    line_offset: int
    # setup, module, or frontmatter: which kind of block this was.
    kind: str


@dataclass
class ComponentCode:
    """The script side of a single-file component."""

    framework: str
    language: str
    source: str
    blocks: list[ScriptBlock] = field(default_factory=list)
    # Blocks skipped because their lang attribute names something with no code
    # parser (for example CoffeeScript), counted rather than guessed at.
    skipped_blocks: int = 0


def is_component(path: str | Path) -> bool:
    return Path(path).suffix.lower() in SFC_EXTENSIONS


def _script_language(attrs: str) -> str | None:
    m = _LANG_ATTR.search(attrs or "")
    if m is None:
        return "javascript"
    named = m.group(1).lower()
    return {
        "js": "javascript",
        "javascript": "javascript",
        "ts": "typescript",
        "typescript": "typescript",
        "tsx": "tsx",
        "jsx": "javascript",
    }.get(named)


def _block_kind(attrs: str) -> str:
    lowered = (attrs or "").lower()
    if "context=" in lowered and "module" in lowered:
        return "module"
    if "setup" in lowered:
        return "setup"
    return "script"


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def read_component(path: str | Path, text: str | None = None) -> ComponentCode:
    """Lift the script blocks out of a Vue, Svelte, or Astro component."""
    path = str(path)
    framework = SFC_EXTENSIONS.get(Path(path).suffix.lower())
    if framework is None:
        raise UnsupportedFormatError(f"{path} is not a single-file component")
    if text is None:
        raw = Path(path).read_bytes()
        if len(raw) > _MAX_BYTES:
            raise IngestError(f"component too large: {len(raw)} bytes")
        text = raw.decode("utf-8", "replace")

    blocks: list[ScriptBlock] = []
    skipped = 0
    if framework == "astro":
        block = _astro_frontmatter(text)
        if block is not None:
            blocks.append(block)
    for match in _SCRIPT_OPEN.finditer(text):
        attrs = match.group(1)
        close = _SCRIPT_CLOSE.search(text, match.end())
        if close is None:
            # An unterminated script block is a malformed component; take the
            # rest of the file rather than dropping the behaviour entirely.
            body = text[match.end():]
            end = len(text)
        else:
            body = text[match.end():close.start()]
            end = close.start()
        del end
        language = _script_language(attrs)
        if language is None:
            skipped += 1
            continue
        blocks.append(
            ScriptBlock(
                language=language,
                source=body,
                line_offset=_line_of(text, match.end()),
                kind=_block_kind(attrs),
            )
        )
    if not blocks:
        # A component with only a template and styles is valid and simply has no
        # symbols. That is an empty result, not an error.
        return ComponentCode(framework=framework, language="javascript", source="", blocks=[], skipped_blocks=skipped)
    # A component that mixes plain JavaScript and TypeScript blocks is parsed as
    # TypeScript, which is a superset, rather than parsing each block separately
    # and losing references that cross between them.
    language = "typescript" if any(b.language in ("typescript", "tsx") for b in blocks) else "javascript"
    source = "\n".join(b.source for b in blocks)
    return ComponentCode(
        framework=framework,
        language=language,
        source=source,
        blocks=blocks,
        skipped_blocks=skipped,
    )


def _astro_frontmatter(text: str) -> ScriptBlock | None:
    """The leading fenced code block of an Astro component, if present."""
    stripped = text.lstrip()
    if not stripped.startswith(_ASTRO_FENCE):
        return None
    lead = len(text) - len(stripped)
    open_end = text.find("\n", lead + len(_ASTRO_FENCE))
    if open_end == -1:
        return None
    close = text.find(f"\n{_ASTRO_FENCE}", open_end)
    if close == -1:
        return None
    body = text[open_end + 1 : close]
    return ScriptBlock(
        language="typescript",
        source=body,
        line_offset=_line_of(text, open_end + 1),
        kind="frontmatter",
    )

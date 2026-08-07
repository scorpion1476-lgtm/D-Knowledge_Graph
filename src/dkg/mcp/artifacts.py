"""Write a tool's native automation artifacts: its hooks and its command package.

Two kinds of artifact, one rule for both. Whatever this module writes carries
:data:`dkg.mcp.configure.OWNER_MARKER`, and whatever it removes has to carry
that marker first. A hook entry a human wrote, or a command file with one of
our names that we did not write, is refused loudly rather than replaced.

The command bodies are not composed here. They are the workflow documents
shipped in :mod:`dkg.skills`, parsed once and re-rendered into whichever format
the target tool documents (markdown with frontmatter, or the TOML prompt files
the Gemini-derived CLIs use). One source, several renderings, so a tool cannot
be shipped a command that says something different from the shipped skill
package.

Nothing here reads ``Path.home()``, ``expanduser``, or the environment. Every
path is built from the caller's config root.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from ..core.errors import ValidationError
from .configure import (
    MARKER_KEY,
    OWNER_MARKER,
    Launch,
    atomic_write,
    load_json_object,
    serialise,
)
from .platforms import Platform, resolve_relative

#: Marker line embedded in every text artifact this module writes. A comment in
#: markdown and in TOML alike, so it is inert in both and still greppable.
TEXT_MARKER = f"managed-by: {OWNER_MARKER}"

_FENCE = "---"

#: The frontmatter field that marks a shipped document as a workflow command.
#: ``dkg-usage.md`` has no frontmatter and is therefore never mistaken for one.
_WORKFLOW_KIND = "workflow-command"


@dataclass(frozen=True)
class WorkflowCommand:
    """One shipped workflow command, parsed from the skill package."""

    name: str
    title: str
    description: str
    cli: tuple[str, ...]
    mcp: tuple[str, ...]
    bounds: str
    body: str


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split leading ``---`` delimited ``key: value`` frontmatter from a body.

    A deliberately small parser rather than a YAML dependency: the shipped
    documents are written to this shape, and adding a runtime dependency to
    read files this project itself authors would be a poor trade.
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != _FENCE:
        return {}, text
    fields: dict[str, str] = {}
    for index in range(1, len(lines)):
        stripped = lines[index].strip()
        if stripped == _FENCE:
            return fields, "".join(lines[index + 1 :]).lstrip("\n")
        if not stripped or stripped.startswith("#"):
            continue
        key, sep, value = stripped.partition(":")
        if not sep:
            raise ValidationError(f"frontmatter line is not 'key: value': {stripped!r}")
        fields[key.strip()] = value.strip()
    raise ValidationError("frontmatter opened with '---' but was never closed")


def _split_list(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _skill_files() -> list[str]:
    return sorted(
        entry.name
        for entry in resources.files("dkg.skills").iterdir()
        if entry.name.endswith(".md")
    )


def _read_skill(name: str) -> str:
    return resources.files("dkg.skills").joinpath(name).read_text(encoding="utf-8")


def workflow_commands() -> tuple[WorkflowCommand, ...]:
    """Every shipped workflow command, sorted by name.

    Discovery is by frontmatter, not by filename convention, so a document
    added to the skill package is either explicitly a workflow command or is
    not one at all.
    """
    found: list[WorkflowCommand] = []
    for filename in _skill_files():
        fields, body = _parse_frontmatter(_read_skill(filename))
        if fields.get("kind") != _WORKFLOW_KIND:
            continue
        missing = sorted({"name", "title", "description", "cli", "mcp", "bounds"} - set(fields))
        if missing:
            raise ValidationError(f"{filename} is a workflow command but is missing: {', '.join(missing)}")
        found.append(
            WorkflowCommand(
                name=fields["name"],
                title=fields["title"],
                description=fields["description"],
                cli=_split_list(fields["cli"]),
                mcp=_split_list(fields["mcp"]),
                bounds=fields["bounds"],
                body=body,
            )
        )
    if not found:
        raise ValidationError("the shipped skill package contains no workflow commands")
    return tuple(sorted(found, key=lambda c: c.name))


def usage_skill() -> tuple[str, str]:
    """Return ``(description, body)`` for the shipped usage document."""
    text = _read_skill("dkg-usage.md")
    return (
        "How to drive the local D-Knowledge Graph: ingest, search, read the graph, "
        "and run the read-only MCP server.",
        text,
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _toml_basic(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _toml_multiline(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    return f'"""\n{escaped}"""'


def render_command(command: WorkflowCommand, fmt: str) -> str:
    """Render one workflow command in the target tool's documented format."""
    driven = (
        f"Drives CLI: {', '.join('dkg ' + name for name in command.cli)}.\n"
        f"Drives MCP tools: {', '.join(command.mcp)}.\n"
        f"Bounds: {command.bounds}.\n"
    )
    if fmt == "markdown":
        return (
            f"{_FENCE}\n"
            f"description: {command.description}\n"
            f"{_FENCE}\n"
            f"<!-- {TEXT_MARKER} -->\n\n"
            f"{driven}\n"
            f"{command.body}"
        )
    if fmt == "markdown-plain":
        # No frontmatter: the tools using this format read the whole file as the
        # prompt, so a frontmatter block would end up in the prompt text.
        return f"<!-- {TEXT_MARKER} -->\n\n# {command.title}\n\n{driven}\n{command.body}"
    if fmt == "toml":
        prompt = f"{driven}\n{command.body}"
        return (
            f"# {TEXT_MARKER}\n"
            f"description = {_toml_basic(command.description)}\n"
            f"prompt = {_toml_multiline(prompt)}\n"
        )
    raise ValidationError(f"unknown command format {fmt!r}")


def render_skill(description: str, body: str) -> str:
    return (
        f"{_FENCE}\n"
        f"name: d-knowledge-graph\n"
        f"description: {description}\n"
        f"{_FENCE}\n"
        f"<!-- {TEXT_MARKER} -->\n\n"
        f"{body}"
    )


# ---------------------------------------------------------------------------
# Command and skill packages
# ---------------------------------------------------------------------------


def planned_command_files(target: Platform, *, config_root: Path, platform_key: str) -> list[tuple[Path, str]]:
    """Every file the command package for ``target`` consists of, sorted by path."""
    if target.commands is None:
        return []
    spec = target.commands
    files: list[tuple[Path, str]] = []
    if spec.directory is not None:
        directory = config_root / resolve_relative(spec.directory, platform_key)
        files = [
            (directory / f"{command.name}{spec.extension}", render_command(command, spec.fmt))
            for command in workflow_commands()
        ]
    if spec.skill_directory is not None:
        description, body = usage_skill()
        files.append(
            (
                config_root / resolve_relative(spec.skill_directory, platform_key) / "SKILL.md",
                render_skill(description, body),
            )
        )
    return sorted(files, key=lambda pair: str(pair[0]))


def install_commands(
    target: Platform, *, config_root: Path, platform_key: str, dry_run: bool = False
) -> dict:
    """Write the command and skill package for one tool."""
    planned = planned_command_files(target, config_root=config_root, platform_key=platform_key)
    written: list[str] = []
    unchanged: list[str] = []
    refused: list[str] = []
    for path, text in planned:
        if path.exists():
            current = path.read_text(encoding="utf-8")
            if OWNER_MARKER not in current:
                refused.append(str(path))
                continue
            if current == text:
                unchanged.append(str(path))
                continue
        written.append(str(path))
        if not dry_run:
            atomic_write(path, text)
    if refused:
        raise ValidationError(
            "refusing to overwrite command files that do not carry the "
            f"{TEXT_MARKER!r} marker: {', '.join(sorted(refused))}"
        )
    return {
        "format": target.commands.fmt if target.commands else "",
        "planned": [str(path) for path, _ in planned],
        "written": sorted(written),
        "unchanged": sorted(unchanged),
        "dry_run": dry_run,
    }


def uninstall_commands(
    target: Platform, *, config_root: Path, platform_key: str, dry_run: bool = False
) -> dict:
    """Remove the command and skill package for one tool, and nothing else."""
    planned = planned_command_files(target, config_root=config_root, platform_key=platform_key)
    removed: list[str] = []
    absent: list[str] = []
    refused: list[str] = []
    for path, _text in planned:
        if not path.exists():
            absent.append(str(path))
            continue
        if OWNER_MARKER not in path.read_text(encoding="utf-8"):
            refused.append(str(path))
            continue
        removed.append(str(path))
        if not dry_run:
            path.unlink()
    if refused:
        raise ValidationError(
            "refusing to remove command files that do not carry the "
            f"{TEXT_MARKER!r} marker: {', '.join(sorted(refused))}"
        )
    return {
        "removed": sorted(removed),
        "absent": sorted(absent),
        "dry_run": dry_run,
    }


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


def hook_command(launch: Launch) -> str:
    """The shell command a hook runs.

    The incremental update path, pointed at the repository the hook fired in.
    No ``--home`` is passed: the hook runs inside the repository, so the
    project-local home is found the same way every other invocation finds it,
    and no absolute path from the installing machine is baked into a file that
    is frequently committed.
    """
    return shlex.join([launch.command, *launch.prefix_args, "update", "--repo", ".", "--quiet"])


def _hook_entry(spec, launch: Launch) -> dict:
    """Build one hook entry in the shape the target tool documents.

    Two shapes cover every tool this project writes hooks for. ``claude-settings``
    nests a list of handlers under a matcher; ``flat-list`` is one object per
    handler with the command inline. Both carry the ownership marker, which is
    what makes the removal side able to refuse an entry it did not write.
    """
    if spec.style == "claude-settings":
        return {
            MARKER_KEY: OWNER_MARKER,
            "hooks": [{MARKER_KEY: OWNER_MARKER, "command": hook_command(launch), "type": "command"}],
            "matcher": spec.matcher,
        }
    if spec.style == "flat-list":
        entry = {MARKER_KEY: OWNER_MARKER, spec.command_key: hook_command(launch)}
        entry.update(dict(spec.entry_extra))
        return entry
    raise ValidationError(f"unknown hook style {spec.style!r}")


def _is_ours(entry) -> bool:
    return isinstance(entry, dict) and entry.get(MARKER_KEY) == OWNER_MARKER


def _hook_document(target: Platform, launch: Launch, document: dict) -> dict:
    """Return ``document`` with our hook entries present exactly once."""
    spec = target.hooks
    if spec is None:
        # Reached only if a caller asks for hooks on a tool that declares none.
        # Raising beats writing a hook file the tool will never read.
        raise ValidationError(f"{target.name!r} declares no hook file, so no hook can be written")
    root = document.get(spec.root_key, {})
    if not isinstance(root, dict):
        raise ValidationError(
            f"the {spec.root_key!r} key is a {type(root).__name__}, expected an object; "
            "refusing to overwrite it"
        )
    updated_root = dict(root)
    for event in spec.events:
        existing = updated_root.get(event, [])
        if not isinstance(existing, list):
            raise ValidationError(
                f"the {spec.root_key}.{event} key is a {type(existing).__name__}, expected a list; "
                "refusing to overwrite it"
            )
        kept = [entry for entry in existing if not _is_ours(entry)]
        updated_root[event] = [*kept, _hook_entry(spec, launch)]
    updated = dict(document)
    updated[spec.root_key] = updated_root
    # Keys the tool requires at the top level of its hook file, for example a
    # schema version. Written only when absent, so a user's own value stands.
    for key, value in spec.document_extra:
        updated.setdefault(key, value)
    return updated


def install_hooks(
    target: Platform,
    *,
    config_root: Path,
    platform_key: str,
    launch: Launch,
    dry_run: bool = False,
) -> dict:
    """Write this project's hook definitions into the tool's own hook file."""
    if target.hooks is None:
        raise ValidationError(f"{target.name} declares no hook support")
    path = config_root / resolve_relative(target.hooks.file, platform_key)
    document, exists = load_json_object(path)
    updated = _hook_document(target, launch, document)
    changed = serialise(updated) != serialise(document) if exists else True
    result = {
        "path": str(path),
        "file_exists": exists,
        "events": sorted(target.hooks.events),
        "command": hook_command(launch),
        "changed": changed,
        "written": False,
        "dry_run": dry_run,
        "preserved_keys": sorted(k for k in document if k != target.hooks.root_key),
    }
    if changed and not dry_run:
        atomic_write(path, serialise(updated))
        result["written"] = True
    return result


def uninstall_hooks(
    target: Platform, *, config_root: Path, platform_key: str, dry_run: bool = False
) -> dict:
    """Remove only the hook entries carrying this project's marker.

    An event list that becomes empty loses its key, and a hook file that ends
    up holding nothing at all is deleted, so an install-then-uninstall round
    trip leaves the tree as it was found. Any entry without the marker is left
    exactly where it was.
    """
    if target.hooks is None:
        raise ValidationError(f"{target.name} declares no hook support")
    spec = target.hooks
    path = config_root / resolve_relative(spec.file, platform_key)
    document, exists = load_json_object(path)
    result = {
        "path": str(path),
        "file_exists": exists,
        "removed": 0,
        "written": False,
        "removed_file": False,
        "dry_run": dry_run,
        "reason": "",
    }
    if not exists:
        result["reason"] = f"no hook file at {path}; nothing to remove"
        return result
    root = document.get(spec.root_key, {})
    if not isinstance(root, dict):
        raise ValidationError(
            f"the {spec.root_key!r} key in {path} is a {type(root).__name__}, expected an object"
        )

    removed = 0
    updated_root: dict = {}
    for event, entries in root.items():
        if not isinstance(entries, list):
            updated_root[event] = entries
            continue
        kept = [entry for entry in entries if not _is_ours(entry)]
        removed += len(entries) - len(kept)
        if kept or event not in spec.events:
            updated_root[event] = kept
    updated = dict(document)
    if updated_root:
        updated[spec.root_key] = updated_root
    else:
        updated.pop(spec.root_key, None)
        # A schema key we added on install goes back out with the entries it
        # was added for, but only if it still holds the value we wrote. A value
        # the user changed is theirs and stays.
        for key, value in spec.document_extra:
            if updated.get(key) == value:
                updated.pop(key, None)

    result["removed"] = removed
    result["preserved_keys"] = sorted(k for k in updated if k != spec.root_key)
    if removed == 0:
        result["reason"] = f"no hook entry in {path} carries the {MARKER_KEY} marker; nothing removed"
        return result
    removes_file = not updated
    result["removed_file"] = removes_file
    if dry_run:
        result["reason"] = "dry run; hook entries would be removed"
        return result
    if removes_file:
        path.unlink()
        result["written"] = True
        result["reason"] = "hook entries removed; the file held nothing else and was deleted"
        return result
    atomic_write(path, serialise(updated))
    result["written"] = True
    result["reason"] = "hook entries removed"
    return result

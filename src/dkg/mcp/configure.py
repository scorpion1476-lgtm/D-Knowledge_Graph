"""Write the D-Knowledge Graph MCP server entry into an AI coding tool's config.

The server this registers is the read-only stdio server (``dkg mcp-stdio``,
see ``server_stdio.py`` and ``tools.py``): its registry exposes query and read
tools only and never mutates the graph. That property is the selling point, so
the generated entry states it in a human-readable description rather than
leaving the consuming tool to guess.

Design choices worth recording:

*   Every tool is a declarative record in :mod:`dkg.mcp.platforms`. Supporting
    one more editor is then a data change, not a code change, and the install,
    plan, detect, and uninstall paths stay identical for all of them.
*   Every public function takes an explicit ``config_root``. Nothing in this
    module, in :mod:`dkg.mcp.platforms`, in :mod:`dkg.mcp.artifacts`, or in
    :mod:`dkg.mcp.rules` reads ``Path.home()``, ``expanduser``, or ``$HOME``; a
    caller that wants the real user configuration directory has to name it.
    That keeps these modules safe to exercise from tests against a temporary
    directory and makes an accidental write into a developer's own editor
    configuration impossible.
*   The entry is argv form (``command`` plus an ``args`` list), never a shell
    string, so no path or argument is ever re-parsed by a shell.
*   Every write is a temporary file in the same directory followed by
    ``os.replace``. ``os.replace`` is atomic on the same filesystem, so an
    interrupted run leaves the previous config intact rather than a truncated
    one. A parse failure raises before any temporary file is created, so a
    config we cannot understand is never clobbered.
*   Written JSON is ``indent=2, sort_keys=True`` with a trailing newline. The
    same inputs therefore produce byte-identical output, which makes installs
    idempotent and diffs reviewable. Unrelated keys and unrelated servers keep
    their values exactly; only the key formatting is normalised.
"""

from __future__ import annotations

import json
import os
import shutil
import sysconfig
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..core.config import default_home
from ..core.errors import ValidationError
from .platforms import Platform, platform_key_now, platforms, resolve_relative

#: Key under which we register ourselves in the consuming tool's server map.
SERVER_NAME = "d-knowledge-graph"

#: Key inside our server entry carrying the ownership marker.
MARKER_KEY = "_managedBy"

#: Value of :data:`MARKER_KEY`. ``uninstall`` removes an entry only when the
#: entry stored under :data:`SERVER_NAME` carries exactly this value, so an
#: entry a human wrote (or another installer wrote) under the same name is
#: refused rather than silently deleted. Every other artifact this project
#: writes (hook entries, command files, the injected rules block) carries the
#: same string, so one marker governs the whole install.
OWNER_MARKER = "d-knowledge-graph/mcp-configure"

#: Default command. The console script installed by this project (see
#: ``[project.scripts]`` in pyproject). It is left unresolved on purpose: a
#: resolved absolute path would depend on which environment happened to run the
#: installer, which is neither deterministic nor portable. A caller that needs
#: an absolute interpreter or script path passes ``command=``.
DEFAULT_COMMAND = "dkg"

#: Distribution name, used when an isolated runner has to be told what to run.
DISTRIBUTION = "d-knowledge-graph"

_DESCRIPTION = (
    "D-Knowledge Graph: local-first knowledge graph over documents, media, and source code. "
    "This MCP server is read-only; it registers query and read tools only, never a write tool, "
    "and never mutates the graph. Runs offline with no network access."
)


# ---------------------------------------------------------------------------
# Filesystem primitives, shared with artifacts.py and rules.py so there is one
# atomic write in this package rather than three.
# ---------------------------------------------------------------------------


def serialise(document: dict) -> str:
    """Render a JSON document in the one canonical form this package writes."""
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def atomic_write(path: Path, text: str) -> None:
    """Write via a temporary file in the same directory, then replace.

    Same directory means same filesystem, so ``os.replace`` is atomic: a reader
    sees either the old complete file or the new complete file, never a partial
    one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        # Leave no stray temporary file behind if the write or replace failed.
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def prune_empty_dirs(directories, *, root: Path, stop_dirs) -> list[str]:
    """Delete directories this project created that are now empty.

    Symmetry is the point: install creates ``.claude/skills/d-knowledge-graph``
    and friends, so an uninstall that removed the files but left the skeleton
    behind would not have restored the tree. Three guards keep that from
    turning into a directory-eating loop:

    *   nothing at or above ``root`` is touched,
    *   nothing whose real path is in ``stop_dirs`` is touched, which is how a
        tool's own configuration directory (the thing detection looks for)
        survives an uninstall, and
    *   a directory that still holds anything at all stops the walk.

    The documented edge case: an empty command directory that a user created
    before the install, inside a tool's configuration directory, is removed.
    """
    stops = {os.path.realpath(str(d)) for d in stop_dirs}
    stops.add(os.path.realpath(str(root)))
    removed: list[str] = []
    ordered = sorted({Path(d) for d in directories}, key=lambda p: len(p.parts), reverse=True)
    for start in ordered:
        current = start
        while True:
            if os.path.realpath(str(current)) in stops:
                break
            if Path(root) not in current.parents:
                break
            if not current.is_dir():
                break
            if any(current.iterdir()):
                break
            current.rmdir()
            removed.append(str(current))
            current = current.parent
    return sorted(removed)


def load_json_object(path: Path) -> tuple[dict, bool]:
    """Return ``(document, exists)`` for an existing or absent JSON object file.

    Refusing on anything we cannot understand is what guarantees a file we do
    not recognise is left byte-for-byte alone instead of being replaced by one
    holding only our entry.
    """
    if not path.exists():
        return {}, False
    raw = path.read_text(encoding="utf-8")
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValidationError(f"{path} is not valid JSON: {e}; refusing to overwrite it") from e
    if not isinstance(document, dict):
        raise ValidationError(f"{path} must contain a JSON object at the top level; refusing to overwrite it")
    return document, True


def _nested_get(document: dict, key: str) -> object:
    """Read a dotted key such as ``amp.mcpServers`` from a JSON document.

    Some tools namespace their settings with dots inside a flat object; others
    nest. Both are read here, flat first, because a flat key that literally
    contains a dot is what those tools actually write.
    """
    if key in document:
        return document[key]
    node: object = document
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _nested_path_exists(document: dict, key: str) -> bool:
    node: object = document
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return True


def _nested_set(document: dict, key: str, value: dict) -> dict:
    """Return a copy of ``document`` with ``key`` set, honouring a dotted key.

    Flat wins. A tool whose setting is literally named ``amp.mcpServers`` keeps
    that name as one key with a dot in it, which is what those tools document
    and what their own writers produce. The nested form is used only when the
    file already has it, so an existing file's own convention is preserved
    rather than duplicated alongside a flat key that means the same thing.
    """
    updated = dict(document)
    if key in document or "." not in key or not _nested_path_exists(document, key):
        updated[key] = value
        return updated
    parts = key.split(".")
    node = updated
    for part in parts[:-1]:
        child = node.get(part)
        node[part] = dict(child) if isinstance(child, dict) else {}
        node = node[part]
    node[parts[-1]] = value
    return updated


def server_map(document: dict, servers_key: str) -> dict:
    """Read a tool's server map out of a parsed configuration document.

    Public because a caller checking what was written should not have to know
    whether the key is flat, dotted, or nested; that is this package's problem.
    """
    servers = _nested_get(document, servers_key)
    return dict(servers) if isinstance(servers, dict) else {}


def _load_servers(path: Path, servers_key: str) -> tuple[dict, dict, bool]:
    """Return ``(document, servers, exists)`` for a tool's server map."""
    document, exists = load_json_object(path)
    if not exists:
        return {}, {}, False
    servers = _nested_get(document, servers_key)
    if servers is None:
        servers = {}
    if not isinstance(servers, dict):
        raise ValidationError(
            f"{path} has a {servers_key!r} key of type {type(servers).__name__}, "
            "expected an object; refusing to overwrite it"
        )
    return document, dict(servers), True


# ---------------------------------------------------------------------------
# R-10: work out a launch command that resolves on this machine.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Launch:
    """A launch command for the consuming tool to spawn.

    ``command`` is never an absolute path. Writing one would bake the machine
    that happened to run the installer into a file that is frequently committed
    or synced between machines, which is exactly the failure this record exists
    to avoid. ``absolute`` records that property so a caller can assert it.
    """

    command: str
    prefix_args: tuple[str, ...]
    runner: str
    installed_by: str
    resolves: bool
    basis: str

    @property
    def absolute(self) -> bool:
        return os.path.isabs(self.command)

    def as_dict(self) -> dict:
        return {
            "command": self.command,
            "prefix_args": list(self.prefix_args),
            "runner": self.runner,
            "installed_by": self.installed_by,
            "resolves": self.resolves,
            "absolute": self.absolute,
            "basis": self.basis,
        }


#: Isolated runners, in the order they are preferred. Each maps to the argv
#: prefix that makes it launch this project's console script.
_ISOLATED_RUNNERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("uvx", ("--from", DISTRIBUTION, DEFAULT_COMMAND)),
    ("uv", ("tool", "run", "--from", DISTRIBUTION, DEFAULT_COMMAND)),
    ("pipx", ("run", "--spec", DISTRIBUTION, DEFAULT_COMMAND)),
)


def _classify_install(script_dir: str, path_exists) -> tuple[str, str]:
    """Say how this copy of the project was installed, and why we think so."""
    parts = [p.lower() for p in Path(script_dir).parts]
    if "pipx" in parts:
        return "pipx", f"the console-script directory {script_dir} sits inside a pipx-managed tree"
    if "uv" in parts and "tools" in parts:
        return "uv-tool", f"the console-script directory {script_dir} sits inside a uv tools tree"
    if path_exists(os.path.join(os.path.dirname(script_dir), "pyvenv.cfg")):
        return "venv", f"{os.path.dirname(script_dir)} holds a pyvenv.cfg, so this is a virtual environment"
    return "system", f"the console-script directory {script_dir} is neither a runner tree nor a virtual environment"


def _first_isolated_runner(which) -> tuple[str, tuple[str, ...]] | None:
    for name, prefix in _ISOLATED_RUNNERS:
        if which(name):
            return name, prefix
    return None


def detect_launch(
    *,
    override: str | None = None,
    which=None,
    script_dir: str | None = None,
    path_exists=None,
) -> Launch:
    """Work out how to spawn this project from a configuration file.

    The three inputs that make this environment-dependent are injected rather
    than read, so a test can describe a machine instead of describing the one
    it happens to run on: ``which`` resolves a name on PATH, ``script_dir`` is
    the directory this interpreter installs console scripts into, and
    ``path_exists`` answers whether a path is there.

    The order is deliberate and is reported in ``basis``:

    1. An explicit ``override`` always wins. A caller that knows better than
       any detection has to be able to say so.
    2. A pipx or uv-tool install already puts a stable shim on PATH, so the
       bare console script is both correct and portable. Rewriting it as
       ``pipx run`` would be strictly worse: it can go to the network.
    3. A virtual-environment install is the case detection exists for. The
       console script only resolves in an activated shell, and an editor
       spawning an MCP server does not activate anything, so an isolated
       runner that is already on PATH is preferred here. That is the one
       command this module can emit that may resolve the distribution over the
       network on its first run; it is chosen only when the alternative is a
       command that does not work at all, and it is reported in ``basis``.
    4. Otherwise the console script, with ``resolves`` telling the truth about
       whether it was actually found on PATH.
    """
    lookup = which if which is not None else shutil.which
    exists = path_exists if path_exists is not None else os.path.exists
    scripts = script_dir if script_dir is not None else (sysconfig.get_path("scripts") or "")

    if override:
        return Launch(
            command=override,
            prefix_args=(),
            runner="explicit",
            installed_by="unknown",
            resolves=True,
            basis="the caller supplied an explicit launch command, so no detection was run",
        )

    installed_by, why = _classify_install(scripts, exists)
    console = lookup(DEFAULT_COMMAND)

    if installed_by == "venv" and not (console and os.path.dirname(console) != os.path.normpath(scripts)):
        isolated = _first_isolated_runner(lookup)
        if isolated is not None:
            name, prefix = isolated
            return Launch(
                command=name,
                prefix_args=prefix,
                runner=name,
                installed_by=installed_by,
                resolves=True,
                basis=(
                    f"{why}, so the {DEFAULT_COMMAND!r} console script would only resolve in an "
                    f"activated shell; {name} is on PATH and launches {DISTRIBUTION} in isolation. "
                    f"Note that {name} may resolve the distribution over the network the first time "
                    "it runs."
                ),
            )
        return Launch(
            command=DEFAULT_COMMAND,
            prefix_args=(),
            runner="console-script",
            installed_by=installed_by,
            resolves=bool(console),
            basis=(
                f"{why}, and no isolated runner ({', '.join(n for n, _ in _ISOLATED_RUNNERS)}) is on "
                f"PATH, so the {DEFAULT_COMMAND!r} console script is the only portable option. It may "
                "not resolve in the process the editor spawns; install this project with pipx or uv, "
                "or pass an explicit command."
            ),
        )

    if console:
        return Launch(
            command=DEFAULT_COMMAND,
            prefix_args=(),
            runner="console-script",
            installed_by=installed_by,
            resolves=True,
            basis=f"{why}, and {DEFAULT_COMMAND!r} resolves on PATH at {console}",
        )

    isolated = _first_isolated_runner(lookup)
    if isolated is not None:
        name, prefix = isolated
        return Launch(
            command=name,
            prefix_args=prefix,
            runner=name,
            installed_by=installed_by,
            resolves=True,
            basis=(
                f"{why}, but {DEFAULT_COMMAND!r} is not on PATH; {name} is, and launches "
                f"{DISTRIBUTION} in isolation. Note that {name} may resolve the distribution over "
                "the network the first time it runs."
            ),
        )
    return Launch(
        command=DEFAULT_COMMAND,
        prefix_args=(),
        runner="console-script",
        installed_by=installed_by,
        resolves=False,
        basis=(
            f"{why}, and neither {DEFAULT_COMMAND!r} nor any isolated runner is on PATH. The console "
            "script is written anyway because an absolute path would not be portable, but it is "
            "reported as unresolved rather than claimed to work."
        ),
    )


# ---------------------------------------------------------------------------
# The platform table, projected for callers.
# ---------------------------------------------------------------------------


def supported_tools(*, platform_key: str | None = None) -> list[dict]:
    """Return the supported tool records, sorted by name for stable output."""
    key = platform_key or platform_key_now()
    return [p.as_dict(key) for p in platforms()]


def _target(tool: str) -> Platform:
    for p in platforms():
        if p.name == tool:
            return p
    names = ", ".join(sorted(p.name for p in platforms()))
    raise ValidationError(f"unknown tool {tool!r}; supported tools are: {names}")


def _config_path(target: Platform, config_root: Path | str, platform_key: str) -> Path:
    """Join the target's relative path onto the caller-supplied root.

    The root is used verbatim. No expansion of ``~`` and no environment lookup
    happens anywhere in this module, so the caller alone decides which tree is
    written.
    """
    return Path(config_root) / resolve_relative(target.config, platform_key)


def _stop_dirs(target: Platform, config_root: Path | str, platform_key: str) -> list[Path]:
    """Directories an uninstall must never prune away for this tool.

    Its detection markers: pruning one of those would make the tool look
    uninstalled to the very next ``detect`` run, which is a change well beyond
    "remove what we wrote".
    """
    root = Path(config_root)
    return [root, *(root / resolve_relative(marker, platform_key) for marker in target.detect)]


def detect_installed(
    *, config_root: Path | str, platform_key: str | None = None
) -> list[dict]:
    """Report which supported tools are actually present under ``config_root``.

    Presence is decided by the tool's own marker paths (its configuration
    directory or its configuration file), never by a process list and never by
    running anything. A tool is reported present only when one of its markers
    exists, and the marker that decided it is reported so the answer can be
    checked. Nothing is created by looking.
    """
    key = platform_key or platform_key_now()
    root = Path(config_root)
    out: list[dict] = []
    for target in platforms():
        markers = [resolve_relative(m, key) for m in target.detect]
        found = sorted(m for m in markers if (root / m).exists())
        record = target.as_dict(key)
        record["present"] = bool(found)
        record["detected_via"] = found[0] if found else ""
        record["detect_markers"] = sorted(markers)
        out.append(record)
    return out


def detected_tool_names(*, config_root: Path | str, platform_key: str | None = None) -> list[str]:
    """Names of the tools present under ``config_root``, sorted."""
    return sorted(r["name"] for r in detect_installed(config_root=config_root, platform_key=platform_key) if r["present"])


# ---------------------------------------------------------------------------
# The server entry itself.
# ---------------------------------------------------------------------------


def _entry_args(dkg_home: Path | str | None, launch: Launch) -> list[str]:
    """Build the argv tail.

    ``dkg --home <path> mcp-stdio`` is the launch form: ``--home`` is a global
    option on the top-level parser and must precede the subcommand. The home is
    passed explicitly rather than relying on ``DKG_HOME`` or the process working
    directory, because the consuming editor spawns the server with a working
    directory we do not control.
    """
    home = default_home() if dkg_home is None else Path(dkg_home)
    return [*launch.prefix_args, "--home", os.path.abspath(home), "mcp-stdio"]


def build_entry(*, dkg_home: Path | str | None, launch: Launch, style: str) -> dict:
    """Build one server entry in the shape the target tool documents."""
    args = _entry_args(dkg_home, launch)
    if style == "mcp-servers":
        return {
            MARKER_KEY: OWNER_MARKER,
            "args": args,
            "command": launch.command,
            "description": _DESCRIPTION,
        }
    if style == "vscode-servers":
        return {
            MARKER_KEY: OWNER_MARKER,
            "args": args,
            "command": launch.command,
            "type": "stdio",
        }
    if style == "copilot-local":
        return {
            MARKER_KEY: OWNER_MARKER,
            "args": args,
            "command": launch.command,
            "type": "local",
        }
    if style == "zed-context-servers":
        # Flat command and args, with no transport tag and no description: that
        # editor's settings schema accepts the pair directly.
        return {
            MARKER_KEY: OWNER_MARKER,
            "args": args,
            "command": launch.command,
        }
    if style == "cline-transport":
        return {
            MARKER_KEY: OWNER_MARKER,
            "disabled": False,
            "transport": {"args": args, "command": launch.command, "type": "stdio"},
        }
    if style == "opencode-mcp":
        return {
            MARKER_KEY: OWNER_MARKER,
            "command": [launch.command, *args],
            "enabled": True,
            "type": "local",
        }
    raise ValidationError(f"unknown server entry style {style!r}")


def _install_plan(
    tool: str,
    *,
    config_root: Path | str,
    dkg_home: Path | str | None,
    command: str | None,
    launch: Launch | None,
    platform_key: str,
) -> tuple[dict, dict, Path, Platform]:
    """Compute the plan plus the document that would be written.

    Shared by :func:`plan_install` and :func:`install` so a dry run reports
    exactly what a real run performs; there is no second code path that could
    drift from the first.
    """
    target = _target(tool)
    path = _config_path(target, config_root, platform_key)
    document, servers, exists = _load_servers(path, target.servers_key)
    chosen = launch if launch is not None else detect_launch(override=command)
    entry = build_entry(dkg_home=dkg_home, launch=chosen, style=target.entry_style)

    existing = servers.get(SERVER_NAME)
    replaces_existing = SERVER_NAME in servers
    existing_is_ours = isinstance(existing, dict) and existing.get(MARKER_KEY) == OWNER_MARKER

    updated_servers = dict(servers)
    updated_servers[SERVER_NAME] = entry
    updated = _nested_set(document, target.servers_key, updated_servers)

    plan = {
        "action": "install",
        "tool": target.name,
        "display": target.display,
        "path": str(path),
        "servers_key": target.servers_key,
        "server_name": SERVER_NAME,
        "file_exists": exists,
        "entry": entry,
        "launch": chosen.as_dict(),
        "replaces_existing": replaces_existing,
        "replaces_unmanaged": replaces_existing and not existing_is_ours,
        "preserved_servers": sorted(k for k in servers if k != SERVER_NAME),
        "changed": serialise(updated) != serialise(document) if exists else True,
    }
    return plan, updated, path, target


def plan_install(
    tool: str,
    *,
    config_root: Path | str,
    dkg_home: Path | str | None = None,
    command: str | None = None,
    launch: Launch | None = None,
    platform_key: str | None = None,
) -> dict:
    """Report what :func:`install` would change, writing nothing.

    Nothing is created, not even the parent directory, so a plan run against a
    config root that does not exist leaves it non-existent.
    """
    plan, _updated, _path, _record = _install_plan(
        tool,
        config_root=config_root,
        dkg_home=dkg_home,
        command=command,
        launch=launch,
        platform_key=platform_key or platform_key_now(),
    )
    plan["forced"] = False
    plan["dry_run"] = True
    plan["written"] = False
    return plan


def install(
    tool: str,
    *,
    config_root: Path | str,
    dkg_home: Path | str | None = None,
    command: str | None = None,
    launch: Launch | None = None,
    platform_key: str | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict:
    """Register the read-only stdio server in ``tool``'s config.

    A missing config is created holding only our entry. An existing config keeps
    every other server and every unrelated top-level key; only our key under the
    server map is added or replaced.

    An entry already sitting under our name that does not carry
    :data:`OWNER_MARKER` is refused rather than replaced. Somebody, or some
    other installer, wrote it deliberately, and overwriting it would lose a
    configuration this project cannot reconstruct. ``force=True`` is the
    explicit way to say otherwise.
    """
    plan, updated, path, _record = _install_plan(
        tool,
        config_root=config_root,
        dkg_home=dkg_home,
        command=command,
        launch=launch,
        platform_key=platform_key or platform_key_now(),
    )
    if plan["replaces_unmanaged"] and not force:
        raise ValidationError(
            f"refusing to replace the existing {SERVER_NAME!r} entry in {path}: it does not carry "
            f"the {MARKER_KEY}={OWNER_MARKER!r} marker, so this project did not write it. "
            "Pass force to overwrite it."
        )
    plan["forced"] = bool(force)
    plan["dry_run"] = dry_run
    if dry_run:
        plan["written"] = False
        return plan
    atomic_write(path, serialise(updated))
    plan["written"] = True
    return plan


def uninstall(
    tool: str,
    *,
    config_root: Path | str,
    platform_key: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Remove our entry, and only ours.

    The entry is removed only when it carries :data:`OWNER_MARKER`. An entry
    under our name that we did not write is refused loudly and left untouched,
    because deleting configuration a human wrote is not recoverable. A config
    with no entry of ours is reported as nothing removed and is not an error, so
    uninstall is safe to run twice.
    """
    key = platform_key or platform_key_now()
    target = _target(tool)
    path = _config_path(target, config_root, key)
    document, servers, exists = _load_servers(path, target.servers_key)

    result = {
        "action": "uninstall",
        "tool": target.name,
        "display": target.display,
        "path": str(path),
        "servers_key": target.servers_key,
        "server_name": SERVER_NAME,
        "file_exists": exists,
        "dry_run": dry_run,
        "removed": False,
        "written": False,
        "removed_file": False,
        "reason": "",
    }

    if not exists:
        result["reason"] = f"no config file at {path}; nothing to remove"
        return result
    if SERVER_NAME not in servers:
        result["reason"] = f"no {SERVER_NAME!r} entry under {target.servers_key!r}; nothing to remove"
        return result

    existing = servers[SERVER_NAME]
    marker = existing.get(MARKER_KEY) if isinstance(existing, dict) else None
    if marker != OWNER_MARKER:
        raise ValidationError(
            f"refusing to remove {SERVER_NAME!r} from {path}: the entry does not carry the "
            f"{MARKER_KEY}={OWNER_MARKER!r} marker, so it was not written by this tool. "
            "Remove it by hand if you are sure."
        )

    remaining = {k: v for k, v in servers.items() if k != SERVER_NAME}
    # An empty map is kept rather than dropping the key: the consuming tool may
    # treat a missing key differently from an empty one, and removing a key we
    # did not create would be a change beyond what was asked for. The single
    # exception is a file that holds nothing but our own now-empty map, which we
    # created ourselves and therefore delete, so an install-then-uninstall round
    # trip leaves the tree exactly as it was found.
    updated = _nested_set(document, target.servers_key, remaining)
    only_ours = not remaining and serialise(updated) == serialise(_nested_set({}, target.servers_key, {}))

    result["removed"] = True
    result["preserved_servers"] = sorted(remaining)
    result["removed_file"] = only_ours
    if dry_run:
        result["reason"] = "dry run; entry would be removed"
        return result
    if only_ours:
        path.unlink()
        result["written"] = True
        result["pruned_dirs"] = prune_empty_dirs(
            [path.parent], root=Path(config_root), stop_dirs=_stop_dirs(target, config_root, key)
        )
        result["reason"] = "entry removed; the file held nothing else and was deleted"
        return result
    atomic_write(path, serialise(updated))
    result["written"] = True
    result["reason"] = "entry removed"
    return result


# ---------------------------------------------------------------------------
# Bundles: the server entry plus the tool's own native artifacts.
# ---------------------------------------------------------------------------
#
# artifacts and rules are imported inside these functions on purpose. They both
# import the primitives above, so a module-level import here would be a cycle.
# Keeping the primitives in one place and paying for a function-level import is
# the cheaper of the two costs.


def install_bundle(
    tool: str,
    *,
    config_root: Path | str,
    dkg_home: Path | str | None = None,
    command: str | None = None,
    launch: Launch | None = None,
    platform_key: str | None = None,
    with_hooks: bool = True,
    with_commands: bool = True,
    with_rules: bool = True,
    force: bool = False,
    dry_run: bool = False,
) -> dict:
    """Install everything this project writes for one tool.

    The server entry always; the tool's hook definitions, its command or skill
    package, and its rules block only when the tool actually supports that kind
    of artifact. A tool that supports none of them gets the server entry and an
    honest note saying why nothing else was written.
    """
    from . import artifacts
    from . import rules as rules_module

    key = platform_key or platform_key_now()
    target = _target(tool)
    chosen = launch if launch is not None else detect_launch(override=command)
    root = Path(config_root)

    # The server plan is the base of the result rather than a nested key, so a
    # single-tool bundle keeps reporting path, servers_key, written and dry_run
    # at the top level exactly as the plain install does. A caller that only
    # cares about the server entry sees the same shape either way.
    server = install(
        tool,
        config_root=config_root,
        dkg_home=dkg_home,
        launch=chosen,
        platform_key=key,
        force=force,
        dry_run=dry_run,
    )
    result: dict = {
        **server,
        "action": "install-bundle",
        "display": target.display,
        "launch": chosen.as_dict(),
        "server": server,
        "hooks": None,
        "commands": None,
        "rules": None,
        "skipped": [],
    }

    if with_hooks:
        if target.hooks is None:
            result["skipped"].append({"artifact": "hooks", "reason": target.hooks_note})
        else:
            result["hooks"] = artifacts.install_hooks(
                target, config_root=root, platform_key=key, launch=chosen, dry_run=dry_run
            )
    if with_commands:
        if target.commands is None:
            result["skipped"].append({"artifact": "commands", "reason": target.commands_note})
        else:
            result["commands"] = artifacts.install_commands(
                target, config_root=root, platform_key=key, dry_run=dry_run
            )
    if with_rules:
        if target.rules is None:
            result["skipped"].append({"artifact": "rules", "reason": target.rules_note})
        else:
            path = root / resolve_relative(target.rules, key)
            result["rules"] = rules_module.install_rules(path, dry_run=dry_run).as_dict()

    result["skipped"].sort(key=lambda s: s["artifact"])
    return result


def uninstall_bundle(
    tool: str,
    *,
    config_root: Path | str,
    platform_key: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Remove everything :func:`install_bundle` writes for one tool, and nothing else.

    Symmetric by construction: the same platform record drives both directions,
    and each removal refuses anything that does not carry :data:`OWNER_MARKER`.
    """
    from . import artifacts
    from . import rules as rules_module

    key = platform_key or platform_key_now()
    target = _target(tool)
    root = Path(config_root)

    server = uninstall(tool, config_root=config_root, platform_key=key, dry_run=dry_run)
    result: dict = {
        **server,
        "action": "uninstall-bundle",
        "display": target.display,
        "server": server,
        "hooks": None,
        "commands": None,
        "rules": None,
    }
    touched: list[Path] = []
    if target.hooks is not None:
        result["hooks"] = artifacts.uninstall_hooks(target, config_root=root, platform_key=key, dry_run=dry_run)
        touched.append(Path(result["hooks"]["path"]).parent)
    if target.commands is not None:
        result["commands"] = artifacts.uninstall_commands(target, config_root=root, platform_key=key, dry_run=dry_run)
        touched.extend(Path(p).parent for p in result["commands"]["removed"])
    if target.rules is not None:
        path = root / resolve_relative(target.rules, key)
        result["rules"] = rules_module.uninstall_rules(path, dry_run=dry_run).as_dict()
        touched.append(path.parent)
    result["pruned_dirs"] = (
        []
        if dry_run
        else prune_empty_dirs(touched, root=root, stop_dirs=_stop_dirs(target, root, key))
    )
    return result


def install_all(
    *,
    config_root: Path | str,
    dkg_home: Path | str | None = None,
    command: str | None = None,
    launch: Launch | None = None,
    platform_key: str | None = None,
    only_detected: bool = True,
    with_hooks: bool = True,
    with_commands: bool = True,
    with_rules: bool = True,
    force: bool = False,
    dry_run: bool = False,
) -> dict:
    """Configure every tool detected under ``config_root`` in one call.

    ``only_detected`` is the default because writing a configuration file for a
    tool that is not installed creates a directory the user did not ask for and
    tells them nothing. Passing ``only_detected=False`` configures every
    supported tool, which is what a machine image build wants.
    """
    key = platform_key or platform_key_now()
    chosen = launch if launch is not None else detect_launch(override=command)
    detected = detect_installed(config_root=config_root, platform_key=key)
    names = sorted(r["name"] for r in detected if r["present"] or not only_detected)

    return {
        "action": "install-all",
        "config_root": str(config_root),
        "platform_key": key,
        "only_detected": only_detected,
        "dry_run": dry_run,
        "launch": chosen.as_dict(),
        "considered": sorted(r["name"] for r in detected),
        "selected": names,
        "skipped_not_present": sorted(r["name"] for r in detected if not r["present"]) if only_detected else [],
        "results": [
            install_bundle(
                name,
                config_root=config_root,
                dkg_home=dkg_home,
                launch=chosen,
                platform_key=key,
                with_hooks=with_hooks,
                with_commands=with_commands,
                with_rules=with_rules,
                force=force,
                dry_run=dry_run,
            )
            for name in names
        ],
    }


def uninstall_all(
    *,
    config_roots,
    platform_key: str | None = None,
    tools: list[str] | None = None,
    dry_run: bool = False,
) -> dict:
    """Unbind every supported tool under each of ``config_roots``.

    The roots are supplied by the caller. This module never reads the watch
    registry itself, because that would mean reaching for a path this module is
    not allowed to derive; the CLI resolves the registry and passes the roots
    in.
    """
    key = platform_key or platform_key_now()
    names = sorted(tools) if tools else sorted(p.name for p in platforms())
    roots = [str(r) for r in config_roots]
    return {
        "action": "uninstall-all",
        "config_roots": roots,
        "platform_key": key,
        "tools": names,
        "dry_run": dry_run,
        "results": [
            {
                "config_root": root,
                "results": [
                    uninstall_bundle(name, config_root=root, platform_key=key, dry_run=dry_run)
                    for name in names
                ],
            }
            for root in sorted(roots)
        ],
    }


def purge_data(*, dkg_home: Path | str, dry_run: bool = False) -> dict:
    """Delete a DKG home, the opposite of the ``--keep-data`` scope.

    Refused unless the named directory actually looks like a DKG home, that is
    it holds the graph database this project creates. Deleting a directory a
    user pointed at by mistake is not recoverable, so the check is a hard gate
    rather than a warning, and keeping the data is the default everywhere.
    """
    home = Path(dkg_home)
    result = {
        "action": "purge-data",
        "dkg_home": str(home),
        "exists": home.exists(),
        "removed": False,
        "dry_run": dry_run,
        "reason": "",
    }
    if not home.exists():
        result["reason"] = f"no directory at {home}; nothing to remove"
        return result
    if not home.is_dir():
        raise ValidationError(f"refusing to purge {home}: it is not a directory")
    if not (home / "graph.sqlite").exists():
        raise ValidationError(
            f"refusing to purge {home}: it holds no graph.sqlite, so it does not look like a "
            "D-Knowledge Graph home. Name the home explicitly if you are sure."
        )
    if dry_run:
        result["reason"] = "dry run; the home would be deleted"
        return result
    shutil.rmtree(home)
    result["removed"] = True
    result["reason"] = "home deleted"
    return result

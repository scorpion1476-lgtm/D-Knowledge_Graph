"""The table of AI coding tools this project knows how to configure.

This module is data. It holds no filesystem access, no environment lookup, and
no logic beyond resolving a per-operating-system path variant, so the honest
question "where does this path come from" is answerable by reading one record.

Three rules the table follows, and they are the reason it is a separate module:

*   **Every record says what its path is based on.** ``note`` is not decoration.
    A configuration path that is wrong is worse than an unsupported tool,
    because it creates a directory the user did not ask for and then reports
    success. Where a path could not be established with confidence the tool is
    absent from this table rather than present with a guess. Tools left out for
    exactly that reason, and the reason each was left out, are listed in
    :data:`NOT_SUPPORTED`.
*   **Absent support is recorded, not silently skipped.** A tool with no
    documented hook mechanism, or one whose hook mechanism needs a detail this
    project has not verified, has ``hooks=None`` and a ``hooks_note`` saying
    which of those it is. Writing a hook file that the tool will never read, or
    one that fires on every tool call because we could not name the right
    event, would look like breadth and deliver worse than nothing.
*   **Paths are relative to a caller-supplied config root.** For a user-level
    install that root is the user's home directory; for a project-level install
    it is a repository root, and several of these tools read the same relative
    path in both places. Nothing here resolves ``~``.

Path variants per operating system are expressed as a mapping keyed by
``sys.platform`` values, with ``"*"`` as the fallback. The key is passed in by
the caller so a test can describe Windows from macOS.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import dataclass

#: The ``sys.platform`` values this table distinguishes. Anything else falls
#: back to the ``"*"`` entry, which is the POSIX layout.
PLATFORM_KEYS: tuple[str, ...] = ("darwin", "linux", "win32")

#: Tools deliberately left out, with the reason. Kept in the source rather than
#: in a document because the reason is a property of the table: somebody adding
#: a row needs to see why a neighbour was refused.
NOT_SUPPORTED: tuple[tuple[str, str], ...] = (
    (
        "OpenAI Codex CLI",
        "its MCP servers live in a TOML table in ~/.codex/config.toml. Rewriting TOML with the "
        "standard library would drop the user's comments, and this project adds no dependency to "
        "avoid that, so nothing is written rather than something lossy.",
    ),
    (
        "Goose",
        "its extensions live in a YAML file. Same reason as Codex: no YAML writer in the standard "
        "library, and no new runtime dependency.",
    ),
    (
        "Crush (current format)",
        "the current configuration is an executable shell script. The deprecated JSON file is "
        "supported instead and is the row shipped here; a generated shell script is not something "
        "this project will write into a user's shell.",
    ),
    (
        "JetBrains AI Assistant",
        "its MCP servers are entered through a settings dialog and no on-disk configuration file "
        "is documented. Junie, the JetBrains agent that is file-configured, is supported instead.",
    ),
    (
        "Trae",
        "only the project-level .trae/mcp.json is documented; the user-level path is not, and "
        "guessing it would create a directory the tool may never read.",
    ),
    (
        "Google Antigravity",
        "the documented path and the path a current install actually uses disagree. Until that is "
        "settled, writing to either would be a coin flip.",
    ),
)


def platform_key_now() -> str:
    """Return the current platform key.

    ``sys.platform`` is a build-time constant of the interpreter, not a user
    setting, so reading it does not make this module environment-dependent in
    the way a home-directory lookup would.
    """
    return sys.platform if sys.platform in PLATFORM_KEYS else "linux"


def resolve_relative(value, platform_key: str) -> str:
    """Pick the path variant for ``platform_key``."""
    if isinstance(value, str):
        return value
    return str(value.get(platform_key) or value["*"])


@dataclass(frozen=True)
class HookSpec:
    """Where a tool's hook definitions live and in what shape.

    ``style`` selects the writer in :mod:`dkg.mcp.artifacts`. ``events`` are the
    tool's own event names, and only events that mean "source changed on disk"
    are used: an incremental re-ingest is worth running then and is waste after
    a read.
    """

    file: object
    root_key: str
    style: str
    events: tuple[str, ...]
    matcher: str = ""
    command_key: str = "command"
    entry_extra: tuple[tuple[str, object], ...] = ()
    document_extra: tuple[tuple[str, object], ...] = ()


@dataclass(frozen=True)
class CommandSpec:
    """Where a tool loads user-authored commands, prompts, or skills from.

    ``directory`` may be ``None`` for a tool that documents a skill package but
    not a user-level command directory: the skill is written and no invented
    command directory is created.
    """

    fmt: str = "markdown"
    extension: str = ".md"
    directory: object = None
    skill_directory: object = None


@dataclass(frozen=True)
class Platform:
    """One AI coding tool this project can configure.

    Frozen because these records are module-level constants shared by every
    caller; making them immutable removes any chance that one call mutates the
    target another call is about to use.
    """

    name: str
    display: str
    config: object
    servers_key: str
    entry_style: str
    detect: tuple
    note: str
    hooks: HookSpec | None = None
    hooks_note: str = ""
    commands: CommandSpec | None = None
    commands_note: str = ""
    rules: object = None
    rules_note: str = ""

    def as_dict(self, platform_key: str) -> dict:
        return {
            "name": self.name,
            "display": self.display,
            "relative_path": resolve_relative(self.config, platform_key),
            "servers_key": self.servers_key,
            "entry_style": self.entry_style,
            "note": self.note,
            "hooks_path": resolve_relative(self.hooks.file, platform_key) if self.hooks else "",
            "hooks_note": self.hooks_note,
            "commands_path": self._commands_path(platform_key),
            "commands_note": self.commands_note,
            "rules_path": resolve_relative(self.rules, platform_key) if self.rules else "",
            "rules_note": self.rules_note,
        }

    def _commands_path(self, platform_key: str) -> str:
        if self.commands is None:
            return ""
        target = self.commands.directory or self.commands.skill_directory
        return resolve_relative(target, platform_key)


# Repeated path fragments, named once so a typo cannot differ between records.
_VSCODE_USER = {
    "*": ".config/Code/User",
    "darwin": "Library/Application Support/Code/User",
    "win32": "AppData/Roaming/Code/User",
}

#: The cross-tool skill location several of these tools search. Writing the
#: same package there twice is harmless: the bytes are identical either way.
_SHARED_SKILLS = ".agents/skills/d-knowledge-graph"

_HOOK_NOT_DOCUMENTED = "This tool documents no hook configuration file, so none is written."
_HOOK_VOCABULARY_UNVERIFIED = (
    "This tool does document hooks, but not in a way this project has verified well enough to "
    "scope one to an edit: its event or matcher vocabulary for 'a file was written' was not "
    "confirmed. A hook that fired after every tool call instead would re-ingest after a search, "
    "so none is written."
)
_COMMANDS_NOT_DOCUMENTED = (
    "No user-level command, prompt, or skill directory is documented for this tool, so none is "
    "created. Inventing one would leave a directory the tool never reads."
)


def _under(base: Mapping[str, str], tail: str) -> dict:
    return {key: f"{value}/{tail}" for key, value in base.items()}


#: The supported tools. Sorted by name in :func:`platforms` so output order is a
#: property of the data, not of how the list was typed.
_PLATFORMS: tuple[Platform, ...] = (
    Platform(
        name="claude-code",
        display="Claude Code",
        config=".claude.json",
        servers_key="mcpServers",
        entry_style="mcp-servers",
        detect=(".claude.json", ".claude"),
        note=(
            "Claude Code's documented user-level MCP file, with the server map under 'mcpServers'. "
            "Confirmed against the vendor documentation and against a working install."
        ),
        hooks=HookSpec(
            file=".claude/settings.json",
            root_key="hooks",
            style="claude-settings",
            events=("PostToolUse",),
            matcher="Edit|MultiEdit|Write",
        ),
        commands=CommandSpec(
            directory=".claude/commands",
            skill_directory=".claude/skills/d-knowledge-graph",
        ),
        rules=".claude/CLAUDE.md",
    ),
    Platform(
        name="cursor",
        display="Cursor",
        config=".cursor/mcp.json",
        servers_key="mcpServers",
        entry_style="mcp-servers",
        detect=(".cursor",),
        note=(
            "Cursor's documented MCP file. The same relative path is read at the user level (under "
            "the home directory) and at the project level (under a repository root), so one record "
            "serves both roots."
        ),
        hooks=HookSpec(
            file=".cursor/hooks.json",
            root_key="hooks",
            style="flat-list",
            events=("afterFileEdit",),
            document_extra=(("version", 1),),
        ),
        commands=CommandSpec(skill_directory=".cursor/skills/d-knowledge-graph"),
        commands_note=(
            "The user-level skill directory is documented; a user-level command directory is not, "
            "so only the skill package is written."
        ),
        rules=".cursor/rules/d-knowledge-graph.mdc",
        rules_note=(
            "Cursor's rules files are documented at the project level and the .mdc extension is "
            "required. Cursor's user-level rules are held in application settings rather than on "
            "disk, so this path is only meaningful when the config root is a repository root."
        ),
    ),
    Platform(
        name="windsurf",
        display="Windsurf",
        config=".codeium/windsurf/mcp_config.json",
        servers_key="mcpServers",
        entry_style="mcp-servers",
        detect=(".codeium/windsurf", ".codeium"),
        note=(
            "Windsurf's documented user-level MCP file, under the Codeium configuration directory. "
            "The Windows path is not stated separately by the vendor; it is the same relative path "
            "under the user profile."
        ),
        hooks=HookSpec(
            file=".codeium/windsurf/hooks.json",
            root_key="hooks",
            style="flat-list",
            events=("post_write_code",),
        ),
        commands=CommandSpec(directory=".codeium/windsurf/global_workflows"),
        rules=".codeium/windsurf/memories/global_rules.md",
        rules_note=(
            "The documented global rules file. It has a size limit of a few thousand characters, "
            "which the injected block stays well inside."
        ),
    ),
    Platform(
        name="gemini-cli",
        display="Gemini CLI",
        config=".gemini/settings.json",
        servers_key="mcpServers",
        entry_style="mcp-servers",
        detect=(".gemini",),
        note=(
            "Gemini CLI's documented user settings file, identical on every operating system; the "
            "MCP server map is a key inside it."
        ),
        hooks=None,
        hooks_note=_HOOK_VOCABULARY_UNVERIFIED,
        commands=CommandSpec(directory=".gemini/commands", fmt="toml", extension=".toml"),
        rules=".gemini/GEMINI.md",
    ),
    Platform(
        name="qwen-code",
        display="Qwen Code",
        config=".qwen/settings.json",
        servers_key="mcpServers",
        entry_style="mcp-servers",
        detect=(".qwen",),
        note=(
            "Qwen Code is a fork of Gemini CLI and keeps its settings layout with the configuration "
            "directory renamed. Same file shape, same server-map key."
        ),
        hooks=None,
        hooks_note=_HOOK_VOCABULARY_UNVERIFIED,
        commands=CommandSpec(directory=".qwen/commands"),
        commands_note="Markdown is the documented format here; the TOML form its parent project uses is deprecated.",
        rules=".qwen/QWEN.md",
    ),
    Platform(
        name="vscode",
        display="Visual Studio Code",
        config=_under(_VSCODE_USER, "mcp.json"),
        servers_key="servers",
        entry_style="vscode-servers",
        detect=(_VSCODE_USER,),
        note=(
            "VS Code's own MCP support (not an extension) reads a user-level mcp.json from the user "
            "data directory. The vendor documents that directory for settings.json but does not "
            "print it for mcp.json, so the path here is the documented settings.json location with "
            "the filename changed. The server map key is 'servers', not 'mcpServers', and each "
            "entry names its transport type."
        ),
        hooks=None,
        hooks_note=_HOOK_NOT_DOCUMENTED,
        commands=None,
        commands_note=(
            "VS Code prompt files are documented at the workspace level; the user-level directory "
            "is not printed by the vendor, so none is created."
        ),
        rules=None,
        rules_note=(
            "Copilot instructions are documented as a repository file (.github/copilot-instructions.md), "
            "which is not what this root is, so nothing is injected here. The Copilot CLI record "
            "covers the documented user-level instructions file."
        ),
    ),
    Platform(
        name="claude-desktop",
        display="Claude Desktop",
        config={
            "*": ".config/Claude/claude_desktop_config.json",
            "darwin": "Library/Application Support/Claude/claude_desktop_config.json",
            "win32": "AppData/Roaming/Claude/claude_desktop_config.json",
        },
        servers_key="mcpServers",
        entry_style="mcp-servers",
        detect=(
            {
                "*": ".config/Claude",
                "darwin": "Library/Application Support/Claude",
                "win32": "AppData/Roaming/Claude",
            },
        ),
        note=(
            "The macOS and Windows paths are the ones the MCP quickstart documents. The Linux path "
            "is the same file under the platform's configuration directory and is not separately "
            "documented, which is stated here rather than presented as verified."
        ),
        hooks=None,
        hooks_note="Claude Desktop has no hook configuration file of its own.",
        commands=None,
        commands_note=(
            "Claude Desktop's skills are synced through the account rather than read from a local "
            "directory, so there is nothing on disk to write."
        ),
        rules=None,
        rules_note=(
            "Claude Desktop keeps its custom instructions in application settings rather than a "
            "file on disk, so there is nothing to inject a marked block into."
        ),
    ),
    Platform(
        name="amazon-q-cli",
        display="Amazon Q Developer CLI",
        config=".aws/amazonq/mcp.json",
        servers_key="mcpServers",
        entry_style="mcp-servers",
        detect=(".aws/amazonq",),
        note=(
            "Amazon Q Developer CLI's documented global MCP file under the AWS configuration "
            "directory. Newer versions read MCP servers from an agent configuration instead and "
            "merge this file only when the agent opts in, so the entry may need that opt-in to "
            "take effect."
        ),
        hooks=None,
        hooks_note=(
            "Amazon Q configures hooks inside an agent definition rather than a hooks file, and "
            "writing into somebody's agent definition is a larger claim than this project makes."
        ),
        commands=CommandSpec(directory=".aws/amazonq/prompts", fmt="markdown-plain"),
        commands_note=(
            "The documented prompts directory takes plain markdown with no frontmatter, so the "
            "marker is written as a comment instead."
        ),
        rules=None,
        rules_note=(
            "Only project-level rules are documented, pulled in through an agent's resources list. "
            "No user-level rules file is documented, so none is written."
        ),
    ),
    Platform(
        name="kiro",
        display="Kiro",
        config=".kiro/settings/mcp.json",
        servers_key="mcpServers",
        entry_style="mcp-servers",
        detect=(".kiro",),
        note=(
            "Kiro's documented user-level MCP file. The same relative path under a repository root "
            "is its workspace-level file, so one record serves both roots."
        ),
        hooks=None,
        hooks_note=(
            "Kiro's agent hooks are documented at the project level only; no user-level hooks "
            "directory is documented, so none is written."
        ),
        commands=CommandSpec(skill_directory=".kiro/skills/d-knowledge-graph"),
        rules=".kiro/steering/d-knowledge-graph.md",
    ),
    Platform(
        name="zed",
        display="Zed",
        config=".config/zed/settings.json",
        servers_key="context_servers",
        entry_style="zed-context-servers",
        detect=(".config/zed",),
        note=(
            "Zed keeps its settings under the configuration directory on macOS as well as Linux. "
            "MCP servers are registered under 'context_servers', its own name for them, and the "
            "entry is a flat command and args pair with no transport tag."
        ),
        hooks=None,
        hooks_note=_HOOK_NOT_DOCUMENTED,
        commands=CommandSpec(skill_directory=_SHARED_SKILLS),
        commands_note=(
            "Zed reads skills from the shared cross-tool skills directory rather than one of its "
            "own, so the package is written there."
        ),
        rules=".config/zed/AGENTS.md",
    ),
    Platform(
        name="opencode",
        display="opencode",
        config=".config/opencode/opencode.json",
        servers_key="mcp",
        entry_style="opencode-mcp",
        detect=(".config/opencode",),
        note=(
            "opencode's documented global configuration file. Its MCP map is keyed 'mcp' and a "
            "local server is one argv list under a 'local' type, which is why it has its own entry "
            "style."
        ),
        hooks=None,
        hooks_note="opencode extends itself with plugins written in JavaScript rather than a hook definition file.",
        commands=CommandSpec(directory=".config/opencode/commands"),
        commands_note="The plural directory name is the documented one; the singular form is a compatibility alias.",
        rules=".config/opencode/AGENTS.md",
    ),
    Platform(
        name="crush",
        display="Crush",
        config=".config/crush/crush.json",
        servers_key="mcp",
        entry_style="vscode-servers",
        detect=(".config/crush",),
        note=(
            "Crush's JSON configuration file. Its current configuration format is an executable "
            "shell script, which this project will not generate, so the JSON file it still supports "
            "is used instead. Its MCP map is keyed 'mcp' and each entry names its transport type."
        ),
        hooks=None,
        hooks_note=(
            "Crush does support hooks in this file, but its global-scope hook commands are "
            "documented as needing an absolute path. This project does not write a machine-specific "
            "absolute path into a shared configuration file, so no hook is written."
        ),
        commands=CommandSpec(skill_directory=".config/crush/skills/d-knowledge-graph"),
        rules=".config/crush/CRUSH.md",
    ),
    Platform(
        name="cline",
        display="Cline",
        config=".cline/data/settings/cline_mcp_settings.json",
        servers_key="mcpServers",
        entry_style="cline-transport",
        detect=(".cline",),
        note=(
            "Cline's current shared settings file, the same path for its editor extension and its "
            "command-line tool on every operating system. The older location inside the editor's "
            "extension storage is read once and migrated, so writing the current path is correct "
            "for a current install."
        ),
        hooks=None,
        hooks_note=(
            "Cline's hooks are executable scripts named after the event rather than a declarative "
            "file. This project does not generate executables, so none is written."
        ),
        commands=CommandSpec(directory=".cline/data/workflows"),
        rules=".cline/rules/d-knowledge-graph.md",
    ),
    Platform(
        name="roo-code",
        display="Roo Code",
        config=_under(
            _VSCODE_USER, "globalStorage/rooveterinaryinc.roo-cline/settings/mcp_settings.json"
        ),
        servers_key="mcpServers",
        entry_style="mcp-servers",
        detect=(
            _under(_VSCODE_USER, "globalStorage/rooveterinaryinc.roo-cline"),
            ".roo",
        ),
        note=(
            "Roo Code is an editor extension and its global MCP file genuinely lives in the "
            "extension's storage directory, so the path carries both the platform-specific editor "
            "user directory and the published extension identifier. Its project-level file is "
            ".roo/mcp.json, which is a different scope."
        ),
        hooks=None,
        hooks_note=_HOOK_NOT_DOCUMENTED,
        commands=CommandSpec(directory=".roo/commands"),
        rules=".roo/rules/d-knowledge-graph.md",
    ),
    Platform(
        name="copilot-cli",
        display="GitHub Copilot CLI",
        config=".copilot/mcp-config.json",
        servers_key="mcpServers",
        entry_style="copilot-local",
        detect=(".copilot",),
        note=(
            "GitHub Copilot CLI's documented MCP configuration file under its own home directory. "
            "Each entry names its transport with a 'local' type rather than 'stdio'."
        ),
        hooks=None,
        hooks_note=(
            "Copilot CLI hook entries carry no matcher, so a hook registered here would run after "
            "every tool call rather than after an edit. That is a re-ingest after every search, so "
            "none is written."
        ),
        commands=CommandSpec(skill_directory=".copilot/skills/d-knowledge-graph"),
        rules=".copilot/copilot-instructions.md",
    ),
    Platform(
        name="junie",
        display="Junie",
        config=".junie/mcp/mcp.json",
        servers_key="mcpServers",
        entry_style="mcp-servers",
        detect=(".junie",),
        note=(
            "Junie's documented user-level MCP file, shared by its command-line tool and its editor "
            "plugin. Its project-level file is the same relative path under a repository root."
        ),
        hooks=None,
        hooks_note=_HOOK_VOCABULARY_UNVERIFIED,
        commands=CommandSpec(
            directory=".junie/commands", skill_directory=".junie/skills/d-knowledge-graph"
        ),
        rules=".junie/AGENTS.md",
    ),
    Platform(
        name="factory-droid",
        display="Factory Droid",
        config=".factory/mcp.json",
        servers_key="mcpServers",
        entry_style="mcp-servers",
        detect=(".factory",),
        note=(
            "Factory's documented user-level MCP file. It is mcp.json, a sibling of the settings "
            "file rather than a key inside it."
        ),
        hooks=None,
        hooks_note=_HOOK_VOCABULARY_UNVERIFIED,
        commands=CommandSpec(directory=".factory/commands"),
        rules=".factory/AGENTS.md",
        rules_note=(
            "Factory checks its own configuration directory for a personal instructions file. The "
            "mechanism is documented; this exact filename inside it follows the convention the "
            "vendor uses at the project level."
        ),
    ),
    Platform(
        name="amp",
        display="Amp",
        config=".config/amp/settings.json",
        servers_key="amp.mcpServers",
        entry_style="mcp-servers",
        detect=(".config/amp",),
        note=(
            "Amp uses the same configuration directory on macOS and Linux. Its server map is a flat "
            "key with a dot in the name ('amp.mcpServers') inside the settings object, not a nested "
            "object, which is why this record is the one that exercises the dotted-key writer."
        ),
        hooks=None,
        hooks_note="Amp extends itself with plugins written in TypeScript rather than a hook definition file.",
        commands=CommandSpec(skill_directory=".config/amp/skills/d-knowledge-graph"),
        rules=".config/amp/AGENTS.md",
    ),
    Platform(
        name="kilo-code",
        display="Kilo Code",
        config=".config/kilo/kilo.jsonc",
        servers_key="mcp",
        entry_style="opencode-mcp",
        detect=(".config/kilo",),
        note=(
            "Kilo Code's current shared configuration file, used by its editor extension and its "
            "command-line tool alike; the older location inside the editor's extension storage is "
            "migrated. The file permits comments, and plain JSON is valid there. Its map is keyed "
            "'mcp' and a local server is an argv list, the same shape opencode uses."
        ),
        hooks=None,
        hooks_note="Kilo Code's hooks ship inside JavaScript plugins rather than a hook definition file.",
        commands=CommandSpec(directory=".config/kilo/commands"),
        rules=None,
        rules_note=(
            "Kilo Code's current instructions live in a key inside its main configuration file "
            "rather than a markdown file, so there is no delimited text file to inject into."
        ),
    ),
    Platform(
        name="continue",
        display="Continue",
        config=".continue/mcpServers/d-knowledge-graph.json",
        servers_key="mcpServers",
        entry_style="mcp-servers",
        detect=(".continue",),
        note=(
            "Continue loads each file in its mcpServers directory, and that loader accepts the "
            "same object-with-an-mcpServers-key shape the other tools use. Writing one file of our "
            "own there avoids editing the user's main configuration file at all."
        ),
        hooks=None,
        hooks_note=_HOOK_NOT_DOCUMENTED,
        commands=CommandSpec(skill_directory=".continue/skills/d-knowledge-graph"),
        rules=".continue/rules/d-knowledge-graph.md",
    ),
)


def platforms() -> tuple[Platform, ...]:
    """Every supported tool, sorted by name so output order is deterministic."""
    return tuple(sorted(_PLATFORMS, key=lambda p: p.name))


def platform_names() -> tuple[str, ...]:
    return tuple(p.name for p in platforms())

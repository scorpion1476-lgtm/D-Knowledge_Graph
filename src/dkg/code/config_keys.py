"""Configuration keys as graph nodes. Keys only, never values.

"Which code reads DATABASE_URL" is a question the graph could not answer,
because configuration lived entirely outside it. This module parses the keys out
of a project's configuration files, gives each one a ``code:config`` node, and
links it to the code that binds it.

THE VALUE IS NEVER STORED. Not in the node, not in its metadata, not in a chunk,
not in an excerpt. A configuration file is where secrets live: a database URL
with a password in it, an API token, a signing key. A knowledge graph that
ingested those would turn every read of the graph, and every backup of it, into
a way to leak them. The parser discards the value at the point of reading, before
it can reach any structure that is persisted, and a test asserts that no value
from a fixture appears anywhere in the database.

The key itself is not a secret: it is the same string that appears in the source
code that reads it, which is already indexed.

Formats handled are the common externalised-configuration ones, each by a small
documented reader rather than by a dependency: ``.env``, ``.properties``,
``.ini``/``.cfg``, ``.toml``, ``.yaml``/``.yml``, and ``.json``. Nested keys are
flattened with dots, because that is how the code that reads them names them.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .model import Symbol

KIND_CONFIG = "config"
EDGE_CONFIGURES = "configures"

CONFIG_EXTENSIONS = (".env", ".properties", ".ini", ".cfg", ".toml", ".yaml", ".yml", ".json")

# Filenames that are configuration regardless of extension, and ones that are
# NOT despite matching. A repository's package manifest is not externalised
# application configuration; treating it as such would fill the graph with the
# names of dependencies.
ALWAYS_CONFIG = (".env",)
NEVER_CONFIG = (
    "package.json",
    "package-lock.json",
    "composer.json",
    "composer.lock",
    "tsconfig.json",
    "jsconfig.json",
    "pyproject.toml",
    "poetry.lock",
    "Cargo.toml",
    "Cargo.lock",
)

# Bounds: a configuration file must not be able to produce an unbounded node set.
MAX_KEYS_PER_FILE = 2000
MAX_CONFIG_BYTES = 2_000_000
MAX_DEPTH = 12

# How code binds a configuration key. Each pattern captures the key in group
# "key" and nothing else; the surrounding value is never touched.
BINDING_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Python: os.environ["X"], os.environ.get("X"), os.getenv("X")
    ("python-env", re.compile(r"os\.(?:environ(?:\.get)?\s*[\[\(]|getenv\s*\()\s*[\"'](?P<key>[^\"']+)[\"']")),
    # Python and generic: config.get("a.b"), settings["a.b"], conf["a.b"]
    ("config-lookup", re.compile(r"\b(?:config|settings|conf|cfg)\s*(?:\.get\s*\(|\[)\s*[\"'](?P<key>[^\"']+)[\"']")),
    # JavaScript and TypeScript: process.env.X and process.env["X"]
    ("node-env", re.compile(r"process\.env\s*(?:\.\s*(?P<key>[A-Za-z_][A-Za-z0-9_]*)|\[\s*[\"'`](?P<key2>[^\"'`]+)[\"'`]\s*\])")),
    # Go: os.Getenv("X")
    ("go-env", re.compile(r"os\.Getenv\(\s*\"(?P<key>[^\"]+)\"")),
    # Java and Spring: @Value("${a.b}")
    ("spring-value", re.compile(r"@Value\s*\(\s*[\"']\$\{(?P<key>[^:}\"']+)")),
    # PHP and Laravel: env('X'), config('a.b')
    ("php-env", re.compile(r"\b(?:env|config)\s*\(\s*[\"'](?P<key>[^\"']+)[\"']")),
)

_ENV_LINE = re.compile(r"^\s*(?:export\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_.]*)\s*=")
_PROPERTIES_LINE = re.compile(r"^\s*(?P<key>[^#!;\s][^=:]*?)\s*[=:]")
_INI_SECTION = re.compile(r"^\s*\[(?P<section>[^\]]+)\]\s*$")
_TOML_TABLE = re.compile(r"^\s*\[{1,2}\s*(?P<table>[^\]]+?)\s*\]{1,2}\s*$")
_TOML_KEY = re.compile(r"^\s*(?P<key>[A-Za-z0-9_.\-\"']+)\s*=")
_YAML_LINE = re.compile(r"^(?P<indent>\s*)(?P<key>[A-Za-z0-9_.\-]+)\s*:(?P<rest>.*)$")


def is_config_file(path: str | Path) -> bool:
    """Whether this path is externalised application configuration."""
    name = Path(path).name
    if name in NEVER_CONFIG:
        return False
    if name in ALWAYS_CONFIG or name.startswith(".env."):
        return True
    return Path(path).suffix.lower() in CONFIG_EXTENSIONS


def extract_keys(path: str | Path, text: str) -> list[str]:
    """Every configuration key in a file, flattened with dots. Never a value.

    Each reader below takes the key and discards the rest of the line at the
    point of reading, so no value is ever held in a variable that reaches a
    caller.
    """
    path = str(path)
    suffix = Path(path).suffix.lower()
    name = Path(path).name
    if name in ALWAYS_CONFIG or name.startswith(".env.") or suffix == ".env":
        keys = _env_keys(text)
    elif suffix == ".properties":
        keys = _properties_keys(text)
    elif suffix in (".ini", ".cfg"):
        keys = _ini_keys(text)
    elif suffix == ".toml":
        keys = _toml_keys(text)
    elif suffix in (".yaml", ".yml"):
        keys = _yaml_keys(text)
    elif suffix == ".json":
        keys = _json_keys(text)
    else:
        keys = []
    seen: list[str] = []
    known: set[str] = set()
    for key in keys:
        cleaned = key.strip().strip("\"'")
        if not cleaned or cleaned in known:
            continue
        known.add(cleaned)
        seen.append(cleaned)
        if len(seen) >= MAX_KEYS_PER_FILE:
            break
    return seen


def _env_keys(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        match = _ENV_LINE.match(line)
        if match:
            out.append(match.group("key"))
    return out


def _properties_keys(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if not stripped or stripped[0] in "#!;":
            continue
        match = _PROPERTIES_LINE.match(line)
        if match:
            out.append(match.group("key").strip())
    return out


def _ini_keys(text: str) -> list[str]:
    out = []
    section = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped[0] in "#;":
            continue
        header = _INI_SECTION.match(line)
        if header:
            section = header.group("section").strip()
            continue
        match = _PROPERTIES_LINE.match(line)
        if match:
            key = match.group("key").strip()
            out.append(f"{section}.{key}" if section else key)
    return out


def _toml_keys(text: str) -> list[str]:
    out = []
    table = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        header = _TOML_TABLE.match(line)
        if header:
            table = header.group("table").strip()
            continue
        match = _TOML_KEY.match(line)
        if match:
            key = match.group("key").strip().strip("\"'")
            out.append(f"{table}.{key}" if table else key)
    return out


def _yaml_keys(text: str) -> list[str]:
    """Flatten a YAML mapping's key paths by indentation.

    A deliberately small reader: it follows indentation to build dotted paths
    and ignores sequence items, anchors, and multi-line scalars. It never looks
    at a value, which is what makes it safe to run over a file that holds one.
    """
    out: list[str] = []
    stack: list[tuple[int, str]] = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#") or raw.lstrip().startswith("-"):
            continue
        match = _YAML_LINE.match(raw)
        if not match:
            continue
        indent = len(match.group("indent"))
        key = match.group("key")
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if len(stack) >= MAX_DEPTH:
            continue
        path = ".".join([*(k for _i, k in stack), key])
        stack.append((indent, key))
        out.append(path)
    return out


def _json_keys(text: str) -> list[str]:
    try:
        obj = json.loads(text)
    except ValueError:
        return []
    out: list[str] = []

    def walk(node, prefix: str, depth: int) -> None:
        if depth > MAX_DEPTH or len(out) >= MAX_KEYS_PER_FILE:
            return
        if isinstance(node, dict):
            for key in node:
                path = f"{prefix}.{key}" if prefix else str(key)
                out.append(path)
                # Only the structure is walked. Scalars are not read.
                walk(node[key], path, depth + 1)

    walk(obj, "", 0)
    return out


def parse_config_file(path: str | Path, text: str):
    """A ParsedFile of configuration nodes for one file. Keys only.

    The symbols carry an EMPTY text field, which is what keeps the value out of
    the chunk table as well as out of the node.
    """
    from .model import ParsedFile

    path = str(path)
    parsed = ParsedFile(path=path, language="config")
    parsed.symbols.append(Symbol("module", Path(path).name, path, 1, 1, "", None))
    for key in extract_keys(path, text):
        parsed.symbols.append(
            Symbol(
                kind=KIND_CONFIG,
                name=key,
                qualified=f"{path}::config:{key}",
                start_line=0,
                end_line=0,
                # Empty by construction. A configuration value must never reach
                # the chunk table.
                text="",
                parent=path,
            )
        )
    return parsed


def find_bindings(text: str) -> set[str]:
    """Configuration keys the given source text reads."""
    keys: set[str] = set()
    for _name, pattern in BINDING_PATTERNS:
        for match in pattern.finditer(text):
            groups = match.groupdict()
            key = groups.get("key") or groups.get("key2")
            if key:
                keys.add(key.strip())
    return keys


def link_bindings(parsed_files, texts: dict[str, str]) -> list[tuple[str, str]]:
    """Pairs of (config node, code symbol) for every binding found.

    Resolved here rather than through the name-based resolver because BOTH
    endpoints are already known exactly: the configuration node by its key and
    the code symbol by the line the binding sits on. Passing them through name
    matching would turn two certain endpoints into a fan-out.
    """
    config_nodes: dict[str, list[str]] = {}
    for parsed in parsed_files:
        for symbol in parsed.symbols:
            if symbol.kind == KIND_CONFIG:
                config_nodes.setdefault(symbol.name, []).append(symbol.qualified)

    if not config_nodes:
        return []

    pairs: set[tuple[str, str]] = set()
    for parsed in parsed_files:
        if parsed.language == "config":
            continue
        text = texts.get(parsed.path)
        if not text:
            continue
        bound = find_bindings(text)
        if not bound:
            continue
        matched = bound & set(config_nodes)
        if not matched:
            continue
        owners = _owning_symbols(parsed, text, matched)
        for key, owner_qualified in owners:
            for config_qualified in config_nodes[key]:
                pairs.add((config_qualified, owner_qualified))
    return sorted(pairs)


def _owning_symbols(parsed, text: str, keys: set[str]) -> list[tuple[str, str]]:
    """Which symbol each binding sits inside, falling back to the module.

    A binding at module level belongs to the module, which is a real node, so
    the edge still lands somewhere addressable rather than being dropped.
    """
    spans = [
        (s.start_line, s.end_line, s.qualified)
        for s in parsed.symbols
        if s.kind != "module" and s.start_line > 0 and s.end_line >= s.start_line
    ]
    out: list[tuple[str, str]] = []
    lines = text.splitlines()
    for number, line in enumerate(lines, start=1):
        found = find_bindings(line)
        for key in sorted(found & keys):
            owner = parsed.path
            for start, end, qualified in spans:
                if start <= number <= end:
                    owner = qualified
                    break
            out.append((key, owner))
    return out

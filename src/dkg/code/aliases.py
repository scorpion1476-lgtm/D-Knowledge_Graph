"""Resolve aliased module imports through a project's compiler configuration.

A TypeScript or JavaScript project routinely declares its own module namespace:

    { "compilerOptions": { "baseUrl": "src", "paths": { "@app/*": ["app/*"] } } }

Given that, ``import { thing } from "@app/utils"`` names ``src/app/utils.ts``.
Without reading the configuration, the resolver sees the bare string ``@app/utils``,
finds no module whose stem matches, and the import stays unresolved. Every
aliased import in the repository is then invisible to impact analysis, which is
exactly the set of imports a large project uses most.

This module reads the configuration file, expands the alias patterns, and
resolves an import specifier to a real in-repository module path. It is
deterministic and reads only the named configuration file: the mapping comes
from the project's own declaration, not from a guess.

The reader is deliberately tolerant. A tsconfig is JSON with comments and
trailing commas permitted, which strict JSON rejects, so comments and trailing
commas are stripped before parsing. A file that still will not parse is reported
rather than raised: an unreadable configuration should degrade to the existing
name-based resolution, not fail an ingest.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# Config file names, in the order they are looked for. tsconfig first because a
# project with both is a TypeScript project whose jsconfig is usually vestigial.
CONFIG_NAMES = ("tsconfig.json", "jsconfig.json")

# Extensions tried when an import specifier carries none, and the index files
# tried when it names a directory. Ordered, so resolution is deterministic when
# a project has both ``utils.ts`` and ``utils.js``.
CANDIDATE_EXTENSIONS = (".ts", ".tsx", ".d.ts", ".js", ".jsx", ".mjs", ".cjs", ".json")
INDEX_STEMS = ("index",)

# A configuration file is small. This cap stops a hostile or mistaken file from
# becoming an unbounded read during ingest.
MAX_CONFIG_BYTES = 1_000_000


@dataclass(frozen=True)
class AliasConfig:
    """One project's declared module namespace."""

    config_path: str = ""
    base_url: str = ""
    paths: dict[str, tuple[str, ...]] = field(default_factory=dict)
    error: str = ""

    @property
    def present(self) -> bool:
        return bool(self.config_path) and not self.error

    @property
    def alias_count(self) -> int:
        return len(self.paths)

    def as_report(self) -> dict:
        """What the ingest result publishes about this configuration."""
        return {
            "config_path": self.config_path or None,
            "base_url": self.base_url or None,
            "alias_patterns": sorted(self.paths),
            "alias_count": self.alias_count,
            "error": self.error or None,
        }


def strip_jsonc(text: str) -> str:
    """Remove line comments, block comments, and trailing commas.

    Quote-aware: a ``//`` inside a string is part of the string, not the start
    of a comment, so a URL in a configuration value survives. Escapes inside
    strings are honoured.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == '"':
                    j += 1
                    break
                j += 1
            out.append(text[i:j])
            i = j
            continue
        if text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end == -1 else end
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        out.append(ch)
        i += 1
    joined = "".join(out)
    # Trailing commas before a closing brace or bracket.
    return re.sub(r",(\s*[}\]])", r"\1", joined)


def load_compiler_config(repo: str | Path, *, names: tuple[str, ...] = CONFIG_NAMES) -> AliasConfig:
    """Read the first compiler configuration found in ``repo``.

    An absent file returns an empty configuration, not an error: most
    repositories have none, and that is not a fault.
    """
    root = Path(repo)
    for name in names:
        candidate = root / name
        if not candidate.is_file():
            continue
        try:
            raw = candidate.read_bytes()
        except OSError as e:
            return AliasConfig(config_path=name, error=f"unreadable: {e}")
        if len(raw) > MAX_CONFIG_BYTES:
            return AliasConfig(
                config_path=name, error=f"larger than the {MAX_CONFIG_BYTES} byte cap"
            )
        try:
            obj = json.loads(strip_jsonc(raw.decode("utf-8", errors="replace")))
        except ValueError as e:
            # Reported, not raised. An unparseable config degrades to the
            # existing name-based resolution rather than failing the ingest.
            return AliasConfig(config_path=name, error=f"not valid JSON after comment strip: {e}")
        if not isinstance(obj, dict):
            return AliasConfig(config_path=name, error="top level is not an object")
        options = obj.get("compilerOptions")
        options = options if isinstance(options, dict) else {}
        base_url = str(options.get("baseUrl", "") or "").strip()
        raw_paths = options.get("paths")
        paths: dict[str, tuple[str, ...]] = {}
        if isinstance(raw_paths, dict):
            for pattern, targets in raw_paths.items():
                if isinstance(targets, str):
                    targets = [targets]
                if not isinstance(targets, list):
                    continue
                cleaned = tuple(str(t) for t in targets if isinstance(t, str) and str(t).strip())
                if cleaned:
                    paths[str(pattern)] = cleaned
        return AliasConfig(config_path=name, base_url=base_url, paths=paths)
    return AliasConfig()


def _normalise(path: str) -> str:
    """Collapse ``./`` and ``a/../b`` without touching the filesystem."""
    parts: list[str] = []
    for part in path.replace("\\", "/").split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _expansions(specifier: str, config: AliasConfig) -> list[str]:
    """Every path the alias table maps this specifier to, most specific first.

    TypeScript's rule is that the pattern with the longest literal prefix before
    the wildcard wins, so patterns are ordered that way rather than by dictionary
    order, and an exact (wildcard-free) pattern beats every wildcard one.
    """
    out: list[tuple[int, int, str]] = []
    for pattern, targets in config.paths.items():
        if "*" not in pattern:
            if pattern != specifier:
                continue
            for rank, target in enumerate(targets):
                out.append((0, rank, target))
            continue
        prefix, _, suffix = pattern.partition("*")
        if not specifier.startswith(prefix) or not specifier.endswith(suffix):
            continue
        middle = specifier[len(prefix) : len(specifier) - len(suffix) or None]
        for rank, target in enumerate(targets):
            # A negative first key sorts the longest literal prefix first.
            out.append((-len(prefix), rank, target.replace("*", middle)))
    out.sort()
    return [target for _p, _r, target in out]


def resolve_specifier(
    specifier: str, config: AliasConfig, known_modules: set[str]
) -> str | None:
    """Resolve an import specifier to a module path present in ``known_modules``.

    Returns None when the configuration does not map it or the target is not a
    module this repository actually has. Returning None rather than a guess is
    the point: a mapping that points nowhere is not a resolution.
    """
    if not specifier or not config.present:
        return None
    base = _normalise(config.base_url)
    candidates: list[str] = []
    for expanded in _expansions(specifier, config):
        candidates.append(_normalise(f"{base}/{expanded}" if base else expanded))
    if base and not candidates:
        # baseUrl alone makes a bare specifier relative to it, which is the
        # other half of what a compiler configuration declares.
        candidates.append(_normalise(f"{base}/{specifier}"))
    for candidate in candidates:
        hit = _match_module(candidate, known_modules)
        if hit is not None:
            return hit
    return None


def _match_module(candidate: str, known_modules: set[str]) -> str | None:
    """The real module path for a candidate, trying extensions and index files."""
    if candidate in known_modules:
        return candidate
    for ext in CANDIDATE_EXTENSIONS:
        with_ext = f"{candidate}{ext}"
        if with_ext in known_modules:
            return with_ext
    for stem in INDEX_STEMS:
        for ext in CANDIDATE_EXTENSIONS:
            index = f"{candidate}/{stem}{ext}"
            if index in known_modules:
                return index
    return None

"""Registry of repositories or corpora for the watch daemon.

Stored as a project-owned JSON file in the DKG home. Names are unique and paths
must exist. No network, no external dependency.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from ..core.errors import ValidationError

REGISTRY_FILENAME = "registry.json"


@dataclass(frozen=True)
class RepoEntry:
    name: str
    path: str


class Registry:
    """A persistent list of registered repositories."""

    def __init__(self, path: str | Path | None) -> None:
        # Optional because TransientRegistry below genuinely has no file. A
        # subclass that quietly held a Path it never wrote would be worse.
        self.path: Path | None = Path(path) if path is not None else None
        self._entries: dict[str, RepoEntry] = {}
        self._load()

    @classmethod
    def in_home(cls, home: str | Path) -> Registry:
        return cls(Path(home) / REGISTRY_FILENAME)

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            self._entries = {}
            return
        try:
            obj = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ValidationError(f"registry {self.path} is not valid JSON: {e}") from e
        entries: dict[str, RepoEntry] = {}
        for item in obj.get("repos", []):
            if not isinstance(item, dict) or "name" not in item or "path" not in item:
                raise ValidationError(f"registry {self.path}: each repo needs a name and a path")
            entries[str(item["name"])] = RepoEntry(str(item["name"]), str(item["path"]))
        self._entries = entries

    def _save(self) -> None:
        if self.path is None:  # pragma: no cover - TransientRegistry overrides this
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"repos": [asdict(e) for e in self._entries.values()]}
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def add(self, name: str, path: str | Path) -> RepoEntry:
        name = str(name).strip()
        if not name:
            raise ValidationError("repository name must be non-empty")
        if name in self._entries:
            raise ValidationError(f"repository {name!r} is already registered")
        resolved = Path(path).expanduser().resolve()
        if not resolved.exists():
            raise ValidationError(f"repository path does not exist: {resolved}")
        entry = RepoEntry(name, str(resolved))
        self._entries[name] = entry
        self._save()
        return entry

    def remove(self, name: str) -> None:
        if name not in self._entries:
            raise ValidationError(f"repository {name!r} is not registered")
        del self._entries[name]
        self._save()

    def get(self, name: str) -> RepoEntry | None:
        return self._entries.get(name)

    def list(self) -> list[RepoEntry]:
        return list(self._entries.values())

    def __len__(self) -> int:
        return len(self._entries)


class TransientRegistry(Registry):
    """A registry that exists only for the life of one process.

    Single-repository watching must not require registering anything: the whole
    point of it is to point the tool at one directory and have it keep up. But
    the daemon is the tested implementation of "notice a change, re-ingest only
    what moved, track health, stop cleanly", and writing a second watcher beside
    it would mean two code paths where one is exercised.

    So the daemon keeps taking a registry, and this is a registry holding one
    entry that is never written to disk. ``dkg watch`` leaves no registry file
    behind and does not disturb an existing one.
    """

    def __init__(self, entries: list[RepoEntry] | None = None) -> None:
        # Deliberately does not call super().__init__: there is no path to load.
        self.path = None
        self._entries: dict[str, RepoEntry] = {e.name: e for e in (entries or [])}

    @classmethod
    def for_repo(cls, path: str | Path, *, name: str | None = None) -> TransientRegistry:
        resolved = Path(path).expanduser().resolve()
        if not resolved.exists():
            raise ValidationError(f"repository path does not exist: {resolved}")
        if not resolved.is_dir():
            raise ValidationError(f"not a directory: {resolved}")
        return cls([RepoEntry(name or resolved.name, str(resolved))])

    def _load(self) -> None:  # pragma: no cover - never has a file to load
        self._entries = {}

    def _save(self) -> None:
        """Never persists. A transient registry that wrote a file would be the
        multi-repository registry wearing a different name, and would surprise
        anyone whose real registry it overwrote."""
        return

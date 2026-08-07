"""Portable backup and restore.

The backup is a tar.gz containing:
- ``graph.sqlite`` (a consistent copy created with SQLite backup API)
- ``audit.log`` (line-delimited JSON)
- ``evidence.ledger`` (line-delimited JSON)
- ``config.json`` (if present)
- ``manifest.json`` (SHA-256 of each file, app version, schema version)

Restore validates the manifest hashes before writing to the target home.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from dkg import __version__ as APP_VERSION

from ..core.db import Database
from ..core.errors import StorageError


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def make_backup(db: Database, out: Path) -> dict:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    home = db.path.parent
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        db_copy = tmp_path / "graph.sqlite"
        try:
            src = db._conn  # internal handle, safe here
            dst = sqlite3.connect(str(db_copy))
            with dst:
                src.backup(dst)
            dst.close()
        except sqlite3.Error as e:
            raise StorageError(f"database backup failed: {e}") from e

        manifest = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "app_version": APP_VERSION,
            "files": {},
        }
        include = [("graph.sqlite", db_copy)]
        for name in ("audit.log", "evidence.ledger", "config.json"):
            p = home / name
            if p.exists():
                include.append((name, p))
        for name, path in include:
            manifest["files"][name] = _sha256_of(path)
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

        with tarfile.open(str(out), "w:gz") as tf:
            for name, path in include + [("manifest.json", manifest_path)]:
                tf.add(str(path), arcname=name)

    return {"backup": str(out), "sha256": _sha256_of(out)}


def restore_backup(archive: Path, home: Path) -> dict:
    archive = Path(archive)
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    if not tarfile.is_tarfile(str(archive)):
        raise StorageError("backup archive is not a tar file")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with tarfile.open(str(archive), "r:gz") as tf:
            for member in tf.getmembers():
                # Refuse absolute paths and any traversal
                name = member.name
                if name.startswith("/") or ".." in Path(name).parts:
                    raise StorageError(f"unsafe path in backup: {name}")
                if member.issym() or member.islnk():
                    raise StorageError("symbolic links in backup are not permitted")
            tf.extractall(path=str(tmp_path))
        manifest_path = tmp_path / "manifest.json"
        if not manifest_path.exists():
            raise StorageError("backup manifest missing")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for name, expected in manifest.get("files", {}).items():
            actual = _sha256_of(tmp_path / name)
            if actual != expected:
                raise StorageError(f"backup file hash mismatch: {name}")
        # Copy in.
        for name in manifest["files"]:
            shutil.copy2(tmp_path / name, home / name)
    return {"restored_to": str(home)}

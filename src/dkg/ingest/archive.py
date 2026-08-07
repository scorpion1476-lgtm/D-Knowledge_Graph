"""Safe archive inspection with decompression caps.

Zip and tar archives are enumerated up to hard caps on total files, total
uncompressed bytes, per-file size, and total compression ratio. Anything that
looks like a zip-bomb, path-traversal entry, or symlink to an out-of-tree
location is refused.
"""

from __future__ import annotations

import os
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from ..core.errors import DecompressionError, SecurityError


@dataclass
class ArchiveEntry:
    name: str
    size: int
    compressed: int
    is_dir: bool


@dataclass
class ArchiveInventory:
    kind: str
    entries: list[ArchiveEntry]
    total_uncompressed: int
    total_compressed: int


def inspect_archive(
    path: Path,
    *,
    max_files: int = 4096,
    max_total_bytes: int = 500 * 1024 * 1024,
    max_per_file_bytes: int = 100 * 1024 * 1024,
    max_ratio: float = 200.0,
) -> ArchiveInventory:
    if not path.exists() or not path.is_file():
        raise DecompressionError(f"archive not found: {path}")
    if zipfile.is_zipfile(str(path)):
        return _inspect_zip(path, max_files, max_total_bytes, max_per_file_bytes, max_ratio)
    if tarfile.is_tarfile(str(path)):
        return _inspect_tar(path, max_files, max_total_bytes, max_per_file_bytes, max_ratio)
    raise DecompressionError("archive format not supported (only zip and tar)")


def _inspect_zip(
    path: Path, max_files: int, max_total: int, max_per: int, max_ratio: float
) -> ArchiveInventory:
    entries: list[ArchiveEntry] = []
    total_u = 0
    total_c = 0
    with zipfile.ZipFile(str(path)) as zf:
        infos = zf.infolist()
        if len(infos) > max_files:
            raise DecompressionError(f"archive has too many entries ({len(infos)} > {max_files})")
        for info in infos:
            _reject_traversal(info.filename)
            entries.append(
                ArchiveEntry(
                    name=info.filename,
                    size=info.file_size,
                    compressed=info.compress_size,
                    is_dir=info.is_dir(),
                )
            )
            if info.file_size > max_per:
                raise DecompressionError(f"entry too large: {info.filename} = {info.file_size}")
            total_u += info.file_size
            total_c += info.compress_size
            if total_u > max_total:
                raise DecompressionError(f"total uncompressed size exceeds cap: {total_u}")
    _check_ratio(total_u, total_c, max_ratio)
    return ArchiveInventory("zip", entries, total_u, total_c)


def _inspect_tar(
    path: Path, max_files: int, max_total: int, max_per: int, max_ratio: float
) -> ArchiveInventory:
    entries: list[ArchiveEntry] = []
    total_u = 0
    with tarfile.open(str(path)) as tf:
        members = tf.getmembers()
        if len(members) > max_files:
            raise DecompressionError(f"archive has too many entries ({len(members)} > {max_files})")
        for m in members:
            _reject_traversal(m.name)
            if m.issym() or m.islnk():
                raise SecurityError(f"symlinks are not permitted: {m.name}")
            entries.append(
                ArchiveEntry(name=m.name, size=m.size, compressed=m.size, is_dir=m.isdir())
            )
            if m.size > max_per:
                raise DecompressionError(f"entry too large: {m.name} = {m.size}")
            total_u += m.size
            if total_u > max_total:
                raise DecompressionError(f"total uncompressed size exceeds cap: {total_u}")
    total_c = max(1, os.path.getsize(path))
    _check_ratio(total_u, total_c, max_ratio)
    return ArchiveInventory("tar", entries, total_u, total_c)


def _reject_traversal(name: str) -> None:
    if not name or ".." in Path(name).parts or name.startswith(("/", "\\")):
        raise SecurityError(f"archive entry has unsafe path: {name!r}")


def _check_ratio(total_u: int, total_c: int, max_ratio: float) -> None:
    total_c = max(1, total_c)
    ratio = total_u / total_c
    if ratio > max_ratio:
        raise DecompressionError(
            f"compression ratio {ratio:.1f} exceeds cap {max_ratio}; possible bomb"
        )

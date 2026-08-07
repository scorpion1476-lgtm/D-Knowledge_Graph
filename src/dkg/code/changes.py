"""Version-control-based incremental change detection for the code plane.

Lists versioned source files and compares their content hashes against the
hashes previously stored in the graph, so only changed files are re-parsed. Two
working-copy kinds are supported through ONE incremental path: git, and
Subversion. The path is shared deliberately. Change detection is "which
versioned files does this working copy have, and which of their hashes moved",
and the only part that differs between the two systems is how the file list is
obtained; everything downstream is identical, so a Subversion checkout gets
exactly the incremental behaviour a git clone gets rather than an approximation
of it.

Both tools are invoked as local subprocesses: list arguments, no shell, a
bounded timeout, and no network. Neither is a Python dependency, and a working
copy whose tool is absent is capability-detected and falls back to a directory
walk rather than failing.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from ..core.errors import IngestError

_GIT_TIMEOUT = 60
_SVN_TIMEOUT = 120


def _git(repo: Path, *args: str) -> str:
    cmd = ["git", "-C", str(repo), *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        raise IngestError(f"git failed to run: {e}") from e
    if proc.returncode != 0:
        raise IngestError(f"git {' '.join(args)} failed (rc={proc.returncode}): {proc.stderr.strip()[:200]}")
    return proc.stdout


def is_git_repo(repo: Path) -> bool:
    try:
        return _git(Path(repo), "rev-parse", "--is-inside-work-tree").strip() == "true"
    except IngestError:
        return False


# An extension-less file is how an executable script is normally written, so a
# bounded number of them are read far enough to check for an interpreter line
# rather than being skipped because they carry no extension.
_MAX_SHEBANG_CANDIDATES = 5000


def list_tracked_files(repo: Path, *, exts: set[str], include_scripts: bool = True) -> list[str]:
    from .config_keys import is_config_file

    out = _git(Path(repo), "ls-files")
    files: list[str] = []
    candidates: list[str] = []
    for line in out.splitlines():
        rel = line.strip()
        if not rel:
            continue
        # Externalised configuration is collected by NAME as well as extension,
        # because .env has no suffix and would otherwise never be seen.
        if Path(rel).suffix.lower() in exts or is_config_file(rel):
            files.append(rel)
        elif include_scripts and not Path(rel).suffix:
            candidates.append(rel)
    if include_scripts:
        from .parser import language_for

        # Bounded: a repository of data files with no extensions must not turn
        # this into an unbounded read of every one of them.
        for rel in candidates[:_MAX_SHEBANG_CANDIDATES]:
            full = Path(repo) / rel
            if full.is_file() and language_for(full) is not None:
                files.append(rel)
    return sorted(files)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# -- Subversion ---------------------------------------------------------------


def _svn(repo: Path, *args: str) -> str:
    cmd = ["svn", *args, str(repo)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_SVN_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        raise IngestError(f"svn failed to run: {e}") from e
    if proc.returncode != 0:
        raise IngestError(
            f"svn {' '.join(args)} failed (rc={proc.returncode}): {proc.stderr.strip()[:200]}"
        )
    return proc.stdout


def is_svn_checkout(repo: Path | str) -> bool:
    """Whether this directory is a Subversion working copy.

    The administrative directory is checked before the binary is invoked, so a
    machine with no ``svn`` installed pays nothing for the question.
    """
    return (Path(repo) / ".svn").is_dir()


def svn_available() -> bool:
    """Whether an ``svn`` binary can be invoked at all."""
    import shutil

    return shutil.which("svn") is not None


def list_versioned_files_svn(repo: Path, *, exts: set[str], include_scripts: bool = True) -> list[str]:
    """Every versioned file in a Subversion working copy, repository-relative.

    ``svn status -v --xml`` is a purely local operation on a working copy: with
    ``-v`` it reports every versioned entry, not only the modified ones, and it
    contacts no server. The XML form is parsed rather than the column layout,
    because the text columns are width-dependent on the author name and would
    misparse for a long one.
    """
    from xml.etree import ElementTree  # noqa: S405

    from .config_keys import is_config_file

    out = _svn(Path(repo), "status", "-v", "--xml")
    try:
        # Parsed with the standard library's ElementTree, which does not resolve
        # external entities and rejects internal general entity definitions.
        # The input is the output of a local binary this process invoked.
        root = ElementTree.fromstring(out)  # noqa: S314
    except ElementTree.ParseError as e:
        raise IngestError(f"svn status returned XML this build could not parse: {e}") from e

    repo_path = Path(repo)
    files: list[str] = []
    candidates: list[str] = []
    for entry in root.iter("entry"):
        raw = entry.get("path")
        if not raw:
            continue
        status = entry.find("wc-status")
        # An unversioned or deleted entry is not part of the working copy's
        # content, so it is not offered to the parser.
        if status is not None and status.get("item") in ("unversioned", "deleted", "missing", "ignored"):
            continue
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = repo_path / raw
        if not candidate.is_file():
            continue
        try:
            rel = str(candidate.resolve().relative_to(repo_path.resolve()))
        except ValueError:
            continue
        if Path(rel).suffix.lower() in exts or is_config_file(rel):
            files.append(rel)
        elif include_scripts and not Path(rel).suffix:
            candidates.append(rel)
    if include_scripts:
        from .parser import language_for

        for rel in candidates[:_MAX_SHEBANG_CANDIDATES]:
            full = repo_path / rel
            if full.is_file() and language_for(full) is not None:
                files.append(rel)
    return sorted(set(files))


# -- git submodules -----------------------------------------------------------


def submodule_paths(repo: Path) -> list[str]:
    """Repository-relative paths of the initialised git submodules.

    Read from ``git submodule status``, which reports what is actually checked
    out rather than what ``.gitmodules`` merely declares. An uninitialised
    submodule has no content to collect, so listing it would promise files that
    are not there.
    """
    try:
        out = _git(Path(repo), "submodule", "status")
    except IngestError:
        return []
    paths: list[str] = []
    for line in out.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # "<status-char><sha> <path> (<describe>)"; a leading '-' means the
        # submodule is not initialised and has nothing checked out.
        if stripped.startswith("-"):
            continue
        parts = stripped.split()
        if len(parts) >= 2:
            paths.append(parts[1])
    return sorted(set(paths))


def list_submodule_files(repo: Path, *, exts: set[str]) -> tuple[list[str], list[str]]:
    """Files inside each initialised submodule, prefixed with its path.

    Returns (files, submodules_read). A submodule that is not itself a git
    working tree is skipped rather than walked, because collecting it would mean
    guessing at what is versioned in it.
    """
    repo = Path(repo)
    collected: list[str] = []
    read: list[str] = []
    for sub in submodule_paths(repo):
        sub_root = repo / sub
        if not is_git_repo(sub_root):
            continue
        read.append(sub)
        for rel in list_tracked_files(sub_root, exts=exts):
            collected.append(f"{sub}/{rel}")
    return sorted(set(collected)), read


def detect_changes(
    repo: Path,
    stored_hashes: dict[str, str],
    *,
    exts: set[str],
    include_submodules: bool = False,
    ignore_rules=None,
) -> dict:
    """Compare versioned files against previously stored hashes.

    Returns changed (new or modified), removed (previously ingested but no longer
    versioned), the unchanged count, and the full versioned list. Works over a
    git clone or a Subversion working copy through the same comparison; the only
    difference is which lister produced the file list.

    ``include_submodules`` is off by default: turning it on changes the shape of
    an ingest, and that must be asked for rather than inferred.
    """
    repo = Path(repo)
    if is_git_repo(repo):
        vcs = "git"
        tracked = list_tracked_files(repo, exts=exts)
        submodules: list[str] = []
        if include_submodules:
            sub_files, submodules = list_submodule_files(repo, exts=exts)
            tracked = sorted(set(tracked) | set(sub_files))
    elif is_svn_checkout(repo):
        vcs = "svn"
        tracked = list_versioned_files_svn(repo, exts=exts)
        submodules = []
    else:
        raise IngestError(f"{repo} is neither a git clone nor a Subversion working copy")

    excluded: list[str] = []
    if ignore_rules is not None and getattr(ignore_rules, "present", False):
        tracked, excluded = ignore_rules.filter(tracked)

    tracked_set = set(tracked)
    changed: list[str] = []
    unchanged = 0
    for rel in tracked:
        if stored_hashes.get(rel) != file_sha256(repo / rel):
            changed.append(rel)
        else:
            unchanged += 1
    # A path the ignore file now excludes was previously ingested and must be
    # dropped, not merely skipped, or the graph would keep stale symbols for it.
    removed = [p for p in stored_hashes if p not in tracked_set]
    return {
        "changed": changed,
        "removed": removed,
        "unchanged": unchanged,
        "tracked": tracked,
        "vcs": vcs,
        "submodules": submodules,
        "excluded": excluded,
    }

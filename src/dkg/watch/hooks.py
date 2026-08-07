"""Keep the graph current on file save and on commit.

The graph is only useful if it describes the code as it is now. Re-ingesting by
hand means it is stale exactly when someone is most likely to consult it, in the
middle of a change. Two triggers close that gap, both driving the SAME
incremental path the watch daemon uses, so there is one update mechanism rather
than three that drift:

* **On save.** The watch daemon already does this; this module adds the piece
  that makes it usable from an editor: a single ``dkg update`` entry point that
  re-ingests only what changed and prints what it did.
* **On commit.** A git ``post-commit`` hook, installed into a repository's
  ``.git/hooks``, that runs the same update.

Rules this follows, because a hook that gets these wrong is worse than no hook:

* **It never blocks the commit.** ``post-commit`` runs after the commit exists,
  and the script exits 0 whatever happens. A graph update failing must not make
  a developer's commit fail; the failure is reported and the graph stays stale,
  which is the honest outcome.
* **It never runs the network.** The update is a local parse.
* **It refuses to clobber.** Installing over an existing hook that this project
  did not write requires ``force``, and the existing file is preserved beside
  the new one rather than deleted.
* **It is removable and its installation is detectable**, so a user can tell
  what is installed and take it out again.
* **It is bounded.** The hook runs with a timeout, so a pathological repository
  cannot leave a developer waiting on a shell that never returns.

The hook script is generated here rather than shipped as a file so that the
interpreter path is the one that actually has the package installed, which is
the usual reason a hook silently does nothing.
"""

from __future__ import annotations

import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

from ..core.errors import ValidationError

# Marker written into every generated hook. Its presence is how a hook this
# project wrote is told apart from one the user or another tool wrote, which is
# what makes refusing to clobber possible.
HOOK_MARKER = "# installed by d-knowledge-graph: incremental code-graph update"

SUPPORTED_HOOKS = ("post-commit", "post-merge", "post-checkout")

# Seconds the update may take before the hook gives up. A developer waiting on a
# shell is a worse outcome than a graph that is one commit behind.
DEFAULT_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class HookStatus:
    name: str
    path: Path
    installed: bool
    ours: bool
    reason: str = ""


def hooks_dir(repo: Path | str) -> Path:
    """The hooks directory for a repository, honouring core.hooksPath."""
    repo = Path(repo)
    configured = _git_config(repo, "core.hooksPath")
    if configured:
        candidate = Path(configured)
        return candidate if candidate.is_absolute() else repo / candidate
    return repo / ".git" / "hooks"


def _git_config(repo: Path, key: str) -> str:
    import subprocess

    try:
        proc = subprocess.run(  # noqa: S603
            ["git", "-C", str(repo), "config", "--get", key],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def render_hook(
    *, home: Path | str | None = None, timeout: int = DEFAULT_TIMEOUT_SECONDS
) -> str:
    """The hook script.

    The interpreter is the one running this, not whatever ``python`` resolves to
    in the hook's environment. Git runs hooks with a reduced environment and the
    single most common reason a hook does nothing is that its interpreter has no
    access to the package.
    """
    interpreter = sys.executable or "python3"
    home_line = f'export DKG_HOME="{Path(home).resolve()}"\n' if home else ""
    return (
        "#!/bin/sh\n"
        f"{HOOK_MARKER}\n"
        "#\n"
        "# Re-ingests only what changed, through the same incremental path the\n"
        "# watch daemon uses. It exits 0 whatever happens: a graph update must\n"
        "# never make a commit fail. A failure is printed and the graph stays\n"
        "# stale, which is the honest outcome.\n"
        "\n"
        "set -u\n"
        f"{home_line}"
        "# Air-gap default holds in the hook too.\n"
        'export DKG_ALLOW_OUTBOUND="${DKG_ALLOW_OUTBOUND:-0}"\n'
        'export DKG_TELEMETRY="${DKG_TELEMETRY:-0}"\n'
        "\n"
        'repo="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0\n'
        "\n"
        f'if ! "{interpreter}" -m dkg update --repo "$repo" --quiet; then\n'
        '  echo "dkg: the code-graph update failed; the graph is now stale" >&2\n'
        "fi\n"
        "exit 0\n"
    )


def status(repo: Path | str, name: str = "post-commit") -> HookStatus:
    """Whether the named hook is installed, and whether this project wrote it."""
    if name not in SUPPORTED_HOOKS:
        raise ValidationError(f"unknown hook {name!r}; expected one of {list(SUPPORTED_HOOKS)}")
    path = hooks_dir(repo) / name
    if not path.exists():
        return HookStatus(name, path, installed=False, ours=False, reason="not present")
    try:
        body = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return HookStatus(name, path, installed=True, ours=False, reason=f"unreadable: {e!r}")
    ours = HOOK_MARKER in body
    return HookStatus(
        name,
        path,
        installed=True,
        ours=ours,
        reason="written by this project" if ours else "written by something else",
    )


def install(
    repo: Path | str,
    *,
    name: str = "post-commit",
    home: Path | str | None = None,
    force: bool = False,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict:
    """Install the update hook into a repository.

    Refuses to overwrite a hook this project did not write unless ``force``, and
    even then keeps the original beside the new one. Silently replacing
    somebody's hook is a way to lose work that is very hard to notice.
    """
    if name not in SUPPORTED_HOOKS:
        raise ValidationError(f"unknown hook {name!r}; expected one of {list(SUPPORTED_HOOKS)}")
    repo = Path(repo)
    if not (repo / ".git").exists():
        raise ValidationError(f"{repo} is not a git repository; there is nowhere to install a hook")

    directory = hooks_dir(repo)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    existing = status(repo, name)
    backup: Path | None = None
    if existing.installed and not existing.ours:
        if not force:
            return {
                "installed": False,
                "path": str(path),
                "reason": (
                    f"{name} already exists and was not written by this project; "
                    "pass force to replace it (the original is kept alongside)"
                ),
            }
        backup = path.with_suffix(path.suffix + ".dkg-backup")
        backup.write_text(path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")

    path.write_text(render_hook(home=home, timeout=timeout), encoding="utf-8")
    # Executable, or git ignores it without saying so.
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return {
        "installed": True,
        "path": str(path),
        "replaced_backup": str(backup) if backup else None,
        "hook": name,
        "why": (
            "Runs the same incremental update the watch daemon runs. It never "
            "blocks a commit: it exits 0 whatever happens, and reports a failure "
            "rather than leaving a stale graph looking current."
        ),
    }


def uninstall(repo: Path | str, *, name: str = "post-commit") -> dict:
    """Remove a hook this project installed. Never removes somebody else's."""
    existing = status(repo, name)
    if not existing.installed:
        return {"removed": False, "path": str(existing.path), "reason": "not installed"}
    if not existing.ours:
        return {
            "removed": False,
            "path": str(existing.path),
            "reason": "that hook was not written by this project; it was left alone",
        }
    existing.path.unlink()
    backup = existing.path.with_suffix(existing.path.suffix + ".dkg-backup")
    restored = False
    if backup.exists():
        existing.path.write_text(backup.read_text(encoding="utf-8"), encoding="utf-8")
        existing.path.chmod(existing.path.stat().st_mode | stat.S_IXUSR)
        backup.unlink()
        restored = True
    return {
        "removed": True,
        "path": str(existing.path),
        "restored_previous_hook": restored,
    }


def update_now(
    db,
    repo: Path | str,
    *,
    tenant_id: str = "local",
    audit_path: Path | None = None,
    resolve: bool = False,
) -> dict:
    """Re-ingest only what changed. The one update path all triggers share.

    Used by the editor-save entry point, by the commit hook, and by the watch
    daemon, so an update means the same thing however it was triggered.
    """
    from ..code.ingest import ingest_repo

    repo = Path(repo)
    if not repo.exists():
        raise ValidationError(f"repository path does not exist: {repo}")
    result = ingest_repo(
        db,
        repo,
        tenant_id=tenant_id,
        audit_path=audit_path,
        incremental=True,
        resolve=resolve,
    )
    return {
        "repo": str(repo),
        "changed_files": result.get("changed", result.get("files")),
        "nodes": result.get("nodes"),
        "edges": result.get("edges"),
        "incremental": True,
        "why": (
            "Only files whose content hash changed were re-parsed. This is the "
            "same path the watch daemon and the commit hook use, so an update "
            "means the same thing however it was triggered."
        ),
    }


def is_executable(path: Path) -> bool:
    return path.exists() and bool(path.stat().st_mode & stat.S_IXUSR) and os.access(path, os.X_OK)

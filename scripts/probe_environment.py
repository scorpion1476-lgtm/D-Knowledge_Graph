#!/usr/bin/env python3
"""Environment diagnostic: tell an install failure apart from an environment failure.

One pasteable JSON report that answers, in a single output, every question that
otherwise takes a guess:

  interpreter          which Python is running this, on which platform
  extras               which declared optional extras are actually installed
  binaries             which external tools the two planes reach for are on PATH
  models               which pre-staged model directories exist on disk
  socket_bind_loopback whether this shell may bind a loopback port at all
  network_egress_pypi  whether the package index is reachable from this shell

The pairing is the point. "pip install failed" and "this shell cannot reach the
package index" look identical from the inside; so do "the code extra is not
installed" and "tree-sitter is installed but no grammar loaded". Reporting the
extras and the reachability check in the same document separates them.

NETWORK DISCLOSURE. This script makes exactly one outbound request, to
https://pypi.org/pypi/pip/json, with a three second timeout, and it reports the
URL it used in the report itself. That is a deliberate, disclosed opt-out probe
in a diagnostic tool; it is never on a product runtime path, and the product's
air-gap default is unaffected. Pass --offline to skip it, in which case the
report records that the check was not attempted rather than recording a failure.

Nothing here downloads a model, installs a package, or starts a server. Every
binary lookup is a PATH lookup; no external process is executed.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import socket
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PYPI_PROBE_URL = "https://pypi.org/pypi/pip/json"
PYPI_PROBE_TIMEOUT = 3

# External binaries the two planes reach for, grouped by what a missing one
# actually costs. Nothing here is required: every one of them is capability
# detected and the core installs and passes its suite with none of them present.
BINARY_GROUPS: dict[str, tuple[tuple[str, str], ...]] = {
    "development": (
        ("git", "incremental re-ingest, co-change, and churn signals"),
        ("gh", "the forge client used by the publish helper"),
        ("pip-audit", "the dependency vulnerability audit run in CI"),
        ("docker", "container validation (never invoked by this script)"),
        ("podman", "container validation (never invoked by this script)"),
    ),
    "media": (
        ("tesseract", "image and keyframe OCR"),
        ("ffprobe", "video metadata"),
        ("ffmpeg", "keyframe and scene detection"),
        ("heif-convert", "HEIC decode"),
        ("heif-dec", "HEIC decode"),
        ("magick", "HEIC decode fallback"),
        ("convert", "HEIC decode fallback, legacy ImageMagick"),
        ("whisper-cli", "speech to text, whisper.cpp style CLI"),
        ("whisper-cpp", "speech to text, whisper.cpp style CLI"),
    ),
    "code": (
        ("node", "required by both language servers"),
        ("pyright-langserver", "type-aware resolution for Python"),
        ("typescript-language-server", "type-aware resolution for JavaScript"),
    ),
}

# Where pre-staged weights live when scripts/prestage_models.py has run, and the
# environment variable that overrides each. None of these is downloaded at
# runtime; an absent one degrades to the documented fallback.
MODEL_LOCATIONS: tuple[tuple[str, str, str, str], ...] = (
    (
        "embeddings",
        "models/embeddings/potion-base-8M",
        "DKG_EMBEDDING_MODEL",
        "falls back to the built-in hashing adapter",
    ),
    (
        "reranker",
        "models/reranker",
        "DKG_RERANKER_CACHE",
        "hybrid degrades to keyword plus FTS rank fusion",
    ),
    (
        "media-detect",
        "models/media-detect",
        "DKG_DETECT_CACHE",
        "zero-shot image tagging is unavailable",
    ),
)

# The language servers are staged with npm under this gitignored directory.
STAGED_LSP_BIN = ROOT / "tools" / "lsp" / "node_modules" / ".bin"


def _probe_socket_bind() -> dict:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return {"ok": True, "port": port, "error": None}
    except Exception as e:
        try:
            s.close()
        except Exception:
            pass
        return {"ok": False, "port": None, "error": repr(e)}


def _probe_network(*, attempt: bool) -> dict:
    """Reach for the package index, and say plainly what was contacted.

    Not attempted is reported as its own state. Recording a skipped check as a
    failure would be the same lie in the other direction: it would read as "the
    index is unreachable" when nothing was ever asked.
    """
    if not attempt:
        return {
            "attempted": False,
            "url": PYPI_PROBE_URL,
            "ok": None,
            "status": None,
            "error": None,
            "note": "skipped by --offline; no outbound request was made",
        }
    try:
        with urllib.request.urlopen(PYPI_PROBE_URL, timeout=PYPI_PROBE_TIMEOUT) as r:
            return {
                "attempted": True,
                "url": PYPI_PROBE_URL,
                "ok": True,
                "status": r.status,
                "error": None,
                "note": None,
            }
    except Exception as e:
        return {
            "attempted": True,
            "url": PYPI_PROBE_URL,
            "ok": False,
            "status": None,
            "error": repr(e),
            "note": None,
        }


def _requirement_name(spec: str) -> str:
    """The distribution name out of a requirement string.

    Deliberately small: the declarations in this project are plain
    ``name>=floor`` or ``name==pin`` forms, with no extras or markers, and a
    full requirement parser would pull in a dependency this script must not have.
    """
    return re.split(r"[<>=!~;\[\s]", spec.strip(), maxsplit=1)[0].strip()


def _declared_extras() -> tuple[dict[str, list[str]], str]:
    """Every optional extra this project declares, and where the list came from.

    pyproject is the declaration of record. tomllib is 3.11 and later, and this
    project supports 3.10, so there is a documented text fallback for the older
    interpreter. Both paths fail loud rather than returning an empty mapping: a
    diagnostic that silently reports "no extras declared" would send a reader
    looking for an install problem that does not exist.
    """
    pyproject = ROOT / "pyproject.toml"
    if not pyproject.is_file():
        raise SystemExit(f"probe: no pyproject.toml at {pyproject}")
    try:
        import tomllib
    except ModuleNotFoundError:
        extras = _extras_from_text(pyproject.read_text(encoding="utf-8"))
        source = "pyproject.toml (text fallback, no tomllib before Python 3.11)"
    else:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        extras = {
            name: list(reqs)
            for name, reqs in (data.get("project", {}).get("optional-dependencies", {})).items()
        }
        source = "pyproject.toml [project.optional-dependencies]"
    if not extras:
        raise SystemExit("probe: pyproject declares no optional extras; refusing to report none")
    return extras, source


def _extras_from_text(text: str) -> dict[str, list[str]]:
    """Parse [project.optional-dependencies] without tomllib.

    Handles exactly the shape this project's pyproject uses: one
    ``name = ["a", "b"]`` assignment inside the table, with the list possibly
    wrapped across lines. Comment lines are dropped before anything else: this
    file documents every extra with a long comment block above it, and a first
    version of this parser folded those comments into the extra's own name.
    """
    lines = text.splitlines()
    try:
        start = next(
            i for i, line in enumerate(lines) if line.strip() == "[project.optional-dependencies]"
        )
    except StopIteration:
        return {}
    out: dict[str, list[str]] = {}
    buffer = ""
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not buffer and stripped.startswith("[") and stripped.endswith("]") and "=" not in stripped:
            break
        buffer = f"{buffer} {stripped}" if buffer else stripped
        if "[" in buffer and buffer.count("[") == buffer.count("]"):
            name, sep, raw = buffer.partition("=")
            if sep:
                out[name.strip().strip('"')] = re.findall(r'"([^"]+)"', raw)
            buffer = ""
    return out


def _installed_version(dist: str) -> str | None:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(dist)
    except PackageNotFoundError:
        return None
    except Exception:
        return None


def _probe_extras() -> dict:
    """Which declared extras are installed here, judged by their requirements.

    An extra is installed when every distribution it requires resolves. Each
    requirement is reported individually so a half-installed extra names the
    missing piece instead of just reading false.
    """
    declared, source = _declared_extras()
    extras: dict[str, dict] = {}
    for name in sorted(declared):
        requirements = {}
        for spec in declared[name]:
            dist = _requirement_name(spec)
            requirements[dist] = {"requirement": spec, "installed_version": _installed_version(dist)}
        missing = sorted(d for d, r in requirements.items() if r["installed_version"] is None)
        extras[name] = {
            # An extra that declares no Python requirement (its capability is an
            # external binary) is trivially satisfied. Saying so beside the flag
            # stops "installed: true" from reading as "the tool is present".
            "installed": not missing,
            "declares_no_python_requirement": not requirements,
            "missing": missing,
            "requirements": requirements,
        }
    return {
        "source": source,
        "declared": sorted(declared),
        "installed": sorted(n for n, e in extras.items() if e["installed"]),
        "not_installed": sorted(n for n, e in extras.items() if not e["installed"]),
        "detail": extras,
    }


def _probe_binaries() -> dict:
    """PATH lookups only. No external process is executed by this script."""
    groups: dict[str, dict] = {}
    for group, entries in BINARY_GROUPS.items():
        found: dict[str, dict] = {}
        for name, why in entries:
            path = shutil.which(name)
            staged = STAGED_LSP_BIN / name
            if path is None and staged.exists():
                path = str(staged)
            found[name] = {"path": path, "needed_for": why}
        groups[group] = {
            "found": sorted(n for n, v in found.items() if v["path"]),
            "absent": sorted(n for n, v in found.items() if not v["path"]),
            "detail": found,
        }
    return groups


def _probe_models() -> dict:
    """Which pre-staged model directories exist. Nothing is loaded or fetched."""
    out: dict[str, dict] = {}
    for capability, relative, env_var, consequence in MODEL_LOCATIONS:
        override = os.environ.get(env_var, "").strip() or None
        path = Path(override) if override else ROOT / relative
        present = path.exists()
        files = 0
        if present and path.is_dir():
            files = sum(1 for p in path.rglob("*") if p.is_file())
        out[capability] = {
            "staged": present,
            "path": str(path),
            "from_env": bool(override),
            "env_var": env_var,
            "files": files,
            "if_absent": consequence,
        }
    asr_model = os.environ.get("DKG_ASR_MODEL", "").strip() or None
    out["asr"] = {
        "staged": bool(asr_model) and Path(asr_model).exists(),
        "path": asr_model,
        "from_env": bool(asr_model),
        "env_var": "DKG_ASR_MODEL",
        "files": 0,
        "if_absent": "speech to text is unavailable and is reported not measured",
    }
    provenance = ROOT / "docs" / "model_provenance.json"
    out["provenance_record"] = {
        "path": "docs/model_provenance.json",
        "present": provenance.is_file(),
    }
    return out


def build_report(*, attempt_network: bool = True) -> dict:
    """The whole diagnostic as one JSON-serialisable mapping."""
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "python": sys.version.split()[0],
        "interpreter": {
            "executable": sys.executable,
            "version": sys.version.split()[0],
            "implementation": platform.python_implementation(),
            "prefix": sys.prefix,
            "in_virtualenv": sys.prefix != sys.base_prefix,
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "project": {
            "root": str(ROOT),
            "installed_version": _installed_version("d-knowledge-graph"),
        },
        "extras": _probe_extras(),
        "binaries": _probe_binaries(),
        "models": _probe_models(),
        "socket_bind_loopback": _probe_socket_bind(),
        "network_egress_pypi": _probe_network(attempt=attempt_network),
        "disclosure": (
            "The only outbound request this script can make is to "
            f"{PYPI_PROBE_URL}. It is a disclosed diagnostic probe, never a "
            "product runtime path, and --offline skips it."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="probe_environment.py",
        description="Report the interpreter, extras, external binaries, staged models, "
        "and package-index reachability as one JSON document.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help=f"skip the package-index reachability probe (otherwise contacts {PYPI_PROBE_URL})",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="write the report here instead of test-evidence/environment_probe.json",
    )
    args = parser.parse_args(argv)

    report = build_report(attempt_network=not args.offline)
    out = Path(args.out) if args.out else ROOT / "test-evidence" / "environment_probe.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, indent=2, sort_keys=True)
    out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

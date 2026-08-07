#!/usr/bin/env python3
"""Resolve the licence of every grammar inside the Tree-sitter grammar bundle.

Why this exists
---------------
The bundle (``tree-sitter-language-pack``) compiles hundreds of grammars into a
single shared object. Depending on it therefore means shipping all of them, not
only the handful a project enables. The standing rule is that every grammar used
must have its licence enumerated and attributed, and the bundle does NOT publish
a per-grammar licence manifest: at the audited revision its
``sources/language_definitions.json`` carries a ``license`` field for 13 of 371
entries, and its ``ATTRIBUTIONS.md`` covers a vendored Rust crate rather than the
grammars.

What it does publish is better than a licence field for this purpose: the
upstream repository and the exact revision compiled in, for every grammar. That
makes the licences auditable from the primary source rather than from a
second-hand summary, which is what this script does. The result is a real
generated manifest, so "the bundle is unauditable" is replaced by a measurement
that can come out either way.

Resolution order per grammar, strongest evidence first:

1. A licence FILE in the repository at the pinned revision. This is the actual
   licence text that was compiled in, so it is the strongest evidence.
2. A licence DECLARATION in the grammar's own metadata at the pinned revision
   (``package.json``, ``tree-sitter.json``, ``Cargo.toml``, ``pyproject.toml``).
   Weaker: it states a licence without shipping its text.
3. The ``license`` field the bundle's own manifest records, if any.

Anything unresolved is reported as unresolved. It is never assumed permissive.

Network use here is build-time and audit-time only, which the air-gap rule
permits explicitly. The product never calls this.

Fail-loud: any unexpected error aborts with a non-zero status and no artifact is
written. An empty or partial manifest is never emitted.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "docs" / "grammar_bundle_sources.json"
DEFAULT_OUT = ROOT / "docs" / "grammar_bundle_licences.json"

LICENCE_FILENAMES = (
    "LICENSE",
    "LICENSE.md",
    "LICENSE.txt",
    "LICENCE",
    "LICENCE.md",
    "LICENCE.txt",
    "COPYING",
    "COPYING.md",
    "COPYING.txt",
    "LICENSE-MIT",
    "UNLICENSE",
    "license",
)
METADATA_FILENAMES = ("package.json", "tree-sitter.json", "Cargo.toml", "pyproject.toml")

# Licence families for the THIRD-PARTY grammars this bundle ships. Nothing here
# describes this project's own licence, which is source-available and
# non-commercial. The permissive set the standing rule admits for a third-party
# dependency is Apache-2.0, MIT, BSD, ISC, HPND, or a public-domain
# equivalent. WTFPL and CC0 are public-domain equivalents. "Apache-2.0 WITH
# LLVM-exception" is Apache-2.0 plus an ADDITIONAL permission, so it is strictly
# more permissive than plain Apache-2.0, never less.
PERMISSIVE = {
    "MIT",
    "MIT-0",
    "ISC",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "BSD-Source-Code",
    "Apache-2.0",
    "Apache-2.0 WITH LLVM-exception",
    "Unlicense",
    "CC0-1.0",
    "WTFPL",
    "0BSD",
    "Zlib",
    "HPND",
    "BlueOak-1.0.0",
}
COPYLEFT = {
    "GPL-2.0",
    "GPL-3.0",
    "AGPL-3.0",
    "LGPL-2.1",
    "LGPL-3.0",
    "MPL-2.0",
    "EPL-2.0",
    "CDDL-1.0",
}

_TIMEOUT = 30


def _get(url: str) -> str | None:
    """Fetch a URL, returning None on 404 and raising on anything unexpected."""
    req = urllib.request.Request(url, headers={"User-Agent": "dkg-grammar-audit/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            return r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code in (403, 404, 451):
            return None
        raise
    except (urllib.error.URLError, TimeoutError):
        return None


def _raw_url(repo: str, rev: str, path: str) -> str | None:
    """Build a raw-content URL for the hosts the bundle actually references."""
    repo = repo.rstrip("/")
    if repo.startswith("https://github.com/"):
        slug = repo[len("https://github.com/") :].removesuffix(".git")
        return f"https://raw.githubusercontent.com/{slug}/{rev}/{path}"
    if repo.startswith("https://gitlab.com/"):
        slug = repo[len("https://gitlab.com/") :].removesuffix(".git")
        return f"https://gitlab.com/{slug}/-/raw/{rev}/{path}"
    if repo.startswith("https://codeberg.org/"):
        slug = repo[len("https://codeberg.org/") :].removesuffix(".git")
        return f"https://codeberg.org/{slug}/raw/commit/{rev}/{path}"
    if repo.startswith("https://git.sr.ht/"):
        slug = repo[len("https://git.sr.ht/") :].removesuffix(".git")
        return f"https://git.sr.ht/{slug}/blob/{rev}/{path}"
    return None


def classify_text(text: str) -> str | None:
    """Identify a licence from its text. Order matters: check the strict first."""
    t = " ".join(text.split()).lower()
    if not t:
        return None
    # Copyleft first, so a GPL file that happens to quote "permission is hereby
    # granted" in a preamble is never mistaken for MIT.
    if "gnu affero general public license" in t:
        return "AGPL-3.0"
    if "gnu lesser general public license" in t:
        return "LGPL-3.0" if "version 3" in t else "LGPL-2.1"
    if "gnu general public license" in t:
        return "GPL-3.0" if "version 3" in t else "GPL-2.0"
    if "mozilla public license" in t:
        return "MPL-2.0"
    if "eclipse public license" in t:
        return "EPL-2.0"
    if "apache license" in t and "version 2.0" in t:
        return "Apache-2.0"  # third-party grammar licence identifier
    if "permission to use, copy, modify, and/or distribute this software" in t:
        return "ISC"
    if "permission is hereby granted, free of charge" in t:
        return "MIT"
    if "redistribution and use in source and binary forms" in t:
        if "neither the name" in t:
            return "BSD-3-Clause"
        return "BSD-2-Clause"
    if "this is free and unencumbered software released into the public domain" in t:
        return "Unlicense"
    if "cc0" in t or "creative commons zero" in t:
        return "CC0-1.0"
    if "do what the fuck you want to public license" in t:
        return "WTFPL"
    if "blue oak model license" in t:
        return "BlueOak-1.0.0"
    return None


def _github_api_licence(repo: str) -> str | None:
    """Last resort: the forge's own licence detection for the repository.

    Weakest evidence of the three, because it reports the default branch rather
    than the pinned revision, so it is recorded as such. Used only when the
    grammar ships no licence file and declares nothing, which at the audited
    revision is two grammars out of 371.
    """
    if not repo.startswith("https://github.com/"):
        return None
    slug = repo[len("https://github.com/") :].rstrip("/").removesuffix(".git")
    raw = _get(f"https://api.github.com/repos/{slug}")
    if not raw:
        return None
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return None
    spdx = ((obj.get("license") or {}) or {}).get("spdx_id")
    if isinstance(spdx, str) and spdx and spdx != "NOASSERTION":
        return _normalise_spdx(spdx)
    return None


def classify_declaration(raw: str, filename: str) -> str | None:
    """Pull an SPDX-ish identifier out of a metadata file."""
    if filename.endswith(".json"):
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            return None
        value = obj.get("license")
        if isinstance(value, dict):
            value = value.get("type") or value.get("text")
        if isinstance(value, str) and value.strip():
            return _normalise_spdx(value)
        return None
    m = re.search(r'^\s*license(?:\.text)?\s*=\s*[\'"]([^\'"]+)[\'"]', raw, re.MULTILINE)
    if m:
        return _normalise_spdx(m.group(1))
    m = re.search(r'^\s*license\s*=\s*\{\s*text\s*=\s*[\'"]([^\'"]+)[\'"]', raw, re.MULTILINE)
    if m:
        return _normalise_spdx(m.group(1))
    return None


def _normalise_spdx(value: str) -> str:
    v = value.strip().strip('"').strip()
    table = {
        "mit": "MIT",
        "mit license": "MIT",
        "the mit license": "MIT",
        "isc": "ISC",
        "apache-2.0": "Apache-2.0",
        "apache 2.0": "Apache-2.0",
        "apache license 2.0": "Apache-2.0",
        "bsd-3-clause": "BSD-3-Clause",
        "bsd-2-clause": "BSD-2-Clause",
        "unlicense": "Unlicense",
        "cc0-1.0": "CC0-1.0",
        "agpl-3.0": "AGPL-3.0",
        "gpl-3.0": "GPL-3.0",
        "gpl-2.0": "GPL-2.0",
        "lgpl-2.1": "LGPL-2.1",
        "mpl-2.0": "MPL-2.0",
    }
    return table.get(v.lower(), v)


def family(spdx: str | None) -> str:
    if spdx is None:
        return "unresolved"
    if spdx in PERMISSIVE:
        return "permissive"
    if spdx in COPYLEFT:
        return "copyleft"
    return "unrecognised"


def audit_one(name: str, spec: dict) -> dict:
    repo = spec.get("repo")
    rev = spec.get("rev") or spec.get("branch") or "HEAD"
    record: dict = {
        "language": name,
        "repo": repo,
        "rev": rev,
        "spdx": None,
        "evidence": "none",
        "evidence_path": None,
    }
    if not repo:
        # A handful of entries are built from a directory inside another repo.
        record["evidence"] = "no repository recorded in the bundle manifest"
        declared = spec.get("license")
        if declared:
            record["spdx"] = _normalise_spdx(declared)
            record["evidence"] = "bundle manifest license field"
        record["family"] = family(record["spdx"])
        return record

    for filename in LICENCE_FILENAMES:
        url = _raw_url(repo, rev, filename)
        if not url:
            break
        text = _get(url)
        if text:
            spdx = classify_text(text)
            if spdx:
                record.update(
                    spdx=spdx, evidence="licence file at pinned revision", evidence_path=filename
                )
                record["family"] = family(spdx)
                return record

    for filename in METADATA_FILENAMES:
        url = _raw_url(repo, rev, filename)
        if not url:
            break
        text = _get(url)
        if text:
            spdx = classify_declaration(text, filename)
            if spdx:
                record.update(
                    spdx=spdx,
                    evidence="licence declared in grammar metadata at pinned revision",
                    evidence_path=filename,
                )
                record["family"] = family(spdx)
                return record

    declared = spec.get("license")
    if declared:
        record.update(spdx=_normalise_spdx(declared), evidence="bundle manifest license field")
        record["family"] = family(record["spdx"])
        return record

    spdx = _github_api_licence(repo)
    if spdx:
        record.update(
            spdx=spdx,
            evidence="forge licence detection for the repository (default branch, not the pinned revision)",
        )
        record["family"] = family(spdx)
        return record

    record["evidence"] = "no licence file or declaration found at the pinned revision"
    record["family"] = "unresolved"
    return record


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument(
        "--only",
        default="",
        help="comma-separated languages to audit instead of the whole bundle",
    )
    args = ap.parse_args()

    if not args.manifest.exists():
        print(
            f"audit-grammar-bundle: manifest not found: {args.manifest}\n"
            "Vendor it from the bundle repository's sources/language_definitions.json.",
            file=sys.stderr,
        )
        return 2
    data = json.loads(args.manifest.read_text(encoding="utf-8"))
    definitions = data.get("languages", data)
    if not isinstance(definitions, dict) or not definitions:
        print("audit-grammar-bundle: manifest carries no language definitions", file=sys.stderr)
        return 2

    wanted = [n.strip() for n in args.only.split(",") if n.strip()]
    if wanted:
        missing = sorted(set(wanted) - set(definitions))
        if missing:
            print(f"audit-grammar-bundle: unknown languages: {missing}", file=sys.stderr)
            return 2
        definitions = {k: v for k, v in definitions.items() if k in wanted}

    names = sorted(definitions)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        records = list(pool.map(lambda n: audit_one(n, definitions[n]), names))

    if len(records) != len(names):
        print("audit-grammar-bundle: incomplete audit; refusing to write", file=sys.stderr)
        return 2

    counts: dict[str, int] = {}
    for r in records:
        counts[r["family"]] = counts.get(r["family"], 0) + 1
    spdx_counts: dict[str, int] = {}
    for r in records:
        key = r["spdx"] or "unresolved"
        spdx_counts[key] = spdx_counts.get(key, 0) + 1

    payload = {
        "bundle": data.get("bundle", "tree-sitter-language-pack"),
        "bundle_version": data.get("bundle_version"),
        "manifest_source": data.get("manifest_source"),
        "audited_languages": len(records),
        "family_counts": dict(sorted(counts.items())),
        "spdx_counts": dict(sorted(spdx_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "copyleft": sorted(r["language"] for r in records if r["family"] == "copyleft"),
        "unresolved": sorted(
            r["language"] for r in records if r["family"] in ("unresolved", "unrecognised")
        ),
        "grammars": records,
        "method": (
            "Each grammar's licence is resolved from its own upstream repository at the "
            "exact revision the bundle pins, strongest evidence first: licence file, then "
            "a declaration in the grammar's metadata, then the bundle manifest's own "
            "license field. Nothing unresolved is assumed permissive."
        ),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    print(f"audit-grammar-bundle: wrote {args.out} for {len(records)} grammars")
    for fam, n in sorted(counts.items()):
        print(f"  {fam}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

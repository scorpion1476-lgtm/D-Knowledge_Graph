#!/usr/bin/env python3
"""Blocking scan for forbidden identifiers in the tracked tree.

The project must never carry the reference code-intelligence tool's name,
handle, URLs, or distinctive terminology, nor the two eponymous names of the
modularity-optimization algorithm family. The obvious way to check that would be
a list of the forbidden strings, but that list would itself put the strings into
a tracked file and defeat the purpose.

So this scanner stores only SHA-256 digests of the lowercased forbidden terms
(``scripts/scrub_digests.json``). Tracked files are tokenised, each token is
lowercased and hashed, and the hash is compared against the digest set. A digest
cannot be read back into the term it came from, so the deny-list is safe to
commit, and a report can name the offending FILE without ever printing the
offending STRING.

The digest set is derived from the untracked planning corpus: tokens that recur
there, are absent from the tracked tree, and are not English dictionary words.
That shape isolates product names, handles, and domains rather than ordinary
vocabulary. Generic domain language (code graph, impact analysis, blast radius)
is deliberately not in the set, because it is allowed.

What is scanned, and why each part is needed:

- Tracked files, which are the obvious surface.
- Untracked, non-ignored files, because the gate runs before a commit and a
  brand new file would otherwise pass and only be caught once recorded.
  Gitignored paths are deliberately excluded: they cannot reach the public
  surface, and the planning corpus legitimately contains the terms.
- Zip-container documents (xlsx, docx, pptx) by unzipping their XML parts,
  because treating them as opaque binaries would leave a tracked, publishable
  document permanently unscanned.
- With ``--history``, every blob reachable from a ref. A file deleted in a
  later commit is still in the history that gets pushed, so an index-only scan
  would report clean while a forbidden term sat one ``git log -p`` away. Run
  this before publishing.

Matching is subtoken-aware: a forbidden name arrives embedded in a longer
identifier far more often than standing alone.

Usage:
    python scripts/scrub_scan.py                    # scan, exit non-zero on any hit
    python scripts/scrub_scan.py --history          # also scan HEAD's whole history
    python scripts/scrub_scan.py --json             # machine-readable summary
    python scripts/scrub_scan.py --add-terms-from FILE
        Merge digests for the newline-separated terms in a LOCAL, UNTRACKED
        file. Only the digests are written; the terms themselves are never
        stored. This is how the deny-list is extended without committing
        plaintext.

Fails loud: a missing or empty digest file is an error, never a silent pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIGESTS = ROOT / "scripts" / "scrub_digests.json"

# A token is a word-ish run long enough to be an identifier. Short runs are
# skipped because they cannot carry a product name and only add noise.
TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_-]{3,}")

# A forbidden name is far more likely to arrive embedded in a longer identifier
# (``foobarClient``, ``foobar_adapter``, a URL path segment) than standing
# alone, so each token is also split on separators and case boundaries and every
# part is checked. Matching whole tokens only would miss the realistic case.
SUBTOKEN_SPLIT = re.compile(r"[-_./:]+|(?<=[a-z0-9])(?=[A-Z])")
MIN_SUBTOKEN = 4

# Files whose bytes are not text worth tokenising. The scan reads text only.
BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".gz",
    ".tar", ".whl", ".so", ".dylib", ".woff", ".woff2", ".ttf", ".sqlite",
}

# Zip-container document formats. Their text lives in XML parts inside the
# archive, so treating them as opaque binaries would leave a tracked, publishable
# document permanently unscanned.
OOXML_SUFFIXES = {".xlsx", ".docx", ".pptx"}

# The digest file holds digests, not terms, so scanning it is meaningless noise.
SELF_EXCLUDE = {"scripts/scrub_digests.json"}


class Unreadable(Exception):
    """A target that is in scope but could not be read.

    The distinction this carries is the whole point of the class. A binary
    suffix is out of scope by policy and reporting it would be noise. A tracked
    path that is missing from disk, a file the process cannot open, or a
    zip-container document that will not unzip is a hole in the coverage of a
    blocking gate, and a gate that cannot see a file must never count that file
    as clean. Every one of these used to be a silent `return None`, which meant
    the scan printed the word clean over a tree it had only partly read.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _load_digests() -> set[str]:
    if not DIGESTS.exists():
        raise SystemExit(f"scrub-scan: digest file missing: {DIGESTS}")
    try:
        raw = json.loads(DIGESTS.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SystemExit(f"scrub-scan: digest file unreadable: {e}") from e
    if not isinstance(raw, list) or not raw:
        raise SystemExit("scrub-scan: digest file is empty or malformed; refusing to pass")
    digests = {str(d).strip().lower() for d in raw if str(d).strip()}
    if not all(len(d) == 64 for d in digests):
        raise SystemExit("scrub-scan: digest file contains a non-SHA-256 entry; refusing to pass")
    return digests


def _tracked_files() -> list[str]:
    proc = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise SystemExit(f"scrub-scan: git ls-files failed: {proc.stderr.strip()}")
    return [line for line in proc.stdout.splitlines() if line.strip()]


def _digest(value: str) -> str:
    return hashlib.sha256(value.lower().encode("utf-8")).hexdigest()


# The number of adjacent parts joined back together when checking a token. A
# multi-hump product name arrives as one lowercased digest but appears in code as
# several parts: `AcmeLensAdapter`, `acme_lens_adapter`, and `acme-lens-mcp` all
# split into [acme, lens, adapter], and none of those parts matches the digest of
# "acmelens" on its own. Rejoining runs of parts is what closes that gap. Four
# covers every realistic product name; the cost is linear in the token length.
MAX_JOINED_PARTS = 4


def _candidates(raw_token: str) -> list[str]:
    """Every form of one token a forbidden term could be hiding in.

    The whole token, each part it splits into, and every run of adjacent parts
    joined back up. The last of those is the one that matters: a term stored as
    a single lowercased digest is invisible to part-by-part matching the moment
    it is written with a case or separator boundary inside it, which is exactly
    how a product name appears inside an identifier.
    """
    out = [raw_token.lower()]
    parts = [p for p in SUBTOKEN_SPLIT.split(raw_token) if p]
    lowered = [p.lower() for p in parts]
    for i in range(len(lowered)):
        joined = ""
        for j in range(i, min(i + MAX_JOINED_PARTS, len(lowered))):
            joined += lowered[j]
            if len(joined) >= MIN_SUBTOKEN:
                out.append(joined)
    return out


def _hits_in(text: str, digests: set[str]) -> int:
    count = 0
    for m in TOKEN.finditer(text):
        # One occurrence is counted once however many of its forms match.
        if any(_digest(c) in digests for c in _candidates(m.group(0))):
            count += 1
    return count


def _ooxml_text(path: Path) -> str:
    """Concatenate the XML parts of a zip-container document.

    Raises Unreadable when the container will not open. Returning an empty
    string instead would have made an unscannable tracked document look exactly
    like a scanned clean one.
    """
    import zipfile

    chunks: list[str] = []
    try:
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                if info.filename.endswith((".xml", ".rels")) and info.file_size < 8 * 1024 * 1024:
                    chunks.append(zf.read(info).decode("utf-8", "ignore"))
    except (OSError, zipfile.BadZipFile, RuntimeError) as e:
        raise Unreadable(f"zip container would not open ({type(e).__name__})") from e
    return "\n".join(chunks)


def _ooxml_bytes_text(data: bytes) -> str:
    """The XML parts of a zip-container document held in memory.

    Raises Unreadable for the same reason the worktree variant does.
    """
    import io
    import zipfile

    chunks: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                if info.filename.endswith((".xml", ".rels")) and info.file_size < 8 * 1024 * 1024:
                    chunks.append(zf.read(info).decode("utf-8", "ignore"))
    except (OSError, zipfile.BadZipFile, RuntimeError) as e:
        raise Unreadable(f"zip container would not open ({type(e).__name__})") from e
    return "\n".join(chunks)


def _untracked_files() -> list[str]:
    """Files added but not yet committed.

    The gate runs before a commit, so a forbidden term in a brand new file would
    otherwise pass unnoticed and only be caught after it was already recorded.
    """
    proc = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        # Returning an empty list here silently dropped the entire new-file
        # surface: the scan would report clean having looked at the index only.
        raise SystemExit(f"scrub-scan: git status failed: {proc.stderr.strip()}")
    out: list[str] = []
    for line in proc.stdout.splitlines():
        if len(line) > 3 and line[0] in "?A M" and line[1] in "?AM ":
            name = line[3:].strip().strip('"')
            if name and not name.endswith("/"):
                out.append(name)
    return out


def _history_messages(ref: str) -> list[tuple[str, str]]:
    """Every (sha, message) commit reachable from a ref, plus the ref names.

    The blob walk below cannot see these. `git rev-list --objects` emits a
    commit as a bare sha with no path, and the parser drops any line that is not
    a (sha, path) pair, so every commit message in the history was silently
    outside the scan. The project's own rule puts commit messages and branch
    names in scope, and a forbidden identifier is at least as likely to appear
    in a message as in a file: a message is written quickly and never reviewed
    again.
    """
    proc = subprocess.run(
        ["git", "rev-list", *shlex.split(ref)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=900,
    )
    if proc.returncode != 0:
        raise SystemExit(f"scrub-scan: cannot enumerate commits of {ref!r}: {proc.stderr.strip()}")
    out: list[tuple[str, str]] = []
    for sha in proc.stdout.split():
        body = subprocess.run(
            ["git", "log", "-1", "--format=%an%n%ae%n%cn%n%ce%n%B", sha],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if body.returncode != 0:
            raise SystemExit(f"scrub-scan: cannot read commit {sha}: {body.stderr.strip()}")
        out.append((sha, body.stdout))
    return out


def _ref_names(ref: str) -> list[str]:
    """Branch and tag names in scope, because a name is published too."""
    proc = subprocess.run(
        ["git", "for-each-ref", "--format=%(refname)"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise SystemExit(f"scrub-scan: cannot list refs: {proc.stderr.strip()}")
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def _history_blobs(ref: str) -> list[tuple[str, str]]:
    """Every (sha, path) blob reachable from a ref.

    A file deleted in a later commit is still in the history that gets pushed,
    so scanning only the current index would report clean while a forbidden term
    sits one `git log -p` away.
    """
    proc = subprocess.run(
        ["git", "rev-list", "--objects", *shlex.split(ref)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=900,
    )
    if proc.returncode != 0:
        # Never degrade to "clean" on a ref that does not resolve. A typo, a
        # renamed branch, or a shallow clone would otherwise turn a blocking
        # gate into a no-op that prints the word clean.
        raise SystemExit(f"scrub-scan: cannot enumerate {ref!r}: {proc.stderr.strip()}")
    out: list[tuple[str, str]] = []
    for line in proc.stdout.splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) == 2 and parts[1].strip():
            out.append((parts[0], parts[1].strip()))
    return out


def _scan_worktree_file(rel: str, digests: set[str]) -> int | None:
    """Match count for one file on disk.

    Returns None only when the path is out of scope by policy: a binary suffix
    whose bytes carry no readable identifier, or a submodule gitlink whose own
    history is not this repository's to scan. Every other way of not reading a
    file raises Unreadable, because a gate that cannot read an in-scope path has
    a hole in it and must say so rather than report clean.
    """
    path = ROOT / rel
    suffix = path.suffix.lower()
    if suffix in BINARY_SUFFIXES:
        return None
    if path.is_symlink() and not path.exists():
        raise Unreadable("dangling symlink")
    if path.is_dir():
        # A gitlink: the submodule has its own repository and its own gate.
        return None
    if not path.exists():
        # The case that motivated this hardening. `git ls-files` lists the
        # index, so a tracked file removed from the worktree is still a tracked
        # path that will be pushed, and skipping it under-covers the scan.
        raise Unreadable("tracked path missing from disk")
    if not path.is_file():
        raise Unreadable("not a regular file")
    if suffix in OOXML_SUFFIXES:
        text = _ooxml_text(path)
    else:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            raise Unreadable(f"could not be opened ({type(e).__name__})") from e
    # The path itself must be clean too: a filename can carry a product name.
    return _hits_in(text, digests) + _hits_in(rel, digests)


def scan(*, history_ref: str | None = None) -> dict:
    digests = _load_digests()
    tracked = _tracked_files()
    untracked = _untracked_files()
    targets: list[str] = []
    for rel in [*tracked, *untracked]:
        if rel not in SELF_EXCLUDE and rel not in targets:
            targets.append(rel)

    offenders: list[dict] = []
    unreadable: list[dict] = []
    scanned = 0
    policy_skipped = 0
    for rel in targets:
        try:
            count = _scan_worktree_file(rel, digests)
        except Unreadable as e:
            unreadable.append({"file": rel, "reason": e.reason, "where": "worktree"})
            continue
        if count is None:
            policy_skipped += 1
            continue
        scanned += 1
        if count:
            offenders.append(
                {"file": rel, "matches": count, "where": "tracked" if rel in tracked else "untracked"}
            )

    history_scanned = 0
    messages_scanned = 0
    if history_ref:
        seen: set[str] = set()
        for sha, name in _history_blobs(history_ref):
            if sha in seen or name in SELF_EXCLUDE:
                continue
            seen.add(sha)
            suffix = Path(name).suffix.lower()
            if suffix in BINARY_SUFFIXES:
                policy_skipped += 1
                continue
            proc = subprocess.run(
                ["git", "cat-file", "-p", sha], cwd=ROOT, capture_output=True, timeout=120
            )
            if proc.returncode != 0:
                # A blob the enumeration named but the object store will not
                # produce means the history was only partly read. Skipping it
                # quietly is exactly the under-coverage this gate must not have.
                unreadable.append(
                    {
                        "file": f"{name} (in {history_ref} history)",
                        "reason": "git cat-file failed",
                        "where": "history",
                    }
                )
                continue
            if suffix in OOXML_SUFFIXES:
                # A zip-container document in history holds its text in XML parts
                # exactly as it does in the worktree. Skipping it here would leave
                # the one scan that is meant to be run before publishing blind to
                # the file type the worktree scan goes out of its way to open.
                try:
                    body = _ooxml_bytes_text(proc.stdout)
                except Unreadable as e:
                    unreadable.append(
                        {
                            "file": f"{name} (in {history_ref} history)",
                            "reason": e.reason,
                            "where": "history",
                        }
                    )
                    continue
            else:
                body = proc.stdout.decode("utf-8", "ignore")
            history_scanned += 1
            count = _hits_in(body, digests) + _hits_in(name, digests)
            if count:
                offenders.append({"file": f"{name} (in {history_ref} history)", "matches": count, "where": "history"})

    messages_scanned = 0
    if history_ref:
        for sha, message in _history_messages(history_ref):
            messages_scanned += 1
            count = _hits_in(message, digests)
            if count:
                offenders.append(
                    {
                        "file": f"commit {sha[:12]} message (in {history_ref} history)",
                        "matches": count,
                        "where": "commit-message",
                    }
                )
        for name in _ref_names(history_ref):
            count = _hits_in(name, digests)
            if count:
                offenders.append(
                    {"file": f"ref {name}", "matches": count, "where": "ref-name"}
                )

    offenders.sort(key=lambda o: (-int(o["matches"]), str(o["file"])))
    unreadable.sort(key=lambda u: str(u["file"]))
    return {
        "digest_terms": len(digests),
        "tracked_files": len(tracked),
        "untracked_files": len(untracked),
        "files_scanned": scanned,
        "policy_skipped": policy_skipped,
        "history_ref": history_ref,
        "history_blobs_scanned": history_scanned,
        "history_commit_messages_scanned": messages_scanned,
        "offending_files": len(offenders),
        "total_matches": sum(int(o["matches"]) for o in offenders),
        "offenders": offenders,
        "unreadable_files": len(unreadable),
        "unreadable": unreadable,
        # Coverage is part of the verdict. An unreadable in-scope path means the
        # scan did not see the whole surface, and a scan that did not see the
        # whole surface has not established that the surface is clean.
        "ok": not offenders and not unreadable,
        "note": (
            "Matches are reported by file and count only. The forbidden strings "
            "are stored as one-way digests and are never printed. Tokens are also "
            "split on separators and case boundaries, so a forbidden name embedded "
            "in a longer identifier is caught. A path that is in scope but could "
            "not be read is reported as unreadable and fails the scan; only a "
            "binary suffix or a submodule gitlink is skipped by policy."
        ),
    }


def add_terms_from(source: Path) -> int:
    if not source.is_file():
        raise SystemExit(f"scrub-scan: term file not found: {source}")
    terms = [
        line.strip().lower()
        for line in source.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not terms:
        raise SystemExit("scrub-scan: term file contained no terms")
    existing = _load_digests() if DIGESTS.exists() else set()
    merged = sorted(existing | {hashlib.sha256(t.encode("utf-8")).hexdigest() for t in terms})
    DIGESTS.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    # Deliberately reports counts only, so the terms never reach a log.
    print(f"scrub-scan: merged {len(terms)} term(s); digest set now holds {len(merged)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan the tracked tree for forbidden identifiers")
    parser.add_argument("--json", dest="as_json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--history",
        dest="history",
        nargs="?",
        const="--all",
        default=None,
        help=(
            "also scan every blob reachable from a ref; defaults to --all, which is "
            "every local ref, because that is the surface a push can expose. Pass an "
            "explicit ref to narrow it."
        ),
    )
    parser.add_argument(
        "--add-terms-from",
        dest="add_from",
        default=None,
        help="merge digests for terms in a LOCAL untracked file (terms are not stored)",
    )
    args = parser.parse_args()

    if args.add_from:
        return add_terms_from(Path(args.add_from))

    result = scan(history_ref=args.history)
    if args.history and not result["history_blobs_scanned"]:
        raise SystemExit(
            f"scrub-scan: {args.history!r} yielded no blobs to scan; refusing to report clean"
        )
    if args.as_json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        extra = (
            f", plus {result['history_blobs_scanned']} blobs and "
            f"{result['history_commit_messages_scanned']} commit messages "
            f"in {result['history_ref']} history"
            if result["history_ref"]
            else ""
        )
        print(
            f"scrub-scan: {result['files_scanned']} text files scanned "
            f"({result['tracked_files']} tracked, {result['untracked_files']} untracked)"
            f"{extra} against {result['digest_terms']} forbidden-term digests"
        )
        if result["unreadable"]:
            print(
                f"scrub-scan: FAIL - {result['unreadable_files']} in-scope path(s) could not "
                "be read, so coverage is incomplete and the tree is NOT established clean"
            )
            for u in result["unreadable"]:
                print(f"  {u['file']}: {u['reason']}")
        if result["offenders"]:
            print(f"scrub-scan: FAIL - {result['offending_files']} file(s) carry a forbidden identifier")
            for o in result["offenders"]:
                print(f"  {o['file']}: {o['matches']} match(es)")
        if result["ok"]:
            print("scrub-scan: clean (no forbidden identifier found, every in-scope path read)")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

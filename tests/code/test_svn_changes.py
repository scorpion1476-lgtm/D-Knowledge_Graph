"""N-19: incremental change detection for a Subversion working copy.

SCOPE OF THIS SUITE, in two halves.

The first half puts a stub ``svn`` on PATH that emits real-format
``svn status -v --xml`` for the directory it is given. It exists because the
project must stay gateable on a machine with no Subversion installed, and it
exercises every line of product code on the path: capability detection, the
subprocess invocation with list arguments, the XML parse, the versioned-file
listing, the hash comparison, and the incremental ingest that re-parses only
what changed.

The second half, from ``test_a_real_working_copy_is_recognised_...`` onward,
creates a real repository with ``svnadmin``, checks it out with ``svn``, and
drives the same path against the real binary. This is the half that can catch
what a hand-written stub cannot: real ``svn status`` output carrying entry
shapes the stub author did not think to emit. It covers recognition, versioned
listing, an unversioned file, a deleted file, a committed edit, and an
uncommitted edit.

Both halves are kept. The stub half is not redundant: it is the half that still
runs where Subversion is absent, and it skips the real half honestly with that
reason rather than passing silently on nothing.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys

import pytest

from dkg.code.changes import (
    detect_changes,
    is_svn_checkout,
    list_versioned_files_svn,
    svn_available,
)
from dkg.core.errors import IngestError

try:
    import tree_sitter  # noqa: F401

    _TS = True
except Exception:
    _TS = False

requires_ts = pytest.mark.skipif(not _TS, reason="tree-sitter not installed (the 'code' extra)")

REAL_SVN = shutil.which("svn") is not None


# A stub that emits the same XML a real `svn status -v --xml` emits for a
# working copy: one <entry> per versioned item carrying a <wc-status> item
# attribute. It walks the directory it is handed, which is what makes the test
# depend on the files rather than on a fixed answer.
_STUB = '''#!{python}
import os, sys, xml.sax.saxutils as sx

args = sys.argv[1:]
target = args[-1]
lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<status>', '<target path="%s">' % sx.quoteattr(target)[1:-1]]
for root, dirs, files in os.walk(target):
    dirs[:] = [d for d in dirs if d != ".svn"]
    for name in sorted(files):
        full = os.path.join(root, name)
        item = "unversioned" if name.endswith(".ignoreme") else "normal"
        lines.append('<entry path=%s><wc-status item="%s" revision="1"></wc-status></entry>'
                     % (sx.quoteattr(full), item))
lines += ['</target>', '</status>']
sys.stdout.write("\\n".join(lines) + "\\n")
'''


@pytest.fixture
def stub_svn(tmp_path, monkeypatch):
    """Put a stub ``svn`` first on PATH for the duration of one test."""
    bindir = tmp_path / "stubbin"
    bindir.mkdir()
    stub = bindir / "svn"
    stub.write_text(_STUB.format(python=sys.executable), encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return bindir


def _make_checkout(root, files):
    (root / ".svn").mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


# -- capability detection -----------------------------------------------------


def test_a_plain_directory_is_not_a_working_copy(tmp_path):
    assert is_svn_checkout(tmp_path) is False


def test_an_administrative_directory_makes_it_a_working_copy(tmp_path):
    (tmp_path / ".svn").mkdir()

    assert is_svn_checkout(tmp_path) is True


def test_availability_is_false_when_nothing_is_on_the_path(tmp_path, monkeypatch):
    """Probed against a real PATH, not compared to a copy of its own logic.

    Asserting `svn_available() is REAL_SVN` would re-implement the function in
    the test and could never fail. Emptying PATH and asserting False, then
    putting a binary on PATH and asserting True, tests the probe instead.
    """
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    assert svn_available() is False


def test_availability_sees_a_binary_on_the_path(stub_svn):
    assert svn_available() is True


# -- listing ------------------------------------------------------------------


def test_versioned_files_are_listed_from_the_status_output(stub_svn, tmp_path):
    work = _make_checkout(
        tmp_path / "wc", {"a.py": "x = 1\n", "pkg/b.py": "y = 2\n", "notes.txt": "hi\n"}
    )

    files = list_versioned_files_svn(work, exts={".py"})

    assert files == ["a.py", "pkg/b.py"]


def test_an_unversioned_entry_is_not_listed(stub_svn, tmp_path):
    work = _make_checkout(tmp_path / "wc", {"a.py": "x = 1\n", "skipme.py.ignoreme": "z = 3\n"})

    files = list_versioned_files_svn(work, exts={".py", ".ignoreme"})

    assert files == ["a.py"], "the stub marks .ignoreme unversioned"


def test_an_absent_svn_binary_is_an_error_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    work = _make_checkout(tmp_path / "wc", {"a.py": "x = 1\n"})

    with pytest.raises(IngestError):
        list_versioned_files_svn(work, exts={".py"})


# -- the shared incremental path ----------------------------------------------


def test_change_detection_runs_through_the_same_comparison_as_git(stub_svn, tmp_path):
    work = _make_checkout(tmp_path / "wc", {"a.py": "x = 1\n", "b.py": "y = 2\n"})

    first = detect_changes(work, {}, exts={".py"})

    assert first["vcs"] == "svn"
    assert sorted(first["changed"]) == ["a.py", "b.py"]
    assert first["unchanged"] == 0

    from dkg.code.changes import file_sha256

    stored = {rel: file_sha256(work / rel) for rel in ("a.py", "b.py")}
    (work / "a.py").write_text("x = 99\n", encoding="utf-8")

    second = detect_changes(work, stored, exts={".py"})

    assert second["changed"] == ["a.py"], "only the modified file is re-parsed"
    assert second["unchanged"] == 1
    assert second["removed"] == []


def test_a_file_that_leaves_the_working_copy_is_reported_removed(stub_svn, tmp_path):
    work = _make_checkout(tmp_path / "wc", {"a.py": "x = 1\n"})

    result = detect_changes(work, {"gone.py": "deadbeef"}, exts={".py"})

    assert result["removed"] == ["gone.py"]


def test_a_directory_under_neither_system_is_refused(tmp_path):
    with pytest.raises(IngestError, match="neither a git clone nor a Subversion"):
        detect_changes(tmp_path, {}, exts={".py"})


# -- end to end ---------------------------------------------------------------


@requires_ts
def test_a_subversion_checkout_ingests_and_then_updates_incrementally(db, stub_svn, tmp_path):
    from dkg.code.ingest import ingest_repo

    work = _make_checkout(
        tmp_path / "wc",
        {
            "lib.py": "def helper():\n    return 1\n",
            "app.py": "from lib import helper\n\n\ndef run():\n    return helper()\n",
        },
    )

    first = ingest_repo(db, work)

    assert first["mode"] == "svn-full"
    assert first["parsed_files"] == 2
    canonicals = {
        r["canonical"]
        for r in db.fetchall("SELECT canonical FROM entities WHERE kind LIKE 'code:%';")
    }
    assert "lib.py::helper" in canonicals

    (work / "lib.py").write_text("def helper():\n    return 42\n", encoding="utf-8")
    second = ingest_repo(db, work)

    assert second["mode"] == "svn-incremental"
    assert second["parsed_files"] == 1, "only the changed file is re-parsed"
    assert second["unchanged_files"] == 1


@requires_ts
def test_a_second_ingest_with_no_change_parses_nothing(db, stub_svn, tmp_path):
    from dkg.code.ingest import ingest_repo

    work = _make_checkout(tmp_path / "wc", {"lib.py": "def helper():\n    return 1\n"})
    ingest_repo(db, work)

    again = ingest_repo(db, work)

    assert again["mode"] == "svn-incremental"
    assert again["parsed_files"] == 0
    assert again["unchanged_files"] == 1


@pytest.mark.skipif(not REAL_SVN, reason="no real Subversion binary in this environment")
def test_the_probe_agrees_with_a_real_binary_when_one_is_present():
    """Runs only where svn exists. Recorded so the gap is visible, not hidden."""
    assert svn_available() is True
    proc = subprocess.run(["svn", "--version", "--quiet"], capture_output=True, text=True)
    assert proc.returncode == 0


# -- against a real Subversion repository -------------------------------------
#
# Everything above this line runs against a stub that emits real-format XML. The
# stub exercises every line of product code, which is worth having, but it
# cannot catch the one class of bug that matters most here: the real binary
# emitting something the parser does not expect. `svn status -v --xml` output
# differs from the stub's in ways a hand-written stub will not think of, and an
# unversioned or externally-defined entry is exactly where a parser goes wrong.
#
# So these create a real repository with svnadmin, check it out with svn, and
# drive ingest_repo through it. They skip honestly where svn is absent rather
# than being deleted, because that is the environment the project is usually
# gated in.


REAL_SVNADMIN = shutil.which("svnadmin") is not None

requires_real_svn = pytest.mark.skipif(
    not (REAL_SVN and REAL_SVNADMIN),
    reason="no real Subversion binary (svn and svnadmin) in this environment",
)


def _run_svn(*args: str, cwd=None) -> str:
    """Non-interactive by construction: no editor, no prompt, no network.

    ``--non-interactive`` is a client flag, so it is passed to ``svn`` and not
    to ``svnadmin``, which rejects it.
    """
    argv = list(args)
    if argv[0] == "svn":
        argv.append("--non-interactive")
    proc = subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"{args} failed: {proc.stderr.strip()}"
    return proc.stdout


def _real_checkout(root, files: dict[str, str]):
    """A real repository and a real working copy with `files` committed."""
    repo = root / "repo"
    work = root / "wc"
    _run_svn("svnadmin", "create", str(repo))
    # A file:// URL is local disk. Nothing here reaches the network.
    _run_svn("svn", "checkout", repo.as_uri(), str(work))
    for name, text in files.items():
        path = work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        _run_svn("svn", "add", str(path), cwd=work)
    _run_svn("svn", "commit", "-m", "initial", cwd=work)
    return work


@requires_real_svn
def test_a_real_working_copy_is_recognised_and_its_files_listed(tmp_path):
    work = _real_checkout(
        tmp_path, {"lib.py": "def helper():\n    return 1\n", "notes.txt": "hello\n"}
    )

    assert is_svn_checkout(work) is True
    listed = set(list_versioned_files_svn(work, exts={".py", ".txt"}))
    assert "lib.py" in listed
    assert "notes.txt" in listed


@requires_real_svn
def test_a_real_unversioned_file_is_not_listed(tmp_path):
    """The distinction the stub can only assert, this one observes."""
    work = _real_checkout(tmp_path, {"lib.py": "def helper():\n    return 1\n"})
    (work / "scratch.py").write_text("def scratch():\n    return 0\n", encoding="utf-8")

    listed = set(list_versioned_files_svn(work, exts={".py"}))

    assert "lib.py" in listed
    assert "scratch.py" not in listed, "an unversioned file must not be listed as versioned"


@requires_ts
@requires_real_svn
def test_a_real_checkout_ingests_and_then_updates_incrementally(db, tmp_path):
    """The row's whole claim, against a real binary and a real working copy."""
    from dkg.code.ingest import ingest_repo

    work = _real_checkout(
        tmp_path,
        {
            "lib.py": "def helper():\n    return 1\n",
            "app.py": "from lib import helper\n\n\ndef run():\n    return helper()\n",
        },
    )

    first = ingest_repo(db, work)
    assert first["mode"] == "svn-full"
    assert first["parsed_files"] == 2
    canonicals = {
        r["canonical"]
        for r in db.fetchall("SELECT canonical FROM entities WHERE kind LIKE 'code:%';")
    }
    assert "lib.py::helper" in canonicals

    # No change at all: the incremental path must re-parse nothing.
    unchanged = ingest_repo(db, work)
    assert unchanged["mode"] == "svn-incremental"
    assert unchanged["parsed_files"] == 0
    assert unchanged["unchanged_files"] == 2

    # One file edited and committed: only that file is re-parsed.
    (work / "lib.py").write_text("def helper():\n    return 42\n", encoding="utf-8")
    _run_svn("svn", "commit", "-m", "change helper", cwd=work)

    changed = ingest_repo(db, work)
    assert changed["mode"] == "svn-incremental"
    assert changed["parsed_files"] == 1, "only the changed file is re-parsed"
    assert changed["unchanged_files"] == 1


@requires_ts
@requires_real_svn
def test_a_real_uncommitted_edit_is_still_detected(db, tmp_path):
    """Change detection is content-based, so it must not wait for a commit."""
    from dkg.code.ingest import ingest_repo

    work = _real_checkout(tmp_path, {"lib.py": "def helper():\n    return 1\n"})
    ingest_repo(db, work)

    (work / "lib.py").write_text("def helper():\n    return 2\n", encoding="utf-8")

    changed = ingest_repo(db, work)
    assert changed["mode"] == "svn-incremental"
    assert changed["parsed_files"] == 1


@requires_real_svn
def test_a_real_deleted_file_leaves_the_versioned_listing(tmp_path):
    work = _real_checkout(
        tmp_path, {"lib.py": "def helper():\n    return 1\n", "gone.py": "def gone():\n    pass\n"}
    )
    _run_svn("svn", "delete", str(work / "gone.py"), cwd=work)
    _run_svn("svn", "commit", "-m", "remove gone", cwd=work)

    listed = set(list_versioned_files_svn(work, exts={".py"}))

    assert "lib.py" in listed
    assert "gone.py" not in listed

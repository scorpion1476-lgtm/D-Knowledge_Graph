"""The forbidden-identifier scrub scan.

The scan is a regression guard, so the important thing to pin is that it can
actually catch a term, not merely that it currently reports clean. A scanner
that always passes would look identical in CI to one that works.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "scrub_scan.py"


def _load():
    spec = importlib.util.spec_from_file_location("scrub_scan", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def scrub():
    return _load()


def test_digest_file_is_present_and_well_formed(scrub):
    digests = json.loads(scrub.DIGESTS.read_text(encoding="utf-8"))
    assert isinstance(digests, list)
    assert digests, "the deny-list must not be empty; an empty list would pass everything"
    assert all(isinstance(d, str) and len(d) == 64 for d in digests)
    assert len(set(digests)) == len(digests), "duplicate digests"


def test_deny_list_holds_no_recoverable_plaintext(scrub):
    # The whole point of storing digests is that the file is safe to commit.
    raw = scrub.DIGESTS.read_text(encoding="utf-8")
    for token in ("http", "://", "@"):
        assert token not in raw


def test_scanner_detects_a_term_whose_digest_is_listed(scrub):
    # A sentinel term stands in for a forbidden one, so this test proves the
    # detection path works without putting a real forbidden string in the tree.
    sentinel = "quixotrexample"
    listed = {hashlib.sha256(sentinel.encode("utf-8")).hexdigest()}
    assert scrub._hits_in(f"a line mentioning {sentinel} here", listed) == 1
    assert scrub._hits_in(f"{sentinel} {sentinel}", listed) == 2
    assert scrub._hits_in("a line mentioning nothing forbidden", listed) == 0


def test_matching_is_case_insensitive_and_token_bounded(scrub):
    sentinel = "quixotrexample"
    listed = {hashlib.sha256(sentinel.encode("utf-8")).hexdigest()}
    assert scrub._hits_in(sentinel.upper(), listed) == 1
    assert scrub._hits_in(sentinel.capitalize(), listed) == 1
    # A longer token that merely contains the sentinel is a different token and
    # must not match, otherwise ordinary words would trip the gate.
    assert scrub._hits_in(f"{sentinel}extra", listed) == 0


def test_short_runs_are_not_tokenised(scrub):
    # The token pattern needs four or more characters; a shorter run cannot
    # carry a product name and would only add noise.
    assert scrub.TOKEN.match("ab") is None
    assert scrub.TOKEN.match("abcd") is not None


def test_tracked_tree_is_currently_clean(scrub):
    result = scrub.scan()
    assert result["digest_terms"] > 0
    assert result["files_scanned"] > 0
    assert result["ok"], f"forbidden identifier found in {result['offending_files']} file(s)"
    # The report must never carry the offending string itself.
    assert "note" in result
    for offender in result["offenders"]:
        assert set(offender) == {"file", "matches", "where"}


# -- hardening added after adversarial review -------------------------------


def test_a_forbidden_name_embedded_in_a_longer_identifier_is_caught(scrub):
    # The realistic reintroduction is not a bare term, it is FoobarClient or
    # foobar_adapter or a URL path segment. Whole-token matching missed all of
    # those, which made the gate close to useless against the actual risk.
    sentinel = "quixotrex"
    listed = {hashlib.sha256(sentinel.encode("utf-8")).hexdigest()}
    for probe in (
        sentinel,
        f"{sentinel}Client",
        f"{sentinel}_adapter",
        f"my_{sentinel}_thing",
        f"https://example.invalid/{sentinel}/v1",
        f"{sentinel.capitalize()}Plugin",
        f"pkg.{sentinel}.mod",
    ):
        assert scrub._hits_in(probe, listed) == 1, probe
    assert scrub._hits_in("an unrelated identifier", listed) == 0


def test_an_occurrence_is_counted_once_not_twice(scrub):
    sentinel = "quixotrex"
    listed = {hashlib.sha256(sentinel.encode("utf-8")).hexdigest()}
    # Whole-token match must not also count as a subtoken match.
    assert scrub._hits_in(sentinel, listed) == 1


def test_short_subtokens_do_not_match(scrub):
    listed = {hashlib.sha256(b"ab").hexdigest()}
    assert scrub._hits_in("ab_something", listed) == 0


def test_zip_container_documents_are_unzipped_and_scanned(scrub, tmp_path):
    import zipfile

    doc = tmp_path / "book.xlsx"
    with zipfile.ZipFile(doc, "w") as zf:
        zf.writestr("xl/sharedStrings.xml", "<t>quixotrex</t>")
    assert ".xlsx" in scrub.OOXML_SUFFIXES
    assert ".xlsx" not in scrub.BINARY_SUFFIXES, "an unscannable tracked document is a blind spot"
    text = scrub._ooxml_text(doc)
    assert "quixotrex" in text
    # A file that is not a zip must fail loud. Degrading to an empty string made
    # an unscannable tracked document indistinguishable from a scanned clean one.
    broken = tmp_path / "broken.xlsx"
    broken.write_bytes(b"not a zip")
    with pytest.raises(scrub.Unreadable):
        scrub._ooxml_text(broken)


def test_untracked_but_not_ignored_files_are_in_scope(scrub):
    untracked = scrub._untracked_files()
    assert isinstance(untracked, list)
    # Gitignored paths must stay out: they cannot reach the public surface, and
    # the planning corpus legitimately contains the terms being guarded against.
    assert not any(".internal-planning" in p for p in untracked)
    assert not any("PUBLIC_READINESS_REPORT" in p for p in untracked)


def test_history_scanning_reaches_blobs_no_longer_in_the_index(scrub):
    blobs = scrub._history_blobs("HEAD")
    assert blobs, "history enumeration returned nothing"
    names = {name for _sha, name in blobs}
    assert any(name.endswith(".py") for name in names)
    # The published history must be clean under the history-aware scan too.
    result = scrub.scan(history_ref="HEAD")
    assert result["history_blobs_scanned"] > 0
    assert result["ok"], f"forbidden identifier in history: {result['offenders']}"


def test_a_term_embedded_in_a_longer_identifier_is_caught(scrub):
    """The reason the gate exists is the embedded case, so it must catch it.

    A forbidden term is stored as one lowercased digest, but it appears in code
    with case and separator boundaries inside it. Splitting a token into parts
    and checking each part can never match such a digest: the parts are
    "acme" and "lens", and the digest is of "acmelens". Before adjacent parts
    were rejoined, every one of the embedded forms below passed the gate while
    the standalone form was caught, which is the failure mode most likely to
    matter in practice.
    """
    import hashlib

    digests = {hashlib.sha256(b"acmelens").hexdigest()}
    caught = [
        "AcmeLens is the tool",
        "class AcmeLens:",
        "acmelens_client = 1",
        "from acmelens.client import thing",
        "AcmeLensAdapter",
        "acme_lens_adapter",
        "AcmeLensClient",
        "acme-lens-mcp",
        "url = 'https://example.test/acme-lens/docs'",
    ]
    for sample in caught:
        assert scrub._hits_in(sample, digests) == 1, sample
    for clean in ("unrelated_identifier", "lensing_module", "acme", "an ordinary sentence"):
        assert scrub._hits_in(clean, digests) == 0, clean


def test_history_scanning_opens_zip_container_documents_too(scrub, tmp_path):
    """A tracked spreadsheet in history must not be a blind spot.

    The worktree scan unzips these documents; the history scan skipped them
    entirely, so a term removed from an older revision of a tracked spreadsheet
    would have gone unreported by the one scan documented as the pre-publish
    check.
    """
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("xl/sharedStrings.xml", "<t>quixotrex</t>")
    assert "quixotrex" in scrub._ooxml_bytes_text(buf.getvalue())
    with pytest.raises(scrub.Unreadable):
        scrub._ooxml_bytes_text(b"not a zip")


# -- coverage hardening: the scan must fail loud when it cannot read a path ---
#
# A scan that silently skips what it cannot read reports the word clean over a
# tree it only partly saw. That is worse than a scan that fails, because the
# output of the two is identical and only one of them is true. These tests pin
# the failure, not the wording.


def test_a_tracked_path_missing_from_disk_makes_the_scan_fail(scrub, monkeypatch):
    """The motivating case: `git ls-files` lists the index, not the disk.

    A tracked file removed from the worktree is still a path that gets pushed.
    Before this, `_scan_worktree_file` returned None for it and the scan counted
    it as nothing at all, so the totals dropped by one and the verdict stayed
    clean. This is the case the parallel modules could have produced by mutating
    the tree underneath a running scan.
    """
    ghost = "docs/a_tracked_file_that_is_not_on_disk.md"
    assert not (scrub.ROOT / ghost).exists()

    real_tracked = scrub._tracked_files()
    monkeypatch.setattr(scrub, "_tracked_files", lambda: [*real_tracked, ghost])

    result = scrub.scan()
    assert not result["ok"], "an unreadable in-scope path must not pass as clean"
    assert result["unreadable_files"] == 1
    entry = result["unreadable"][0]
    assert entry["file"] == ghost
    assert "missing" in entry["reason"]
    # It is a coverage failure, not a forbidden-identifier hit; the two must not
    # be conflated or the report would name an innocent file as an offender.
    assert result["offending_files"] == 0


def test_an_unopenable_tracked_file_makes_the_scan_fail(scrub, monkeypatch, tmp_path):
    """Present on disk but not readable by this process.

    Exercised through the real code path: the file exists, is not a policy skip,
    and raises OSError on open. macOS running as root can still read a 0o000
    file, so the permission bit is not a reliable trigger and the failure is
    injected at the read instead.
    """
    real_read = scrub.Path.read_text
    target = "README.md"

    def refuse(self, *a, **kw):
        if self.name == "README.md":
            raise PermissionError("refused for the test")
        return real_read(self, *a, **kw)

    monkeypatch.setattr(scrub.Path, "read_text", refuse)
    result = scrub.scan()
    assert not result["ok"]
    assert any(u["file"] == target for u in result["unreadable"]), result["unreadable"]
    assert any("could not be opened" in u["reason"] for u in result["unreadable"])


def test_a_tracked_document_that_will_not_unzip_makes_the_scan_fail(scrub, monkeypatch):
    """The tracked xlsx is real evidence, so an unopenable one is a real hole."""

    def refuse(_path):
        raise scrub.Unreadable("zip container would not open (BadZipFile)")

    monkeypatch.setattr(scrub, "_ooxml_text", refuse)
    result = scrub.scan()
    assert not result["ok"]
    assert any(u["file"].endswith(".xlsx") for u in result["unreadable"]), result["unreadable"]


def test_a_history_blob_the_object_store_will_not_produce_makes_the_scan_fail(scrub, monkeypatch):
    """A named blob that cat-file cannot emit means history was partly read."""
    real_run = scrub.subprocess.run
    state = {"tripped": False}

    def flaky(cmd, *a, **kw):
        if len(cmd) > 2 and cmd[1] == "cat-file" and not state["tripped"]:
            state["tripped"] = True
            return scrub.subprocess.CompletedProcess(cmd, 128, b"", b"object missing")
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(scrub.subprocess, "run", flaky)
    result = scrub.scan(history_ref="HEAD")
    assert state["tripped"], "the injected failure never fired; the test would be vacuous"
    assert not result["ok"]
    assert any(u["where"] == "history" for u in result["unreadable"]), result["unreadable"]


def test_git_status_failure_is_not_treated_as_an_empty_untracked_set(scrub, monkeypatch):
    """Returning [] there dropped the whole new-file surface without a word."""
    real_run = scrub.subprocess.run

    def failing(cmd, *a, **kw):
        if len(cmd) > 1 and cmd[1] == "status":
            return scrub.subprocess.CompletedProcess(cmd, 1, "", "fatal: not a git repository")
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(scrub.subprocess, "run", failing)
    with pytest.raises(SystemExit):
        scrub._untracked_files()


def test_a_clean_scan_still_reports_ok_and_counts_its_coverage(scrub):
    """Guard the guard: the failures above must not be the only outcome.

    If the hardening made every scan fail, the tests above would pass while the
    gate became useless. This pins that the unmodified tree still reads clean
    with real coverage on both dimensions.
    """
    result = scrub.scan()
    assert result["ok"]
    assert result["unreadable_files"] == 0
    assert result["files_scanned"] > 100
    assert result["policy_skipped"] > 0, "binary files should be skipped by policy, not read"

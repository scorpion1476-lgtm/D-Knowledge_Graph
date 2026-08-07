"""N-18: the project-owned indexing ignore file and its reported exclusion set."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from dkg.code.ignores import IGNORE_FILENAME, load_ignore_rules, parse_rules

try:
    import tree_sitter  # noqa: F401

    _TS = True
except Exception:
    _TS = False

_GIT = shutil.which("git") is not None

requires_ts = pytest.mark.skipif(not _TS, reason="tree-sitter not installed (the 'code' extra)")
requires_git = pytest.mark.skipif(not _GIT, reason="git is not installed in this environment")


def _rules(text):
    from dkg.code.ignores import IgnoreRules

    return IgnoreRules(parse_rules(text), source=IGNORE_FILENAME)


# -- pattern semantics --------------------------------------------------------


def test_comments_and_blank_lines_are_not_patterns():
    rules = parse_rules("# a comment\n\n   \nbuild/\n")

    assert [r.pattern for r in rules] == ["build/"]


def test_a_bare_name_matches_the_basename_at_any_depth():
    rules = _rules("*.min.js\n")

    assert rules.excludes("bundle.min.js")
    assert rules.excludes("static/js/vendor/bundle.min.js")
    assert not rules.excludes("bundle.js")


def test_a_trailing_slash_matches_a_directory_and_everything_under_it():
    rules = _rules("build/\n")

    assert rules.excludes("build/out.js")
    assert rules.excludes("build/nested/deep/out.js")
    assert not rules.excludes("src/build_helper.py")


def test_a_single_star_does_not_cross_a_separator():
    rules = _rules("vendor/*\n")

    assert rules.excludes("vendor/lib.js")
    assert not rules.excludes("vendor/a/b.js"), "a single star must not cross a slash"


def test_a_double_star_does_cross_a_separator():
    rules = _rules("vendor/**/*.go\n")

    assert rules.excludes("vendor/a/b/thing.go")
    assert rules.excludes("vendor/thing.go")
    assert not rules.excludes("src/thing.go")


def test_a_pattern_containing_a_slash_is_anchored_at_the_root():
    rules = _rules("src/generated.py\n")

    assert rules.excludes("src/generated.py")
    assert not rules.excludes("deep/src/generated.py")


def test_a_later_negation_re_includes_a_path():
    rules = _rules("src/*.py\n!src/keep.py\n")

    assert rules.excludes("src/drop.py")
    assert not rules.excludes("src/keep.py"), "the later negation must win"


def test_order_matters_so_a_later_exclusion_also_wins():
    rules = _rules("!src/keep.py\nsrc/*.py\n")

    assert rules.excludes("src/keep.py")


# -- loading ------------------------------------------------------------------


def test_an_absent_ignore_file_is_not_an_error(tmp_path):
    rules = load_ignore_rules(tmp_path)

    assert rules.present is False
    assert rules.error == ""
    assert rules.report()["pattern_count"] == 0


def test_an_oversized_ignore_file_is_reported(tmp_path):
    (tmp_path / IGNORE_FILENAME).write_text("x\n" * 600_000, encoding="utf-8")

    rules = load_ignore_rules(tmp_path)

    assert rules.present is False
    assert "cap" in rules.error


def test_the_report_names_every_pattern_and_what_it_excluded(tmp_path):
    (tmp_path / IGNORE_FILENAME).write_text("build/\n*.min.js\n", encoding="utf-8")
    rules = load_ignore_rules(tmp_path)

    kept, dropped = rules.filter(
        ["src/a.py", "build/b.js", "static/c.min.js", "build/deep/d.js"]
    )
    report = rules.report()

    assert kept == ["src/a.py"]
    assert dropped == ["build/b.js", "static/c.min.js", "build/deep/d.js"]
    assert report["source"] == IGNORE_FILENAME
    assert report["patterns"] == ["build/", "*.min.js"]
    assert report["excluded_count"] == 3
    assert report["excluded_by_pattern"] == {"*.min.js": 1, "build/": 2}
    assert set(report["excluded"]) == set(dropped)


# -- end to end ---------------------------------------------------------------


def _run(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _git_repo(root, files):
    _run(root, "init", "-q")
    _run(root, "config", "user.email", "test@example.invalid")
    _run(root, "config", "user.name", "Test")
    _run(root, "config", "commit.gpgsign", "false")
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _run(root, "add", "-A")
    _run(root, "commit", "-q", "-m", "seed")


@requires_ts
@requires_git
def test_an_ignored_tracked_file_stays_out_of_the_graph(db, tmp_path):
    """Tracked and indexed are different questions.

    The ignored file is committed to git, so nothing but the ignore file can
    keep it out of the graph.
    """
    from dkg.code.ingest import ingest_repo

    _git_repo(
        tmp_path,
        {
            "src/real.py": "def real():\n    return 1\n",
            "vendor/generated.py": "def generated():\n    return 2\n",
            IGNORE_FILENAME: "vendor/\n",
        },
    )

    result = ingest_repo(db, tmp_path)

    canonicals = {
        r["canonical"]
        for r in db.fetchall("SELECT canonical FROM entities WHERE kind LIKE 'code:%';")
    }
    assert "src/real.py::real" in canonicals
    assert not any("vendor/" in c for c in canonicals), sorted(canonicals)
    assert result["ignored"]["excluded_count"] == 1
    assert result["ignored"]["excluded"] == ["vendor/generated.py"]
    assert result["ignored"]["excluded_by_pattern"] == {"vendor/": 1}


@requires_ts
@requires_git
def test_without_the_ignore_file_the_same_repository_indexes_both(db, tmp_path):
    """The exclusion must depend on the file, not on the path's name."""
    from dkg.code.ingest import ingest_repo

    _git_repo(
        tmp_path,
        {
            "src/real.py": "def real():\n    return 1\n",
            "vendor/generated.py": "def generated():\n    return 2\n",
        },
    )

    result = ingest_repo(db, tmp_path)

    canonicals = {
        r["canonical"]
        for r in db.fetchall("SELECT canonical FROM entities WHERE kind LIKE 'code:%';")
    }
    assert "vendor/generated.py::generated" in canonicals
    assert result["ignored"]["excluded_count"] == 0
    assert result["ignored"]["source"] is None


@requires_ts
@requires_git
def test_a_negation_keeps_one_file_from_an_excluded_directory(db, tmp_path):
    from dkg.code.ingest import ingest_repo

    _git_repo(
        tmp_path,
        {
            "vendor/a.py": "def a():\n    return 1\n",
            "vendor/keep.py": "def keep():\n    return 2\n",
            IGNORE_FILENAME: "vendor/\n!vendor/keep.py\n",
        },
    )

    ingest_repo(db, tmp_path)

    canonicals = {
        r["canonical"]
        for r in db.fetchall("SELECT canonical FROM entities WHERE kind LIKE 'code:%';")
    }
    assert "vendor/keep.py::keep" in canonicals
    assert "vendor/a.py::a" not in canonicals

"""Q-11: read-only rename preview naming every file, line, and reference.

Every case here asserts the SPLIT as well as the count: an occurrence that
belongs in the ambiguous or commentary bucket must not appear in the applicable
one, because the applicable list is what the command-line apply step rewrites.
"""

from __future__ import annotations

import pytest

from dkg.code.rename import (
    REASON_NAME_NOT_UNIQUE,
    REASON_NO_EDGE,
    REASON_UNKNOWN_LANGUAGE,
    mask_non_code,
    preview_rename,
)
from dkg.core.errors import ValidationError

try:
    import tree_sitter  # noqa: F401

    _TS = True
except Exception:
    _TS = False

requires_ts = pytest.mark.skipif(not _TS, reason="tree-sitter not installed (the 'code' extra)")

if _TS:
    from dkg.code.graph import write_code_graph
    from dkg.code.parser import parse_source


def _write_repo(root, files):
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _ingest(db, root, files, language="python"):
    parsed = [parse_source(rel, text, language=language) for rel, text in files.items()]
    write_code_graph(db, parsed, dict(files), source_uri=f"code://{root}")


# -- the lexical masker, testable on its own ---------------------------------


def test_masker_blanks_python_comments_and_strings_but_keeps_code():
    text = 'target()  # call target\nname = "target"\n'
    masked = mask_non_code(text, "python")

    assert masked is not None
    assert len(masked) == len(text)
    assert masked.startswith("target()")
    # The comment marker is blanked along with its text, so the offset is taken
    # from the original and only the first line is inspected.
    first_line = masked.splitlines()[0]
    assert first_line[text.index("#") :].strip() == "", "the comment mention must be blanked"
    assert masked.splitlines()[1].strip() == "name =", "the string literal is blanked"
    assert masked.count("target") == 1, "only the call survives"


def test_masker_keeps_python_floor_division_which_is_not_a_comment():
    text = "value = target // 2\n"
    masked = mask_non_code(text, "python")

    assert masked == text, "Python has no // comment; masking one would drop real code"


def test_masker_handles_javascript_line_and_block_comments():
    text = "target();\n// target\n/* target */\nlet s = 'target';\n"
    masked = mask_non_code(text, "javascript")

    assert masked.count("target") == 1


def test_masker_returns_none_for_a_language_it_has_no_profile_for():
    assert mask_non_code("target", "brainfuck") is None


def test_masker_preserves_line_structure():
    text = "a\n# comment\nb\n"
    masked = mask_non_code(text, "python")

    assert masked.count("\n") == text.count("\n")
    assert masked.splitlines()[2] == "b"


# -- the preview -------------------------------------------------------------


def test_unresolved_target_is_reported_not_guessed(db, tmp_path):
    result = preview_rename(db, "nothing_here", "renamed", repo_root=tmp_path)

    assert result["resolved"] is False
    assert result["target"] is None
    assert result["applicable"] == []


def test_new_name_must_be_an_identifier(db, tmp_path):
    with pytest.raises(ValidationError):
        preview_rename(db, "x", "not a name", repo_root=tmp_path)
    with pytest.raises(ValidationError):
        preview_rename(db, "x", "", repo_root=tmp_path)


@requires_ts
def test_preview_names_the_definition_and_every_attributed_reference(db, tmp_path):
    files = {
        "lib.py": "def target():\n    return 1\n",
        "app.py": "from lib import target\n\n\ndef run():\n    return target()\n",
    }
    _write_repo(tmp_path, files)
    _ingest(db, tmp_path, files)

    result = preview_rename(db, "lib.py::target", "renamed", repo_root=tmp_path)

    assert result["resolved"] is True
    assert result["target"]["canonical"] == "lib.py::target"
    assert result["old_name"] == "target"

    sites = {(o["path"], o["line"]) for o in result["applicable"]}
    assert ("lib.py", 1) in sites, "the definition site must be listed"
    assert ("app.py", 5) in sites, "the call site must be listed"
    definition = [o for o in result["applicable"] if o["kind"] == "definition"]
    assert len(definition) == 1
    assert definition[0]["path"] == "lib.py"
    assert all(o["column"] >= 1 for o in result["applicable"])
    assert result["counts"]["files_touched"] >= 2


@requires_ts
def test_a_comment_mention_lands_in_commentary_not_in_applicable(db, tmp_path):
    files = {
        "lib.py": "def target():\n    return 1\n",
        "app.py": (
            "from lib import target\n"
            "\n"
            "\n"
            "def run():\n"
            "    # target is called below\n"
            "    return target()\n"
        ),
    }
    _write_repo(tmp_path, files)
    _ingest(db, tmp_path, files)

    result = preview_rename(db, "lib.py::target", "renamed", repo_root=tmp_path)

    commentary_lines = {(o["path"], o["line"]) for o in result["commentary"]}
    applicable_lines = {(o["path"], o["line"]) for o in result["applicable"]}
    assert ("app.py", 5) in commentary_lines
    assert ("app.py", 5) not in applicable_lines
    assert ("app.py", 6) in applicable_lines


@requires_ts
def test_a_name_shared_by_two_definitions_is_ambiguous_not_applicable(db, tmp_path):
    files = {
        "one.py": "def shared():\n    return 1\n",
        "two.py": "def shared():\n    return 2\n",
        "app.py": "from one import shared\n\n\ndef run():\n    return shared()\n",
    }
    _write_repo(tmp_path, files)
    _ingest(db, tmp_path, files)

    result = preview_rename(db, "one.py::shared", "renamed", repo_root=tmp_path)

    assert result["counts"]["same_name_definitions"] == 1
    assert result["applicable"] == [], "nothing is applicable while the name is not unique"
    assert result["ambiguous"], "the occurrences must still be reported"
    assert {o["reason"] for o in result["ambiguous"]} == {REASON_NAME_NOT_UNIQUE}


@requires_ts
def test_a_code_occurrence_in_an_unattributed_file_is_ambiguous(db, tmp_path):
    """A file the graph has no edge from must not be silently rewritten.

    The occurrence here is real code (a local assignment), not a comment, so it
    reaches the no-edge branch rather than the commentary one.
    """
    files = {
        "lib.py": "def target():\n    return 1\n",
        "other.py": "def unrelated():\n    target = 1\n    return target\n",
    }
    _write_repo(tmp_path, files)
    _ingest(db, tmp_path, files)

    result = preview_rename(db, "lib.py::target", "renamed", repo_root=tmp_path)

    assert not [o for o in result["applicable"] if o["path"] == "other.py"]
    other = [o for o in result["ambiguous"] if o["path"] == "other.py"]
    assert other, "the occurrence must be reported, not dropped"
    assert {o["reason"] for o in other} == {REASON_NO_EDGE}


@requires_ts
def test_a_docstring_mention_lands_in_commentary(db, tmp_path):
    files = {
        "lib.py": "def target():\n    return 1\n",
        "other.py": 'HELP = """see target for details"""\n\n\ndef unrelated():\n    return 0\n',
    }
    _write_repo(tmp_path, files)
    _ingest(db, tmp_path, files)

    result = preview_rename(db, "lib.py::target", "renamed", repo_root=tmp_path)

    assert not [o for o in result["applicable"] if o["path"] == "other.py"]
    assert [o for o in result["commentary"] if o["path"] == "other.py"]


@requires_ts
def test_an_unknown_language_makes_every_occurrence_ambiguous(db, tmp_path):
    files = {"lib.py": "def target():\n    return 1\n"}
    _write_repo(tmp_path, files)
    _ingest(db, tmp_path, files)
    # A second file in the graph whose language has no lexical profile.
    (tmp_path / "note.zzz").write_text("target\n", encoding="utf-8")
    db.execute(
        "INSERT INTO entities(entity_id, tenant_id, kind, canonical, display, metadata_json) "
        "VALUES (?,?,?,?,?,?);",
        (
            "ent-zzz",
            "local",
            "code:module",
            "note.zzz",
            "note.zzz",
            '{"path": "note.zzz", "language": "zzz", "start_line": 1, "end_line": 1}',
        ),
    )

    result = preview_rename(db, "lib.py::target", "renamed", repo_root=tmp_path)

    zzz = [o for o in result["ambiguous"] if o["path"] == "note.zzz"]
    assert zzz and zzz[0]["reason"] == REASON_UNKNOWN_LANGUAGE
    assert not [o for o in result["applicable"] if o["path"] == "note.zzz"]


@requires_ts
def test_partial_identifier_matches_are_not_occurrences(db, tmp_path):
    files = {
        "lib.py": "def target():\n    return 1\n",
        "app.py": (
            "from lib import target\n"
            "\n"
            "\n"
            "def run():\n"
            "    target_extra = 1\n"
            "    my_target = 2\n"
            "    return target() + target_extra + my_target\n"
        ),
    }
    _write_repo(tmp_path, files)
    _ingest(db, tmp_path, files)

    result = preview_rename(db, "lib.py::target", "renamed", repo_root=tmp_path)
    app_lines = sorted(o["line"] for o in result["applicable"] if o["path"] == "app.py")

    assert 5 not in app_lines, "target_extra is a different identifier"
    assert 6 not in app_lines, "my_target is a different identifier"
    assert 1 in app_lines and 7 in app_lines


@requires_ts
def test_preview_writes_nothing(db, tmp_path):
    files = {
        "lib.py": "def target():\n    return 1\n",
        "app.py": "from lib import target\n\n\ndef run():\n    return target()\n",
    }
    _write_repo(tmp_path, files)
    _ingest(db, tmp_path, files)
    before = {rel: (tmp_path / rel).read_text(encoding="utf-8") for rel in files}

    preview_rename(db, "lib.py::target", "renamed", repo_root=tmp_path)

    after = {rel: (tmp_path / rel).read_text(encoding="utf-8") for rel in files}
    assert after == before


@requires_ts
def test_a_short_name_matching_several_definitions_reports_alternatives(db, tmp_path):
    files = {
        "one.py": "def shared():\n    return 1\n",
        "two.py": "def shared():\n    return 2\n",
    }
    _write_repo(tmp_path, files)
    _ingest(db, tmp_path, files)

    result = preview_rename(db, "shared", "renamed", repo_root=tmp_path)

    assert result["resolved"] is False
    assert sorted(result["alternatives"]) == ["one.py::shared", "two.py::shared"]


@requires_ts
def test_reads_are_confined_to_the_repository_root(db, tmp_path):
    files = {"lib.py": "def target():\n    return 1\n"}
    inner = tmp_path / "repo"
    inner.mkdir()
    _write_repo(inner, files)
    _ingest(db, inner, files)
    # A graph entry pointing outside the root must be refused, not followed.
    db.execute(
        "INSERT INTO entities(entity_id, tenant_id, kind, canonical, display, metadata_json) "
        "VALUES (?,?,?,?,?,?);",
        (
            "ent-escape",
            "local",
            "code:module",
            "../outside.py",
            "outside.py",
            '{"path": "../outside.py", "language": "python", "start_line": 1, "end_line": 1}',
        ),
    )
    (tmp_path / "outside.py").write_text("target\n", encoding="utf-8")

    result = preview_rename(db, "lib.py::target", "renamed", repo_root=inner)

    assert not any(o["path"] == "../outside.py" for o in result["applicable"])
    assert any(
        u["path"] == "../outside.py" and "escapes" in u["reason"] for u in result["unreadable"]
    )


@requires_ts
def test_a_file_over_the_byte_cap_is_reported_not_read(db, tmp_path):
    files = {
        "lib.py": "def target():\n    return 1\n",
        "app.py": "from lib import target\n\n\ndef run():\n    return target()\n",
    }
    _write_repo(tmp_path, files)
    _ingest(db, tmp_path, files)

    result = preview_rename(db, "lib.py::target", "renamed", repo_root=tmp_path, max_file_bytes=10)

    assert result["applicable"] == []
    assert {u["path"] for u in result["unreadable"]} >= {"lib.py", "app.py"}
    assert all("cap" in u["reason"] for u in result["unreadable"])

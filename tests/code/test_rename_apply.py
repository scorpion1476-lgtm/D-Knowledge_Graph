"""Q-13: applying a previewed rename, command line only.

Three properties this suite exists to hold: the default writes nothing, a write
needs the confirmation asked for twice, and the capability never appears on the
read-only MCP surface.
"""

from __future__ import annotations

import pytest

from dkg.code.rename import apply_rename, preview_rename, render_diff
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


FILES = {
    "lib.py": "def target():\n    return 1\n",
    "app.py": (
        "from lib import target\n"
        "\n"
        "\n"
        "def run():\n"
        "    # target is the one below\n"
        "    return target()\n"
    ),
}


def _setup(db, root):
    for rel, text in FILES.items():
        (root / rel).write_text(text, encoding="utf-8")
    parsed = [parse_source(rel, text, language="python") for rel, text in FILES.items()]
    write_code_graph(db, parsed, dict(FILES), source_uri=f"code://{root}")
    return preview_rename(db, "lib.py::target", "renamed", repo_root=root)


# -- the MCP boundary --------------------------------------------------------


def test_rename_apply_is_not_registered_on_the_read_only_mcp_surface(db):
    """The deliberate divergence: no write tool behind the MCP trust boundary."""
    from dkg.mcp.tools import build_read_registry

    registry = build_read_registry(db)
    names = set(registry.tools)

    # Guard the guard: every assertion below is vacuously true on an empty
    # registry, so the registry has to be shown to be populated first.
    assert len(names) > 20, "the registry must actually be built"
    assert "dkg.code.rename.preview" in names, "the read-only half IS served"
    assert not any("rename" in n and "apply" in n for n in names)
    assert all(spec.kind == "read" for spec in registry.tools.values())
    # The read-only preview may be served; applying may not.
    assert "dkg.code.rename.apply" not in names


# -- refusal to write --------------------------------------------------------


@requires_ts
def test_default_is_a_dry_run_that_writes_nothing(db, tmp_path):
    preview = _setup(db, tmp_path)
    before = {rel: (tmp_path / rel).read_text(encoding="utf-8") for rel in FILES}

    result = apply_rename(preview, repo_root=tmp_path)

    assert result["applied"] is False
    assert result["dry_run"] is True
    assert result["files_changed"] == 0
    after = {rel: (tmp_path / rel).read_text(encoding="utf-8") for rel in FILES}
    assert after == before


@requires_ts
def test_confirm_alone_is_not_enough(db, tmp_path):
    preview = _setup(db, tmp_path)
    before = (tmp_path / "lib.py").read_text(encoding="utf-8")

    result = apply_rename(preview, repo_root=tmp_path, confirm=True)

    assert result["applied"] is False
    assert (tmp_path / "lib.py").read_text(encoding="utf-8") == before


@requires_ts
def test_dry_run_false_without_confirm_is_not_enough(db, tmp_path):
    preview = _setup(db, tmp_path)
    before = (tmp_path / "lib.py").read_text(encoding="utf-8")

    result = apply_rename(preview, repo_root=tmp_path, dry_run=False)

    assert result["applied"] is False
    assert (tmp_path / "lib.py").read_text(encoding="utf-8") == before


def test_an_unresolved_preview_cannot_be_applied(db, tmp_path):
    preview = preview_rename(db, "missing", "renamed", repo_root=tmp_path)

    with pytest.raises(ValidationError):
        apply_rename(preview, repo_root=tmp_path, confirm=True, dry_run=False)


# -- the diff ----------------------------------------------------------------


@requires_ts
def test_dry_run_returns_a_unified_diff_of_exactly_what_would_change(db, tmp_path):
    preview = _setup(db, tmp_path)

    diff = apply_rename(preview, repo_root=tmp_path)["diff"]

    assert "--- a/lib.py" in diff and "+++ b/lib.py" in diff
    assert "-def target():" in diff
    assert "+def renamed():" in diff
    assert "-    return target()" in diff
    assert "+    return renamed()" in diff
    # The comment line is in the commentary bucket, so it must not be in the diff.
    assert "# renamed is the one below" not in diff
    assert render_diff(preview, repo_root=tmp_path) == diff


# -- the write ---------------------------------------------------------------


@requires_ts
def test_apply_rewrites_only_the_applicable_occurrences(db, tmp_path):
    preview = _setup(db, tmp_path)

    result = apply_rename(preview, repo_root=tmp_path, confirm=True, dry_run=False)

    assert result["applied"] is True
    assert result["files_changed"] == 2
    assert sorted(result["files"]) == ["app.py", "lib.py"]

    lib = (tmp_path / "lib.py").read_text(encoding="utf-8")
    app = (tmp_path / "app.py").read_text(encoding="utf-8")
    assert lib == "def renamed():\n    return 1\n"
    assert "from lib import renamed" in app
    assert "return renamed()" in app
    # The comment kept the old name: it was never applicable.
    assert "# target is the one below" in app
    assert app.count("renamed") == 2


@requires_ts
def test_apply_is_a_no_op_when_nothing_is_applicable(db, tmp_path):
    """A preview whose occurrences are all ambiguous must not write."""
    files = {
        "one.py": "def shared():\n    return 1\n",
        "two.py": "def shared():\n    return 2\n",
    }
    for rel, text in files.items():
        (tmp_path / rel).write_text(text, encoding="utf-8")
    parsed = [parse_source(rel, text, language="python") for rel, text in files.items()]
    write_code_graph(db, parsed, dict(files), source_uri=f"code://{tmp_path}")
    preview = preview_rename(db, "one.py::shared", "renamed", repo_root=tmp_path)
    assert preview["applicable"] == []

    result = apply_rename(preview, repo_root=tmp_path, confirm=True, dry_run=False)

    assert result["files_changed"] == 0
    assert (tmp_path / "one.py").read_text(encoding="utf-8") == files["one.py"]
    assert (tmp_path / "two.py").read_text(encoding="utf-8") == files["two.py"]


@requires_ts
def test_two_occurrences_on_one_line_are_handled_positionally(db, tmp_path):
    files = {
        "lib.py": "def target():\n    return 1\n",
        "app.py": "from lib import target\n\n\ndef run():\n    return target() + target()\n",
    }
    for rel, text in files.items():
        (tmp_path / rel).write_text(text, encoding="utf-8")
    parsed = [parse_source(rel, text, language="python") for rel, text in files.items()]
    write_code_graph(db, parsed, dict(files), source_uri=f"code://{tmp_path}")
    preview = preview_rename(db, "lib.py::target", "renamed", repo_root=tmp_path)

    apply_rename(preview, repo_root=tmp_path, confirm=True, dry_run=False)

    app = (tmp_path / "app.py").read_text(encoding="utf-8")
    assert "return renamed() + renamed()" in app
    assert "target" not in app


@requires_ts
def test_a_bad_new_name_is_refused_before_anything_is_written(db, tmp_path):
    preview = _setup(db, tmp_path)
    before = (tmp_path / "lib.py").read_text(encoding="utf-8")

    for bad in ("not an identifier", "", "9lives", "has-a-hyphen"):
        preview["new_name"] = bad
        with pytest.raises(ValidationError):
            apply_rename(preview, repo_root=tmp_path, confirm=True, dry_run=False)
        assert (tmp_path / "lib.py").read_text(encoding="utf-8") == before, bad

"""T-11: a browsable markdown knowledge base from the community structure."""

from __future__ import annotations

import pytest

from dkg.code.wiki import ADVISORY, INDEX_NAME, MANIFEST_NAME, build_wiki

try:
    import tree_sitter  # noqa: F401

    _TS = True
except Exception:
    _TS = False

requires_ts = pytest.mark.skipif(not _TS, reason="tree-sitter not installed (the 'code' extra)")

if _TS:
    from dkg.code.graph import write_code_graph
    from dkg.code.parser import parse_source


def _cluster(prefix: str, size: int = 4) -> str:
    return "\n".join(
        f"def {prefix}{i}():\n    return "
        + " + ".join(f"{prefix}{j}()" for j in range(size) if j != i)
        + "\n"
        for i in range(size)
    )


FILES = {
    "alpha.py": _cluster("a") + "\ndef main():\n    return a0()\n",
    "beta.py": _cluster("b"),
    "bridge.py": "from alpha import a0\nfrom beta import b0\n\n\ndef link():\n    return a0() + b0()\n",
}


def _ingest(db, files=None):
    files = FILES if files is None else files
    parsed = [parse_source(rel, text, language="python") for rel, text in files.items()]
    write_code_graph(db, parsed, dict(files), source_uri="test://wiki")


def test_an_empty_graph_still_produces_an_index(db, tmp_path):
    result = build_wiki(db, tmp_path)

    assert (tmp_path / INDEX_NAME).exists()
    assert result["communities"] == 0
    assert INDEX_NAME in result["written"]


@requires_ts
def test_one_page_per_community_plus_an_index(db, tmp_path):
    _ingest(db)

    result = build_wiki(db, tmp_path)

    pages = sorted(p.name for p in tmp_path.glob("*.md"))
    assert INDEX_NAME in pages
    assert len(pages) == result["communities"] + 1
    assert result["communities"] >= 2, "the fixture holds at least two clusters"


@requires_ts
def test_a_community_page_carries_members_entry_points_and_crossing_edges(db, tmp_path):
    _ingest(db)
    build_wiki(db, tmp_path)

    pages = [p for p in tmp_path.glob("community-*.md")]
    texts = {p.name: p.read_text(encoding="utf-8") for p in pages}

    assert any("## Members" in t for t in texts.values())
    assert all("## Entry points" in t for t in texts.values())
    assert all("## Edges leaving this community" in t for t in texts.values())
    # main is entry-point evidence and must be named as one on some page.
    assert any("main" in t.split("## Members")[0] for t in texts.values()), (
        "main should appear in an entry-points section"
    )
    # The bridging symbol produces a crossing edge somewhere.
    assert any("bridge.py::link" in t for t in texts.values())


@requires_ts
def test_every_page_carries_the_advisory_label(db, tmp_path):
    _ingest(db)
    build_wiki(db, tmp_path)

    for page in tmp_path.glob("*.md"):
        assert ADVISORY in page.read_text(encoding="utf-8"), page.name


@requires_ts
def test_the_index_links_to_every_community_page(db, tmp_path):
    _ingest(db)
    result = build_wiki(db, tmp_path)

    index = (tmp_path / INDEX_NAME).read_text(encoding="utf-8")
    for name in result["pages"]:
        if name == INDEX_NAME:
            continue
        assert f"({name})" in index, name


@requires_ts
def test_a_community_page_links_back_to_the_index(db, tmp_path):
    _ingest(db)
    build_wiki(db, tmp_path)

    for page in tmp_path.glob("community-*.md"):
        assert f"({INDEX_NAME})" in page.read_text(encoding="utf-8")


# -- incremental regeneration -------------------------------------------------


@requires_ts
def test_regenerating_an_unchanged_graph_writes_nothing(db, tmp_path):
    _ingest(db)
    first = build_wiki(db, tmp_path)
    assert first["written"]

    second = build_wiki(db, tmp_path)

    assert second["written"] == [], second["written"]
    assert sorted(second["unchanged"]) == sorted(first["written"])


@requires_ts
def test_the_full_flag_rewrites_everything(db, tmp_path):
    _ingest(db)
    build_wiki(db, tmp_path)

    forced = build_wiki(db, tmp_path, incremental=False)

    assert forced["unchanged"] == []
    assert sorted(forced["written"]) == sorted(forced["pages"])


@requires_ts
def test_a_changed_graph_rewrites_the_pages_that_changed(db, tmp_path):
    _ingest(db)
    build_wiki(db, tmp_path)

    extra = {"gamma.py": _cluster("g")}
    write_code_graph(
        db,
        [parse_source(r, t, language="python") for r, t in extra.items()],
        extra,
        source_uri="test://wiki",
    )
    again = build_wiki(db, tmp_path)

    assert again["written"], "a new cluster must produce at least one new page"
    assert INDEX_NAME in again["written"], "the index totals changed"


@requires_ts
def test_a_page_whose_community_is_gone_is_removed(db, tmp_path):
    _ingest(db)
    first = build_wiki(db, tmp_path)
    assert first["communities"] >= 2

    db.execute("DELETE FROM relationships;")
    db.execute("DELETE FROM entities;")
    _ingest(db, {"solo.py": "def only():\n    return 1\n"})
    second = build_wiki(db, tmp_path)

    assert second["removed"], "stale pages must be deleted, not left to be read as current"
    for name in second["removed"]:
        assert not (tmp_path / name).exists()


@requires_ts
def test_the_manifest_is_written_and_is_not_a_page(db, tmp_path):
    _ingest(db)
    result = build_wiki(db, tmp_path)

    assert (tmp_path / MANIFEST_NAME).exists()
    assert MANIFEST_NAME not in result["pages"]


@requires_ts
def test_a_corrupt_manifest_causes_a_full_rewrite_rather_than_a_failure(db, tmp_path):
    _ingest(db)
    build_wiki(db, tmp_path)
    (tmp_path / MANIFEST_NAME).write_text("not json", encoding="utf-8")

    result = build_wiki(db, tmp_path)

    assert sorted(result["written"]) == sorted(result["pages"])


@requires_ts
def test_a_pipe_in_a_symbol_name_cannot_break_a_table(db, tmp_path):
    files = {"weird.py": "def a():\n    return 1\n"}
    _ingest(db, files)
    db.execute(
        "INSERT INTO entities(entity_id, tenant_id, kind, canonical, display, metadata_json) "
        "VALUES (?,?,?,?,?,?);",
        (
            "ent-pipe",
            "local",
            "code:function",
            "weird.py::has|pipe",
            "has|pipe",
            '{"path": "weird.py", "language": "python", "start_line": 1, "end_line": 1}',
        ),
    )

    build_wiki(db, tmp_path)

    text = "".join(p.read_text(encoding="utf-8") for p in tmp_path.glob("community-*.md"))
    assert "has\\|pipe" in text, "the pipe must be escaped so the table survives"

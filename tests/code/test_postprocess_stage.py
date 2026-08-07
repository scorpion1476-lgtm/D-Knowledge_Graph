"""N-22: post-processing as a named stage that can be re-run, skipped, or reduced."""

from __future__ import annotations

import pytest

from dkg.code.postprocess import (
    DEFAULT_LEVEL,
    LEVEL_STAGES,
    LEVELS,
    STAGE_COMMUNITIES,
    STAGE_FLOWS,
    STAGE_INDEX,
    STAGE_RISK,
    STAGES,
    graph_revision,
    last_run,
    resolve_level,
    run_postprocess,
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


FILES = {
    "app.py": (
        "def main():\n"
        "    return handler()\n"
        "\n"
        "def handler():\n"
        "    return helper()\n"
        "\n"
        "def helper():\n"
        "    return 1\n"
    ),
    "other.py": "def unrelated():\n    return 2\n",
}


def _ingest(db):
    parsed = [parse_source(rel, text, language="python") for rel, text in FILES.items()]
    write_code_graph(db, parsed, dict(FILES), source_uri="test://postprocess")


def _stage(result, name):
    return next(s for s in result["stages"] if s["stage"] == name)


# -- the level contract -------------------------------------------------------


def test_levels_are_ordered_cheapest_first_and_each_includes_the_one_below():
    for smaller, larger in zip(LEVELS, LEVELS[1:], strict=False):
        assert set(LEVEL_STAGES[smaller]) <= set(LEVEL_STAGES[larger]), (smaller, larger)
    assert LEVEL_STAGES["none"] == ()
    assert set(LEVEL_STAGES["full"]) == set(STAGES)


def test_an_unknown_level_is_refused_loudly():
    with pytest.raises(ValidationError, match="unknown post-processing level"):
        resolve_level("enormous")


def test_an_unknown_stage_is_refused_loudly(db):
    with pytest.raises(ValidationError, match="unknown post-processing stage"):
        run_postprocess(db, stages=("not_a_stage",))


def test_the_default_level_is_a_real_level():
    assert DEFAULT_LEVEL in LEVEL_STAGES


# -- skipping -----------------------------------------------------------------


@requires_ts
def test_level_none_derives_nothing(db):
    _ingest(db)

    result = run_postprocess(db, level="none")

    assert result["stages_run"] == []
    assert result["level_applied"] == "none"
    for stage in STAGES:
        assert _stage(result, stage)["ran"] is False
    assert db.fetchone("SELECT COUNT(*) AS n FROM code_flows;")["n"] == 0
    assert db.fetchone("SELECT COUNT(*) AS n FROM code_community_summaries;")["n"] == 0


@requires_ts
def test_a_skipped_stage_says_why_it_was_skipped(db):
    _ingest(db)

    result = run_postprocess(db, level="minimal")

    assert _stage(result, STAGE_COMMUNITIES)["ran"] is True
    flows = _stage(result, STAGE_FLOWS)
    assert flows["ran"] is False
    assert "not part of level 'minimal'" in flows["reason"]


# -- reducing -----------------------------------------------------------------


@requires_ts
def test_each_level_runs_exactly_its_stages(db):
    _ingest(db)

    for level in LEVELS:
        result = run_postprocess(db, level=level)
        ran = set(result["stages_run"])
        expected = set(LEVEL_STAGES[level])
        # The index stage needs a staged embedding model, so it may honestly
        # not run. Everything else must.
        assert ran <= expected, (level, ran, expected)
        assert (expected - ran) <= {STAGE_INDEX}, (level, ran, expected)


@requires_ts
def test_the_level_applied_is_reported_not_the_level_requested(db, monkeypatch):
    """A stage whose capability is absent must LOWER the applied level.

    The capability is forced absent rather than branched on. Branching on
    whatever this machine happens to have means the discrimination is only
    tested where it does not fire, and a build that reported the requested
    level as the applied one would pass everywhere.
    """
    import dkg.adapters.embedding as embedding

    _ingest(db)

    class _Unavailable:
        name = "hashing"
        dimension = 8

        def available(self):
            return False, "no model staged in this test"

    monkeypatch.setattr(embedding, "default_embedding_adapter", lambda *a, **k: _Unavailable())

    result = run_postprocess(db, level="full")

    index = _stage(result, STAGE_INDEX)
    assert index["ran"] is False
    assert "no real embedding model staged" in index["reason"]
    assert result["level_requested"] == "full"
    assert result["level_applied"] == "standard", (
        "full was requested, the index stage could not run, so standard is what held"
    )
    # The stages that CAN run still do; a missing capability lowers the level,
    # it does not abandon the run.
    assert set(result["stages_run"]) == {STAGE_COMMUNITIES, STAGE_FLOWS, STAGE_RISK}


@requires_ts
def test_the_applied_level_reaches_full_when_every_stage_runs(db):
    """The other half: the report must not be permanently pessimistic."""
    _ingest(db)

    result = run_postprocess(db, level="full")

    if _stage(result, STAGE_INDEX)["ran"]:
        assert result["level_applied"] == "full"
    else:
        pytest.skip("no real embedding model staged in this environment")


# -- re-running a single stage ------------------------------------------------


@requires_ts
def test_one_stage_can_be_re_run_on_its_own(db):
    _ingest(db)
    run_postprocess(db, level="standard")
    flows_before = db.fetchone("SELECT COUNT(*) AS n FROM code_flows;")["n"]
    assert flows_before > 0

    result = run_postprocess(db, stages=(STAGE_RISK,))

    assert result["stages_run"] == [STAGE_RISK]
    assert _stage(result, STAGE_COMMUNITIES)["ran"] is False
    # The other stages' output survives: re-running one must not wipe the rest.
    assert db.fetchone("SELECT COUNT(*) AS n FROM code_flows;")["n"] == flows_before
    assert db.fetchone("SELECT COUNT(*) AS n FROM code_symbol_risk;")["n"] > 0


@requires_ts
def test_re_running_a_stage_replaces_rather_than_duplicates(db):
    _ingest(db)
    run_postprocess(db, level="minimal")
    first = db.fetchone("SELECT COUNT(*) AS n FROM code_community_summaries;")["n"]

    run_postprocess(db, level="minimal")

    assert db.fetchone("SELECT COUNT(*) AS n FROM code_community_summaries;")["n"] == first


# -- revision tracking --------------------------------------------------------


@requires_ts
def test_the_revision_changes_when_the_graph_does(db):
    _ingest(db)
    before = graph_revision(db)

    extra = {"third.py": "def added():\n    return 3\n"}
    write_code_graph(
        db,
        [parse_source(r, t, language="python") for r, t in extra.items()],
        extra,
        source_uri="test://postprocess",
    )

    assert graph_revision(db) != before


@requires_ts
def test_the_last_run_is_recorded_and_reports_whether_it_is_current(db):
    _ingest(db)
    run_postprocess(db, level="standard")

    recorded = last_run(db)

    assert recorded is not None
    assert recorded["level"] == "standard"
    assert recorded["current"] is True

    extra = {"fourth.py": "def more():\n    return 4\n"}
    write_code_graph(
        db,
        [parse_source(r, t, language="python") for r, t in extra.items()],
        extra,
        source_uri="test://postprocess",
    )

    assert last_run(db)["current"] is False, "the graph moved, so the run is stale"


def test_no_run_recorded_yet_returns_none(db):
    assert last_run(db) is None


# -- integration with ingest --------------------------------------------------


@requires_ts
def test_ingest_reports_the_level_it_applied(db, tmp_path):
    from dkg.code.ingest import ingest_repo

    (tmp_path / "a.py").write_text(FILES["app.py"], encoding="utf-8")

    result = ingest_repo(db, tmp_path)

    assert result["postprocess"]["level_requested"] == DEFAULT_LEVEL
    assert result["postprocess"]["level_applied"] in LEVELS
    assert set(result["postprocess"]["stages_run"]) <= set(STAGES)


@requires_ts
def test_ingest_can_skip_post_processing_entirely(db, tmp_path):
    from dkg.code.ingest import ingest_repo

    (tmp_path / "a.py").write_text(FILES["app.py"], encoding="utf-8")

    result = ingest_repo(db, tmp_path, postprocess="none")

    assert result["postprocess"]["stages_run"] == []
    assert result["nodes"] > 0, "the graph itself is still written"
    assert db.fetchone("SELECT COUNT(*) AS n FROM code_flows;")["n"] == 0

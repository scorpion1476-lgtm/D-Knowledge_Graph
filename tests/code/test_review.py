"""Suggested review questions generated from the graph analysis.

Gated on tree-sitter (the 'code' extra); skips honestly when absent.
"""

from __future__ import annotations

import pytest

try:
    import tree_sitter  # noqa: F401

    _TS = True
except Exception:
    _TS = False

requires_ts = pytest.mark.skipif(not _TS, reason="tree-sitter not installed (the 'code' extra)")

from dkg.code.review import CATEGORY_WEIGHTS, collect_analyses, review_questions  # noqa: E402

if _TS:
    from dkg.code.graph import write_code_graph
    from dkg.code.parser import parse_source


def _ingest(db, files):
    parsed = [parse_source(p, t, language=lang) for p, t, lang in files]
    write_code_graph(db, parsed, {p: t for p, t, _ in files}, source_uri="test://review")


# A hub called by several symbols, an unreferenced function, and a chain that
# makes one symbol a genuine cut vertex.
CORE = (
    "core.py",
    "def util():\n    return 1\n"
    "def hub():\n    return util()\n"
    "def alpha():\n    return hub()\n"
    "def beta():\n    return hub()\n"
    "def gamma():\n    return hub()\n"
    "def orphan():\n    return 0\n",
    "python",
)


@requires_ts
def test_questions_are_generated_with_evidence_and_priority(db):
    _ingest(db, [CORE])
    result = review_questions(db)
    assert result["questions"], "a graph with a hub and an orphan should raise questions"
    for q in result["questions"]:
        assert q["question"].endswith("?"), q["question"]
        assert q["category"] in CATEGORY_WEIGHTS
        assert 0.0 <= q["priority"] <= 1.0
        assert q["evidence"], q["id"]
        # Priority must be reconstructible from its two published parts.
        assert q["priority"] == pytest.approx(q["category_weight"] * q["signal_strength"], abs=1e-6)


@requires_ts
def test_isolated_symbol_produces_a_question_naming_it(db):
    _ingest(db, [CORE])
    result = review_questions(db, limit=100)
    isolated = [q for q in result["questions"] if q["category"] == "isolated"]
    assert any("orphan" in q["subject"] for q in isolated)


@requires_ts
def test_untested_hotspot_question_reports_real_caller_count(db):
    _ingest(db, [CORE])
    result = review_questions(db, limit=100)
    hotspots = [q for q in result["questions"] if q["category"] == "untested_hotspot"]
    assert hotspots, "hub has three callers and no test edge"
    hub = next((q for q in hotspots if q["subject"].endswith("::hub")), None)
    assert hub is not None
    assert hub["evidence"]["inbound_calls"] == 3
    assert "3 other symbols" in hub["question"]


@requires_ts
def test_a_tested_symbol_stops_producing_an_untested_question(db):
    tested = (
        "svc.py",
        "def worker():\n    return 1\ndef a():\n    return worker()\ndef b():\n    return worker()\n",
        "python",
    )
    test_file = (
        "test_svc.py",
        "def test_worker():\n    return worker()\n",
        "python",
    )
    # The test file must be ingested alongside the code it exercises: edge
    # resolution only sees the files passed in one write_code_graph call.
    _ingest(db, [tested, test_file])
    result = review_questions(db, limit=100)
    subjects = {q["subject"] for q in result["questions"] if q["category"] == "untested_hotspot"}
    assert "svc.py::worker" not in subjects


@requires_ts
def test_questions_are_ordered_by_priority_then_id(db):
    _ingest(db, [CORE])
    result = review_questions(db, limit=100)
    keys = [(-q["priority"], q["id"]) for q in result["questions"]]
    assert keys == sorted(keys)


@requires_ts
def test_ids_are_stable_and_unique(db):
    _ingest(db, [CORE])
    first = review_questions(db, limit=100)["questions"]
    second = review_questions(db, limit=100)["questions"]
    assert [q["id"] for q in first] == [q["id"] for q in second]
    assert len({q["id"] for q in first}) == len(first)


@requires_ts
def test_a_chokepoint_is_not_also_asked_about_as_a_plain_hub(db):
    _ingest(db, [CORE])
    result = review_questions(db, limit=100)
    chokepoints = {q["subject"] for q in result["questions"] if q["category"] == "chokepoint"}
    hubs = {q["subject"] for q in result["questions"] if q["category"] == "hub"}
    assert not (chokepoints & hubs), "the same symbol should not be asked about twice"


@requires_ts
def test_limit_and_per_category_bound_the_output(db):
    _ingest(db, [CORE])
    result = review_questions(db, limit=3)
    assert len(result["questions"]) == 3
    assert result["totals"]["returned"] == 3
    assert result["totals"]["generated"] >= 3
    capped = review_questions(db, limit=100, per_category=1)
    counts: dict[str, int] = {}
    for q in capped["questions"]:
        counts[q["category"]] = counts.get(q["category"], 0) + 1
    assert all(n <= 1 for n in counts.values()), counts


@requires_ts
def test_precomputed_analyses_are_reused_rather_than_recomputed(db):
    _ingest(db, [CORE])
    analyses = collect_analyses(db)
    from_shared = review_questions(db, limit=100, analyses=analyses)
    fresh = review_questions(db, limit=100)
    assert from_shared["questions"] == fresh["questions"]


@requires_ts
def test_totals_and_category_breakdown_are_consistent(db):
    _ingest(db, [CORE])
    result = review_questions(db, limit=100)
    assert sum(result["totals"]["by_category"].values()) == result["totals"]["generated"]
    assert result["totals"]["returned"] == len(result["questions"])


def test_empty_graph_yields_no_questions_and_does_not_raise(db):
    result = review_questions(db)
    assert result["questions"] == []
    assert result["totals"]["generated"] == 0
    assert "prompts for a reviewer" in result["why"]["note"]

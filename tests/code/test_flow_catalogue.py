"""T-13: the persisted execution-flow catalogue and its three queries."""

from __future__ import annotations

import pytest

from dkg.code.catalogue import flows_affected_by, get_flow, list_flows
from dkg.code.postprocess import run_postprocess

try:
    import tree_sitter  # noqa: F401

    _TS = True
except Exception:
    _TS = False

requires_ts = pytest.mark.skipif(not _TS, reason="tree-sitter not installed (the 'code' extra)")

if _TS:
    from dkg.code.graph import write_code_graph
    from dkg.code.parser import parse_source


# main reaches handler reaches helper, across three files.
FILES = {
    "cli.py": "from svc import handler\n\n\ndef main():\n    return handler()\n",
    "svc.py": "from util import helper\n\n\ndef handler():\n    return helper()\n",
    "util.py": "def helper():\n    return 1\n",
    "test_svc.py": "from svc import handler\n\n\ndef test_handler():\n    return handler()\n",
    "lonely.py": "def unreached():\n    return 2\n",
}


def _ingest_and_catalogue(db):
    parsed = [parse_source(rel, text, language="python") for rel, text in FILES.items()]
    write_code_graph(db, parsed, dict(FILES), source_uri="test://flows")
    return run_postprocess(db, level="standard")


# -- nothing built yet --------------------------------------------------------


def test_listing_before_anything_is_built_says_so(db):
    result = list_flows(db)

    assert result["flows"] == []
    assert result["source"] == "not precomputed"
    assert "dkg code-postprocess" in result["reason"]


def test_retrieving_before_anything_is_built_says_so(db):
    result = get_flow(db, "anything")

    assert result["flow"] is None
    assert result["source"] == "not precomputed"


def test_the_affected_query_before_anything_is_built_says_so(db):
    result = flows_affected_by(db, ["cli.py"])

    assert result["flows"] == []
    assert result["source"] == "not precomputed"


# -- listing ------------------------------------------------------------------


@requires_ts
def test_flows_are_detected_once_and_persisted(db):
    report = _ingest_and_catalogue(db)

    flows_stage = next(s for s in report["stages"] if s["stage"] == "flows")
    assert flows_stage["ran"] is True
    assert flows_stage["flows"] >= 1
    assert db.fetchone("SELECT COUNT(*) AS n FROM code_flows;")["n"] == flows_stage["flows"]


@requires_ts
def test_listing_returns_ranked_order_highest_first(db):
    _ingest_and_catalogue(db)

    result = list_flows(db)

    assert result["source"] == "precomputed"
    assert result["flows"], "main and the test are both entry points"
    scores = [f["rank_score"] for f in result["flows"]]
    assert scores == sorted(scores, reverse=True)
    names = [f["name"] for f in result["flows"]]
    assert "cli.py::main" in names
    assert "the maximum observed in THIS catalogue" in result["ranking"]


@requires_ts
def test_a_flow_that_reaches_nothing_is_not_catalogued(db):
    """A one-step flow is an entry point standing alone, not a flow."""
    _ingest_and_catalogue(db)

    names = {f["name"] for f in list_flows(db)["flows"]}

    assert "lonely.py::unreached" not in names


# -- retrieval ----------------------------------------------------------------


@requires_ts
def test_a_flow_is_retrievable_by_name_with_its_ordered_steps(db):
    _ingest_and_catalogue(db)

    result = get_flow(db, "cli.py::main")

    flow = result["flow"]
    assert flow is not None
    assert flow["entry"] == "cli.py::main"
    steps = flow["steps"]
    assert [s["order"] for s in steps] == list(range(len(steps))), "steps are ordered"
    canonicals = [s["canonical"] for s in steps]
    assert canonicals[0] == "cli.py::main"
    assert "svc.py::handler" in canonicals
    assert "util.py::helper" in canonicals
    # Depth increases along the chain, which is what makes it a flow rather
    # than a set.
    by_name = {s["canonical"]: s["depth"] for s in steps}
    assert by_name["cli.py::main"] < by_name["svc.py::handler"] < by_name["util.py::helper"]


@requires_ts
def test_a_flow_is_retrievable_by_its_stable_identifier(db):
    _ingest_and_catalogue(db)
    by_name = get_flow(db, "cli.py::main")["flow"]

    by_id = get_flow(db, by_name["flow_id"])["flow"]

    assert by_id == by_name


@requires_ts
def test_the_identifier_is_stable_across_recomputation(db):
    _ingest_and_catalogue(db)
    first = get_flow(db, "cli.py::main")["flow"]["flow_id"]

    run_postprocess(db, stages=("flows",))

    assert get_flow(db, "cli.py::main")["flow"]["flow_id"] == first


@requires_ts
def test_an_unknown_name_is_reported_not_invented(db):
    _ingest_and_catalogue(db)

    result = get_flow(db, "nope.py::nothing")

    assert result["flow"] is None
    assert result["source"] == "precomputed"
    assert "no catalogued flow" in result["reason"]


# -- affected by a change -----------------------------------------------------


@requires_ts
def test_a_changed_file_names_the_flows_that_pass_through_it(db):
    _ingest_and_catalogue(db)

    result = flows_affected_by(db, ["util.py"])

    names = {f["name"] for f in result["flows"]}
    assert "cli.py::main" in names, "main reaches helper in util.py"
    assert result["changed_files"] == ["util.py"]
    assert all(f["files_touched"] >= 1 for f in result["flows"])


@requires_ts
def test_a_file_no_flow_passes_through_is_reported_as_such(db):
    _ingest_and_catalogue(db)

    result = flows_affected_by(db, ["lonely.py"])

    assert result["flows"] == []
    assert result["files_in_no_flow"] == ["lonely.py"]


@requires_ts
def test_flows_touching_more_of_the_change_set_rank_first(db):
    _ingest_and_catalogue(db)

    result = flows_affected_by(db, ["cli.py", "svc.py", "util.py"])

    touched = [f["files_touched"] for f in result["flows"]]
    assert touched == sorted(touched, reverse=True)
    assert result["flows"][0]["name"] == "cli.py::main"


@requires_ts
def test_an_empty_change_set_returns_nothing_without_error(db):
    _ingest_and_catalogue(db)

    result = flows_affected_by(db, [])

    assert result["flows"] == []
    assert result["changed_files"] == []


# -- staleness ----------------------------------------------------------------


@requires_ts
def test_a_catalogue_from_an_earlier_graph_is_reported_stale(db):
    _ingest_and_catalogue(db)
    assert list_flows(db)["current"] is True

    extra = {"new.py": "def added():\n    return 9\n"}
    write_code_graph(
        db,
        [parse_source(r, t, language="python") for r, t in extra.items()],
        extra,
        source_uri="test://flows",
    )

    stale = list_flows(db)
    assert stale["current"] is False
    assert "STALE" in stale["note"]
    assert get_flow(db, "cli.py::main")["current"] is False

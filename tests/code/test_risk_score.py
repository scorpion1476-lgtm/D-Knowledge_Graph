"""Q-07: the advisory change risk score, its factors, and its named levels."""

from __future__ import annotations

import pytest

from dkg.code.risk import (
    LEVEL_NAMES,
    SECURITY_TERMS,
    WEIGHTS,
    change_risk,
    is_security_sensitive,
)

try:
    import tree_sitter  # noqa: F401

    _TS = True
except Exception:
    _TS = False

requires_ts = pytest.mark.skipif(not _TS, reason="tree-sitter not installed (the 'code' extra)")

if _TS:
    from dkg.code.graph import write_code_graph
    from dkg.code.parser import parse_source


def _ingest(db, files, uri="test://risk"):
    parsed = [parse_source(rel, text, language="python") for rel, text in files.items()]
    write_code_graph(db, parsed, dict(files), source_uri=uri)


def _factor(symbol: dict, name: str) -> dict:
    return next(f for f in symbol["factors"] if f["factor"] == name)


# A shape with a clear spread: hub is called by three peers and reached from
# main; lonely is called by nobody.
REPO = {
    "core.py": (
        "def hub():\n"
        "    return 1\n"
        "\n"
        "def lonely():\n"
        "    return 0\n"
    ),
    "a.py": "from core import hub\n\n\ndef one():\n    return hub()\n",
    "b.py": "from core import hub\n\n\ndef two():\n    return hub()\n",
    "c.py": "from core import hub\n\n\ndef main():\n    return hub()\n",
}


def test_weights_sum_to_one_which_is_what_bounds_the_score():
    assert round(sum(WEIGHTS.values()), 6) == 1.0
    assert set(WEIGHTS) == {
        "execution_flow_participation",
        "caller_count",
        "community_crossing",
        "test_coverage",
        "security_sensitive_naming",
    }


def test_security_vocabulary_matches_name_and_path_and_nothing_else():
    assert is_security_sensitive("verify_token")
    assert is_security_sensitive("handler", path="auth/session.py")
    assert not is_security_sensitive("compute_total", path="billing/report.py")


def test_empty_graph_scores_zero_and_says_why(db):
    result = change_risk(db, files=["nothing.py"])

    assert result["change_score"] == 0.0
    assert result["symbol_count"] == 0
    assert "nothing was scored" in result["why"]["empty_change_set"]


@requires_ts
def test_every_factor_is_reported_with_its_contribution(db):
    _ingest(db, REPO)

    result = change_risk(db, files=["core.py"])
    hub = next(s for s in result["symbols"] if s["canonical"] == "core.py::hub")

    reported = {f["factor"] for f in hub["factors"]}
    assert reported == set(WEIGHTS)
    for factor in hub["factors"]:
        assert factor["weight"] == WEIGHTS[factor["factor"]]
        assert 0.0 <= factor["normalised"] <= 1.0
        assert factor["contribution"] == pytest.approx(
            factor["weight"] * factor["normalised"], abs=1e-4
        )


@requires_ts
def test_contributions_sum_exactly_to_the_reported_score(db):
    _ingest(db, REPO)

    result = change_risk(db, files=["core.py", "a.py", "b.py", "c.py"])

    assert result["symbols"], "the change set must match something"
    for symbol in result["symbols"]:
        total = sum(f["contribution"] for f in symbol["factors"])
        assert round(total, 4) == symbol["structural_score"], symbol["canonical"]


@requires_ts
def test_score_is_inside_the_unit_interval(db):
    _ingest(db, REPO)

    result = change_risk(db, files=["core.py", "a.py", "b.py", "c.py"])

    for symbol in result["symbols"]:
        assert 0.0 <= symbol["structural_score"] <= 1.0
    assert 0.0 <= result["change_score"] <= 1.0


@requires_ts
def test_a_called_symbol_scores_above_an_uncalled_one(db):
    """The score must move with the graph, not be a constant."""
    _ingest(db, REPO)

    result = change_risk(db, files=["core.py"])
    scores = {s["canonical"]: s["structural_score"] for s in result["symbols"]}

    assert scores["core.py::hub"] > scores["core.py::lonely"], scores
    hub = next(s for s in result["symbols"] if s["canonical"] == "core.py::hub")
    assert hub["raw"]["callers"] == 3


@requires_ts
def test_a_security_sensitive_name_raises_the_score_of_an_otherwise_identical_symbol(db):
    files = {
        "m.py": (
            "def verify_token():\n"
            "    return 1\n"
            "\n"
            "def compute_total():\n"
            "    return 1\n"
        )
    }
    _ingest(db, files, uri="test://risk-sec")

    result = change_risk(db, files=["m.py"])
    scores = {s["canonical"]: s["structural_score"] for s in result["symbols"]}

    assert scores["m.py::verify_token"] > scores["m.py::compute_total"]
    sensitive = next(s for s in result["symbols"] if s["canonical"] == "m.py::verify_token")
    assert sensitive["raw"]["security_terms_matched"]
    assert set(sensitive["raw"]["security_terms_matched"]) <= set(SECURITY_TERMS)


@requires_ts
def test_test_coverage_contributes_nothing_when_a_test_edge_exists(db):
    """The coverage factor is the ABSENCE of a test edge.

    Asserting the factor rather than the total, because a tested symbol also
    gains a caller and an entry-point reach from the test itself, so the totals
    are not a controlled comparison. The controlled comparison is the next test.
    """
    files = {
        "m.py": "def covered():\n    return 1\n\n\ndef bare():\n    return 1\n",
        "test_m.py": (
            "from m import covered\n\n\ndef test_covered():\n    return covered()\n"
        ),
    }
    _ingest(db, files, uri="test://risk-test")

    result = change_risk(db, files=["m.py"])
    by_name = {s["canonical"]: s for s in result["symbols"]}

    assert by_name["m.py::covered"]["raw"]["has_test_edge"] is True
    assert by_name["m.py::bare"]["raw"]["has_test_edge"] is False
    covered_factor = _factor(by_name["m.py::covered"], "test_coverage")
    bare_factor = _factor(by_name["m.py::bare"], "test_coverage")
    assert covered_factor["contribution"] == 0.0
    assert bare_factor["contribution"] == WEIGHTS["test_coverage"]


@requires_ts
def test_removing_only_the_test_edge_raises_that_symbols_score(db):
    """Everything else held fixed, losing test coverage must raise the score."""
    files = {
        "m.py": "def covered():\n    return 1\n",
        "test_m.py": (
            "from m import covered\n\n\ndef test_covered():\n    return covered()\n"
        ),
    }
    _ingest(db, files, uri="test://risk-edge")

    before = change_risk(db, symbols=["m.py::covered"])["symbols"][0]
    assert before["raw"]["has_test_edge"] is True

    db.execute("DELETE FROM relationships WHERE predicate='code:tested_by';")

    after = change_risk(db, symbols=["m.py::covered"])["symbols"][0]
    assert after["raw"]["has_test_edge"] is False
    assert after["raw"]["callers"] == before["raw"]["callers"], "the call edge is untouched"
    assert after["structural_score"] > before["structural_score"]


@requires_ts
def test_levels_are_named_and_their_cuts_are_published(db):
    _ingest(db, REPO)

    result = change_risk(db, files=["core.py"])

    assert result["levels"]["names"] == list(LEVEL_NAMES)
    cuts = result["levels"]["cuts"]
    assert set(cuts) == set(LEVEL_NAMES)
    # Monotonic: a higher level cannot open at a lower score.
    ordered = [cuts[name] for name in LEVEL_NAMES]
    assert ordered == sorted(ordered)
    assert "nearest-rank percentile" in result["levels"]["derivation"]
    for symbol in result["symbols"]:
        assert symbol["level"] in LEVEL_NAMES
        assert symbol["structural_score"] >= cuts[symbol["level"]]


@requires_ts
def test_every_published_cut_is_a_score_some_symbol_in_this_graph_actually_has(db):
    """The property that separates a derived cut from a tuned constant.

    Checking only that the cuts are ordered, or that the caveat string mentions
    a percentile, would pass for any hardcoded ladder. Nearest rank means each
    cut IS an observed value, so that is what is asserted.
    """
    _ingest(db, REPO)

    result = change_risk(db, files=["core.py", "a.py", "b.py", "c.py"], limit=1000)
    everything = change_risk(db, symbols=[s["canonical"] for s in result["symbols"]], limit=1000)
    observed = {s["structural_score"] for s in everything["symbols"]}
    cuts = result["levels"]["cuts"]

    # The lowest level opens at the lowest observed score, by construction.
    assert cuts["low"] == min(observed), (cuts, sorted(observed))
    for name in LEVEL_NAMES:
        assert cuts[name] in observed, (name, cuts[name], sorted(observed))


@requires_ts
def test_the_cuts_move_when_the_graph_moves(db):
    """A tuned constant would not. A distribution-derived cut has to."""
    _ingest(db, REPO)
    before = change_risk(db, files=["core.py"])["levels"]["cuts"]

    # Add a cluster of untested, security-named, heavily called symbols, which
    # shifts the score distribution the cuts are taken from.
    extra = {
        "auth.py": (
            "def verify_token():\n    return 1\n\n\ndef check_password():\n    return 2\n"
        ),
        "d.py": "from auth import verify_token\n\n\ndef d():\n    return verify_token()\n",
        "e.py": "from auth import verify_token\n\n\ndef e():\n    return verify_token()\n",
    }
    _ingest(db, extra, uri="test://risk")

    after = change_risk(db, files=["core.py"])["levels"]["cuts"]

    assert after != before, (before, after)


@requires_ts
def test_result_is_deterministic_across_runs(db):
    _ingest(db, REPO)

    first = change_risk(db, files=["core.py", "a.py"])
    second = change_risk(db, files=["core.py", "a.py"])

    assert first["symbols"] == second["symbols"]
    assert first["levels"] == second["levels"]


@requires_ts
def test_a_change_set_can_be_named_by_symbol_as_well_as_by_file(db):
    _ingest(db, REPO)

    by_file = change_risk(db, files=["core.py"])
    by_symbol = change_risk(db, symbols=["core.py::hub"])

    assert [s["canonical"] for s in by_symbol["symbols"]] == ["core.py::hub"]
    hub_from_file = next(s for s in by_file["symbols"] if s["canonical"] == "core.py::hub")
    assert by_symbol["symbols"][0]["structural_score"] == hub_from_file["structural_score"]

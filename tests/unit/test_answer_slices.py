"""Answer-shaped node-level slices: ranking, budgeting, and honest reporting.

The behaviours that matter, each of which would be easy to break silently:

* a slice is a SYMBOL, never a whole file, which is the whole point,
* the seed is never dropped to fit a budget,
* the budget is actually honoured, and what was dropped is reported,
* detail levels genuinely trade size against completeness,
* ranking is deterministic, so a result can be diffed between runs,
* an unknown seed is refused rather than guessed at.
"""

from __future__ import annotations

import pytest

from dkg.code.capability import grammar_available
from dkg.code.graph import write_code_graph
from dkg.code.parser import parse_source
from dkg.context.slices import DETAIL_LEVELS, answer_slices
from dkg.core.db import open_database
from dkg.core.errors import ValidationError

pytestmark = pytest.mark.skipif(
    not grammar_available("python"), reason="the python grammar is not installed"
)

CORE = '''\
"""Shared helpers."""


def helper(value):
    """Add one."""
    doubled = value * 2
    tripled = value * 3
    return doubled + tripled + 1


def unrelated(value):
    """Nothing to do with the helper."""
    a = 1
    b = 2
    return a + b + value
'''

CALLER = '''\
from core import helper


def first(value):
    """Call the helper once."""
    prepared = value + 1
    return helper(prepared)


def second(value):
    """Call the helper twice."""
    left = helper(value)
    right = helper(value + 1)
    return left + right


def third(value):
    """Does not touch the helper at all."""
    return value * 9


def fourth(value):
    """Mostly unrelated work, with one call to the helper buried in it."""
    a = value + 1
    b = a * 2
    c = b - 3
    d = c // 4
    e = d + 5
    result = helper(e)
    f = result * 6
    g = f - 7
    h = g // 8
    i = h + 9
    return i
'''


@pytest.fixture
def db(tmp_path):
    texts = {"core.py": CORE, "caller.py": CALLER}
    parsed = [parse_source(name, text) for name, text in texts.items()]
    with open_database(tmp_path / "g.db") as database:
        write_code_graph(
            database, parsed, texts, source_uri="code://slices-test", tenant_id="local"
        )
        yield database


def test_a_slice_is_a_symbol_not_a_file(db):
    result = answer_slices(db, "core.py::helper", relation="callers", depth=1)
    assert result["found"]
    canonicals = [s["canonical"] for s in result["slices"]]
    assert "caller.py::first" in canonicals
    assert "caller.py::second" in canonicals
    # The whole point: a symbol that does not call the helper is not returned
    # merely because it lives in a file that does.
    assert "caller.py::third" not in canonicals
    for s in result["slices"]:
        assert "::" in s["canonical"], "a slice must name a symbol, not a file"


def test_the_seed_survives_a_budget_too_small_for_anything(db):
    """Required units are never dropped to fit, and the overrun is reported."""
    result = answer_slices(db, "core.py::helper", relation="callers", depth=1, token_budget=1)
    canonicals = [s["canonical"] for s in result["slices"]]
    assert "core.py::helper" in canonicals
    assert result["totals"]["budget_exceeded"] is True


def test_the_budget_is_honoured_and_what_was_dropped_is_reported(db):
    generous = answer_slices(db, "core.py::helper", relation="callers", depth=2, token_budget=None)
    tight = answer_slices(db, "core.py::helper", relation="callers", depth=2, token_budget=60)
    # STRICTLY fewer, not "no more": `<=` would pass unchanged if the budget
    # were ignored entirely, which is the exact regression this guards against.
    assert tight["totals"]["returned"] < generous["totals"]["returned"], (
        "the tight budget returned as much as the unbounded one, so the budget "
        "had no effect"
    )
    assert tight["totals"]["tokens_used"] < generous["totals"]["tokens_used"]
    # Dropping is allowed; dropping silently is not. Unconditional, because the
    # assertion above guarantees something was dropped.
    assert tight["totals"]["omitted"] > 0
    assert tight["omitted"]
    assert all("canonical" in o for o in tight["omitted"])


def test_detail_levels_trade_size_for_completeness(db):
    sizes = {}
    for detail in DETAIL_LEVELS:
        result = answer_slices(
            db, "core.py::helper", relation="callers", depth=1, detail=detail, token_budget=None
        )
        sizes[detail] = result["totals"]["tokens_used"]
    assert sizes["signature"] <= sizes["focused"] <= sizes["full"]
    assert sizes["signature"] < sizes["full"], "the levels must actually differ"


def test_focused_mode_keeps_the_lines_that_mention_the_seed(db):
    result = answer_slices(
        db, "core.py::helper", relation="callers", depth=1, detail="focused", token_budget=None
    )
    second = next(s for s in result["slices"] if s["canonical"] == "caller.py::second")
    assert "helper(" in second["excerpt"]

    # And drops the ones that do not. `>= 0` was the earlier assertion and a
    # line count can never be negative, so it proved nothing. `fourth` is the
    # symbol that makes this checkable: its body is mostly arithmetic unrelated
    # to the seed, with one buried call.
    fourth = next(s for s in result["slices"] if s["canonical"] == "caller.py::fourth")
    assert "helper(" in fourth["excerpt"], "the line that matters was dropped"
    assert fourth["elided_lines"] > 0, "focused mode elided nothing at all"
    assert "elided" in fourth["excerpt"], "an elision must be marked, not silent"
    full = answer_slices(
        db, "core.py::helper", relation="callers", depth=1, detail="full", token_budget=None
    )
    fourth_full = next(s for s in full["slices"] if s["canonical"] == "caller.py::fourth")
    assert len(fourth["excerpt"]) < len(fourth_full["excerpt"])
    # The unrelated arithmetic really is gone.
    assert "g = f - 7" in fourth_full["excerpt"]
    assert "g = f - 7" not in fourth["excerpt"]


def test_signature_mode_returns_the_declaration_and_says_what_it_elided(db):
    result = answer_slices(
        db, "core.py::helper", relation="callers", depth=1, detail="signature", token_budget=None
    )
    first = next(s for s in result["slices"] if s["canonical"] == "caller.py::first")
    assert first["excerpt"].strip().startswith("def first")
    assert first["elided_lines"] > 0


def test_ranking_is_deterministic_so_a_result_can_be_diffed(db):
    runs = [
        answer_slices(db, "core.py::helper", relation="callers", depth=2, token_budget=500)
        for _ in range(3)
    ]
    keys = [[s["canonical"] for s in r["slices"]] for r in runs]
    assert keys[0] == keys[1] == keys[2]
    scores = [[s["score"] for s in r["slices"]] for r in runs]
    assert scores[0] == scores[1] == scores[2]


def test_a_nearer_symbol_outranks_a_further_one(db):
    """Distance must actually drive the ranking.

    An earlier version of this test looped over the results comparing anything
    further away than a distance-1 symbol. Nothing in this graph is further, so
    the loop body never executed and only `is not None` survived. The fixture
    below therefore asserts that a mixture of distances is PRESENT before
    comparing them, so the comparison cannot silently become a no-op again.
    """
    result = answer_slices(db, "core.py::helper", relation="callers", depth=3, token_budget=None)
    by_distance: dict[int, list[float]] = {}
    for s in result["slices"]:
        by_distance.setdefault(s["distance"], []).append(s["score"])
    assert len(by_distance) >= 2, (
        f"the fixture produced only distances {sorted(by_distance)}; this test "
        "cannot compare near against far and would pass vacuously"
    )
    ordered = sorted(by_distance)
    for nearer, further in zip(ordered, ordered[1:]):
        assert min(by_distance[nearer]) > max(by_distance[further]), (
            f"a symbol at distance {further} outranked one at {nearer}"
        )


def test_an_unknown_seed_is_refused_rather_than_guessed(db):
    result = answer_slices(db, "core.py::no_such_symbol", relation="callers")
    assert result["found"] is False
    assert result["slices"] == []
    assert "nothing was guessed" in result["why"]


def test_an_unknown_relation_or_detail_is_rejected(db):
    with pytest.raises(ValidationError, match="unknown relation"):
        answer_slices(db, "core.py::helper", relation="sideways")
    with pytest.raises(ValidationError, match="unknown detail"):
        answer_slices(db, "core.py::helper", detail="verbose")


def test_the_over_approximation_caveat_travels_with_the_answer(db):
    result = answer_slices(db, "core.py::helper", relation="callers", depth=1)
    assert "over-approximate" in result["why"]


def test_callees_and_callers_are_different_directions(db):
    callers = answer_slices(db, "core.py::helper", relation="callers", depth=1, token_budget=None)
    callees = answer_slices(db, "caller.py::first", relation="callees", depth=1, token_budget=None)
    assert "caller.py::first" in {s["canonical"] for s in callers["slices"]}
    assert "core.py::helper" in {s["canonical"] for s in callees["slices"]}

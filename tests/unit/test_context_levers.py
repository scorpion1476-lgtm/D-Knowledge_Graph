"""The four context levers: packing, delta sessions, exact answers, provenance bounds.

These pin the properties that make the levers safe to turn on, not just cheap:
a structurally required unit is never dropped, a changed symbol is re-sent, an
unrecognised question falls through instead of being answered wrongly, and both
sides of any comparison are counted by the same tokenizer.
"""

from __future__ import annotations

import json

import pytest

from dkg.context import (
    PRICE_TABLE,
    ContextFlags,
    SessionContext,
    Unit,
    answer_exact,
    classify,
    cost_usd,
    count_tokens,
    fixed_neighbourhood,
    measure,
    pack_units,
    pricing_note,
    provenance_bounded,
    tokenizer_name,
    tokenizer_note,
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

CORE = (
    "core.py",
    "def util():\n    return 1\n"
    "def hub():\n    return util()\n"
    "def a():\n    return hub()\n"
    "def b():\n    return hub()\n"
    "def lonely():\n    return 0\n",
    "python",
)


def _ingest(db, files):
    parsed = [parse_source(p, t, language=lang) for p, t, lang in files]
    write_code_graph(db, parsed, {p: t for p, t, _ in files}, source_uri="test://ctx")


# -- tokens and cost --------------------------------------------------------


def test_token_counting_is_consistent_and_nonzero():
    assert count_tokens("") == 0
    a = count_tokens("def util():\n    return 1\n")
    assert a > 0
    assert count_tokens("x" * 400) > a
    # Same function both sides, always.
    assert count_tokens("abc") == count_tokens("abc")


def test_tokenizer_note_states_which_tokenizer_was_used():
    note = tokenizer_note()
    assert note["tokenizer"] == tokenizer_name()
    assert isinstance(note["real_tokenizer"], bool)
    # A count must never be presented as tokenizer-measured when it was not.
    if not note["real_tokenizer"]:
        assert note["tokenizer"] == "in-repo-estimator"


def test_cost_is_a_transparent_multiplication():
    rate = PRICE_TABLE["mid"].input_per_mtok
    assert cost_usd(1_000_000, tier="mid") == pytest.approx(rate)
    assert cost_usd(0) == 0.0
    assert cost_usd(500_000, tier="mid") == pytest.approx(rate / 2)
    with pytest.raises(KeyError):
        cost_usd(10, tier="no-such-tier")


def test_pricing_note_is_dated_and_labels_itself_configuration():
    note = pricing_note()
    assert note["rates_recorded_on"]
    assert "configuration, not a measurement" in note["note"]
    assert note["input_usd_per_mtok"] > 0


def test_measure_reports_tokens_characters_and_cost():
    m = measure("def util():\n    return 1\n")
    assert m["tokens"] > 0
    assert m["characters"] > 0
    assert m["cost_usd"] >= 0


# -- budgeted packing -------------------------------------------------------


def _units():
    return [
        Unit("req", "code:function", "x" * 400, score=0.1, required=True),
        Unit("opt_high", "code:function", "y" * 40, score=0.9),
        Unit("opt_low", "code:function", "z" * 40, score=0.5),
    ]


def test_packing_orders_required_first_then_score_then_key():
    packed = pack_units(_units(), budget=None)
    assert [u.key for u in packed.units] == ["req", "opt_high", "opt_low"]


def test_a_required_unit_is_never_dropped_to_fit_the_budget():
    # Budget far below the required unit's own cost.
    packed = pack_units(_units(), budget=1)
    assert [u.key for u in packed.units] == ["req"]
    assert packed.budget_exceeded is True, "overspend must be reported, not hidden"
    assert packed.tokens_used > packed.budget


def test_optional_units_fill_the_remaining_budget_by_rank():
    units = _units()
    required_cost = units[0].tokens()
    room = required_cost + units[1].tokens()
    packed = pack_units(units, budget=room)
    keys = [u.key for u in packed.units]
    assert "req" in keys and "opt_high" in keys
    assert "opt_low" not in keys
    assert [u.key for u in packed.omitted] == ["opt_low"]


def test_unbounded_budget_returns_everything():
    packed = pack_units(_units(), budget=None)
    assert len(packed.units) == 3
    assert packed.omitted == []
    assert packed.budget is None
    assert packed.budget_exceeded is False


def test_packing_is_deterministic_and_reports_its_rule():
    first = pack_units(_units(), budget=200).to_dict()
    second = pack_units(_units(), budget=200).to_dict()
    assert first == second
    assert "never dropped" in first["why"]["rule"]


def test_empty_pack_is_safe():
    packed = pack_units([], budget=100)
    assert packed.units == [] and packed.tokens_used == 0
    assert packed.text == ""


# -- delta session ----------------------------------------------------------


def test_second_turn_sends_nothing_already_seen():
    session = SessionContext()
    first = session.turn(_units())
    second = session.turn(_units())
    assert len(first.sent) == 3 and first.packed.tokens_used > 0
    assert second.sent == [] and second.packed.tokens_used == 0
    assert len(second.suppressed) == 3
    assert session.turns == 2


def test_a_changed_symbol_is_resent_not_suppressed():
    session = SessionContext()
    session.turn([Unit("k", "code:function", "original", score=1.0)])
    result = session.turn([Unit("k", "code:function", "CHANGED BODY", score=1.0)])
    assert [u.key for u in result.resent_changed] == ["k"]
    assert [u.key for u in result.sent] == ["k"]
    assert result.suppressed == []


def test_full_resend_recovers_a_client_that_lost_its_history():
    session = SessionContext()
    session.turn(_units())
    result = session.turn(_units(), full_resend=True)
    assert len(result.sent) == 3
    assert result.suppressed == []


def test_reset_clears_what_the_session_has_seen():
    session = SessionContext()
    session.turn(_units())
    assert session.seen_count == 3
    session.reset()
    assert session.seen_count == 0
    assert len(session.turn(_units()).sent) == 3


# -- exact answers ----------------------------------------------------------


@pytest.mark.parametrize(
    "question,kind",
    [
        ("who calls core.py::hub", "callers"),
        ("callers of core.py::hub", "callers"),
        ("what is the blast radius of core.py::util", "impact"),
        ("what breaks if I change core.py::util", "impact"),
        ("what does core.py::hub call", "callees"),
        ("tests for core.py::hub", "tests_for"),
        ("is core.py::hub tested", "is_tested"),
    ],
)
def test_structural_questions_are_recognised(question, kind):
    got = classify(question)
    assert got is not None, question
    assert got[0] == kind


@pytest.mark.parametrize(
    "question",
    [
        # A structural fragment inside a question that also needs judgement must
        # not be answered: doing so drops half the question and presents a
        # partial answer as the whole one.
        "who calls core.py::hub and why was it designed that way",
        "should I refactor who calls core.py::hub",
        "is core.py::hub tested well enough for production",
        "callers of core.py::hub, and is that too many",
        "what is the blast radius of core.py::util and should I be worried",
        "explain the architecture in prose",
        "why was this designed this way",
        "summarise the release notes",
        "",
        "   ",
    ],
)
def test_a_question_needing_judgement_falls_through(question):
    # Answering these "exactly" would be confidently wrong.
    assert classify(question) is None


@requires_ts
def test_exact_answers_are_correct_and_cost_no_model_tokens(db):
    _ingest(db, [CORE])
    callers = answer_exact(db, "who calls core.py::hub")
    assert callers["answer"] == ["core.py::a", "core.py::b"]
    assert callers["model_tokens"] == 0
    assert callers["resolved"] is True

    impact = answer_exact(db, "what is the blast radius of core.py::util")
    assert set(impact["answer"]) == {"core.py::hub", "core.py::a", "core.py::b"}
    assert impact["model_tokens"] == 0

    callees = answer_exact(db, "what does core.py::hub call")
    assert callees["answer"] == ["core.py::util"]


@requires_ts
def test_an_unknown_symbol_refuses_rather_than_guesses(db):
    _ingest(db, [CORE])
    result = answer_exact(db, "who calls core.py::does_not_exist")
    assert result["resolved"] is False
    assert result["answer"] == []
    assert "not in the code graph" in result["why"]


@requires_ts
def test_unmatched_question_returns_none_so_the_normal_path_runs(db):
    _ingest(db, [CORE])
    assert answer_exact(db, "explain why this module exists") is None


@requires_ts
def test_exact_answers_carry_the_over_approximation_caveat(db):
    _ingest(db, [CORE])
    assert "over-approximate" in answer_exact(db, "who calls core.py::hub")["why"]


# -- provenance bounds ------------------------------------------------------


@requires_ts
def test_provenance_bounded_returns_only_nodes_with_a_path_to_the_seed(db):
    _ingest(db, [CORE])
    bounded = provenance_bounded(db, ["core.py::util"])
    keys = {u.key for u in bounded.units}
    # a and b reach util through hub; lonely does not reach it at all.
    assert {"core.py::util", "core.py::hub", "core.py::a", "core.py::b"} <= keys
    assert "core.py::lonely" not in keys
    assert bounded.units[0].required or any(u.required for u in bounded.units)


@requires_ts
def test_fixed_neighbourhood_is_available_as_the_comparison_baseline(db):
    _ingest(db, [CORE])
    baseline = fixed_neighbourhood(db, ["core.py::util"], depth=2)
    assert baseline.strategy == "fixed_neighbourhood"
    assert baseline.reached >= 1
    # The two strategies must be comparable, which means both report a count.
    assert provenance_bounded(db, ["core.py::util"]).reached >= 1


@requires_ts
def test_both_strategies_are_deterministic(db):
    _ingest(db, [CORE])
    assert provenance_bounded(db, ["core.py::util"]).to_dict() == provenance_bounded(db, ["core.py::util"]).to_dict()
    assert fixed_neighbourhood(db, ["core.py::util"]).to_dict() == fixed_neighbourhood(db, ["core.py::util"]).to_dict()


# -- flags ------------------------------------------------------------------


def test_flags_default_on_and_are_overridable_from_the_environment(monkeypatch):
    monkeypatch.delenv("DKG_CONTEXT_EXACT_ANSWERS", raising=False)
    monkeypatch.delenv("DKG_TOKEN_BUDGET", raising=False)
    assert ContextFlags.from_env().exact_answers is True
    assert ContextFlags.from_env().default_token_budget is None

    monkeypatch.setenv("DKG_CONTEXT_EXACT_ANSWERS", "0")
    assert ContextFlags.from_env().exact_answers is False
    monkeypatch.setenv("DKG_TOKEN_BUDGET", "4000")
    assert ContextFlags.from_env().default_token_budget == 4000
    # A nonsense budget must not silently become a tiny one.
    monkeypatch.setenv("DKG_TOKEN_BUDGET", "not-a-number")
    assert ContextFlags.from_env().default_token_budget is None
    monkeypatch.setenv("DKG_TOKEN_BUDGET", "-5")
    assert ContextFlags.from_env().default_token_budget is None


def test_flags_round_trip_to_a_dict_for_the_benchmark_record():
    d = ContextFlags().to_dict()
    assert set(d) == {
        "delta_session",
        "exact_answers",
        "provenance_bounded",
        "budgeted_slices",
        "default_token_budget",
    }


# -- payload budget trimming ------------------------------------------------


def _payload(n_hubs=40, n_bridges=30):
    return {
        "hubs": [{"canonical": f"mod_{i:03d}.py::fn_{i}", "degree": 40 - i} for i in range(n_hubs)],
        "chokepoints": [{"canonical": f"c{i}.py::x", "betweenness": 0.1} for i in range(5)],
        "bridges": {"bridge_edges": [{"from": f"a{i}", "to": f"b{i}"} for i in range(n_bridges)]},
        "totals": {"nodes": 2927, "edge_pairs": 4913},
        "why": {"note": "structural and advisory"},
    }


def test_no_budget_returns_the_payload_untouched():
    from dkg.context.pack import apply_budget

    p = _payload()
    assert apply_budget(p, budget=None) is p
    assert apply_budget(p, budget=0) is p


def test_a_payload_already_within_budget_is_not_trimmed():
    from dkg.context.pack import apply_budget

    p = _payload(n_hubs=1, n_bridges=1)
    out = apply_budget(p, budget=100_000)
    assert out is p
    assert "token_budget" not in out


def test_trimming_reaches_the_budget_when_it_can():
    from dkg.context.pack import apply_budget
    from dkg.context.tokens import count_tokens

    out = apply_budget(_payload(), budget=1500)
    rendered = json.dumps(out, indent=2, sort_keys=True)
    assert count_tokens(rendered) <= 1500
    assert out["token_budget"]["budget_exceeded"] is False


def test_reported_tokens_match_the_payload_actually_produced():
    """The report block is part of what the caller pays for."""
    from dkg.context.pack import apply_budget
    from dkg.context.tokens import count_tokens

    payload = _payload()
    untrimmed = count_tokens(json.dumps(payload, indent=2, sort_keys=True))
    # Budgets are fractions of the measured size, so this binds under the real
    # tokenizer and under the fallback estimator alike.
    for fraction in (0.25, 0.5, 0.8):
        budget = int(untrimmed * fraction)
        assert budget < untrimmed, "the budget must actually bind for this to test anything"
        out = apply_budget(payload, budget=budget)
        actual = count_tokens(json.dumps(out, indent=2, sort_keys=True))
        reported = out["token_budget"]["tokens"]
        # Never understate what the caller pays. At a digit boundary the count
        # can only settle one token high, never low.
        assert reported >= actual, budget
        assert reported - actual <= 1, budget
        # The claim that matters is one-directional: a payload reported as
        # fitting must really fit. The reverse (reported over by one token at a
        # digit boundary while it actually fits) is safe conservatism.
        if not out["token_budget"]["budget_exceeded"]:
            assert actual <= budget, budget


def test_nested_ranked_lists_are_trimmed_too():
    """A list nested one level down is often the biggest one in the payload."""
    from dkg.context.pack import apply_budget

    out = apply_budget(_payload(n_hubs=5, n_bridges=200), budget=1200)
    assert len(out["bridges"]["bridge_edges"]) < 200
    assert "bridges.bridge_edges" in out["token_budget"]["entries_dropped"]


def test_no_ranked_list_is_ever_emptied():
    """A result listing nothing is not a cheaper answer, it is no answer."""
    from dkg.context.pack import apply_budget

    out = apply_budget(_payload(), budget=50)
    assert len(out["hubs"]) >= 1
    assert len(out["chokepoints"]) >= 1
    assert len(out["bridges"]["bridge_edges"]) >= 1
    # It could not reach the budget, and says so rather than pretending.
    assert out["token_budget"]["budget_exceeded"] is True


def test_totals_are_never_rewritten_by_trimming():
    from dkg.context.pack import apply_budget

    out = apply_budget(_payload(), budget=900)
    assert out["totals"] == {"nodes": 2927, "edge_pairs": 4913}
    assert len(out["hubs"]) < 40
    assert "not a complete listing" in out["token_budget"]["note"]


def test_trimming_does_not_mutate_the_caller_s_payload():
    from dkg.context.pack import apply_budget

    p = _payload()
    apply_budget(p, budget=900)
    assert len(p["hubs"]) == 40
    assert len(p["bridges"]["bridge_edges"]) == 30
    assert "token_budget" not in p


def test_trimming_takes_from_the_longest_list_first():
    from dkg.context.pack import apply_budget

    out = apply_budget(_payload(n_hubs=60, n_bridges=5), budget=1200)
    dropped = out["token_budget"]["entries_dropped"]
    assert dropped.get("hubs", 0) > dropped.get("bridges.bridge_edges", 0)


# -- ambiguity and session fixes from the adversarial audit ------------------


@requires_ts
def test_an_ambiguous_symbol_name_is_refused_not_guessed(db):
    """Picking one of several same-named symbols is the worst failure here.

    It reports one symbol's coverage under another symbol's name, and nothing in
    the output says a choice was made.
    """
    _ingest(
        db,
        [
            ("alpha.py", "def handle():\n    return 1\n", "python"),
            ("zeta.py", "def handle():\n    return 2\n", "python"),
            ("test_zeta.py", "def test_handle():\n    return handle()\n", "python"),
        ],
    )
    result = answer_exact(db, "is handle tested")
    assert result["resolved"] is False
    assert result["ambiguous"] is True
    assert set(result["candidates"]) == {"alpha.py::handle", "zeta.py::handle"}
    assert "disambiguate" in result["why"]
    # Naming the symbol exactly still resolves.
    exact = answer_exact(db, "is alpha.py::handle tested")
    assert exact["resolved"] is True
    assert exact["ambiguous"] is False


@requires_ts
def test_an_impact_answer_discloses_that_it_is_depth_bounded(db):
    _ingest(db, [CORE])
    result = answer_exact(db, "what is the blast radius of core.py::util")
    assert result["traversal"]["depth"] >= 1
    assert "truncated" in result["traversal"]


def test_a_unit_the_budget_dropped_is_offered_again_next_turn():
    """Marking a unit seen before the budget decided its fate meant a dropped
    unit was suppressed on every later turn and never sent at all."""
    small = Unit("B", "code:function", "x" * 20, score=0.9)
    big = Unit("A", "code:function", "y" * 400, score=0.5)
    session = SessionContext(budget=30)
    first = session.turn([small, big])
    assert [u.key for u in first.sent] == ["B"]
    assert [u.key for u in first.packed.omitted] == ["A"]
    second = session.turn([small, big])
    # B was sent, so it is suppressed. A never was, so it must still be offered.
    assert [u.key for u in second.suppressed] == ["B"]
    assert "A" in [u.key for u in second.packed.omitted]
    # Given room, A finally goes out rather than being lost forever.
    third = session.turn([small, big], budget=10_000)
    assert "A" in [u.key for u in third.sent]

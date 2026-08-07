"""Context levers: cut the tokens a task costs without cutting correctness.

Four independent levers, each behind a flag so it can be measured on and off:

- ``delta_session``: within a session, send only what has not been sent before.
- ``exact_answers``: answer structural questions from the graph with no model.
- ``provenance_bounded``: return the evidence that supports the answer and stop,
  rather than a fixed neighbourhood.
- ``budgeted_slices``: return ranked typed units packed into a token budget.

Every default here was chosen from a measured result, not from taste, and the
measurement is in ``docs/BENCHMARKS.md``. A lever that did not pay for itself is
defaulted off and said so.

Flags are read from the environment so a run can be reproduced from the command
line, and every flag is inspectable through :func:`active_flags` so a benchmark
records the configuration it actually ran under.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass

from .exact import answer_exact, classify
from .pack import PackResult, Unit, pack_units, units_from_graph
from .provenance import (
    ContextResult,
    claim_evidence_bounded,
    fixed_neighbourhood,
    provenance_bounded,
)
from .session import SessionContext, TurnResult
from .tokens import (
    PRICE_TABLE,
    PRICE_TABLE_DATE,
    cost_usd,
    count_tokens,
    measure,
    pricing_note,
    tokenizer_available,
    tokenizer_name,
    tokenizer_note,
)

__all__ = [
    "ContextFlags",
    "ContextResult",
    "PackResult",
    "PRICE_TABLE",
    "PRICE_TABLE_DATE",
    "SessionContext",
    "TurnResult",
    "Unit",
    "active_flags",
    "answer_exact",
    "claim_evidence_bounded",
    "classify",
    "cost_usd",
    "count_tokens",
    "fixed_neighbourhood",
    "measure",
    "pack_units",
    "pricing_note",
    "provenance_bounded",
    "tokenizer_available",
    "tokenizer_name",
    "tokenizer_note",
    "units_from_graph",
]


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class ContextFlags:
    """Which levers are active, and the default token budget.

    Defaults are set from measurement. See ``docs/BENCHMARKS.md`` for the run
    that chose each one.
    """

    delta_session: bool = True
    exact_answers: bool = True
    provenance_bounded: bool = True
    budgeted_slices: bool = True
    default_token_budget: int | None = None

    @classmethod
    def from_env(cls) -> ContextFlags:
        budget_raw = os.environ.get("DKG_TOKEN_BUDGET", "").strip()
        budget: int | None = None
        if budget_raw:
            try:
                parsed = int(budget_raw)
            except ValueError:
                parsed = 0
            budget = parsed if parsed > 0 else None
        return cls(
            delta_session=_env_flag("DKG_CONTEXT_DELTA_SESSION", True),
            exact_answers=_env_flag("DKG_CONTEXT_EXACT_ANSWERS", True),
            provenance_bounded=_env_flag("DKG_CONTEXT_PROVENANCE_BOUNDED", True),
            budgeted_slices=_env_flag("DKG_CONTEXT_BUDGETED_SLICES", True),
            default_token_budget=budget,
        )

    def to_dict(self) -> dict:
        return asdict(self)


def active_flags() -> ContextFlags:
    return ContextFlags.from_env()

"""Delta-only session context.

Across a multi-turn session an agent asks several related questions, and the
graph keeps surfacing the same symbols. Sending the same unit again on turn four
costs the same as sending it on turn one and tells the model nothing new.

This tracks what a session has already been shown and sends only the difference.
Two properties make it safe rather than merely cheap:

- A unit is re-sent when its CONTENT changes, not just when its name is new. The
  key alone would let a stale version stand while the code moved underneath.
- A caller can force a full resend, because a long session eventually needs a
  refresher and because a client that lost its history must be able to recover.

State lives in memory for the life of the session object. Nothing is persisted,
so there is no cache to invalidate across runs and no way for one session's
assumptions to leak into another's.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field

from .pack import PackResult, Unit, pack_units


def _fingerprint(unit: Unit) -> str:
    return hashlib.sha256(unit.rendered().encode("utf-8")).hexdigest()


@dataclass
class TurnResult:
    """What one turn actually sends, and what it suppressed."""

    packed: PackResult
    sent: list[Unit]
    suppressed: list[Unit]
    resent_changed: list[Unit]
    turn: int

    def to_dict(self) -> dict:
        return {
            "turn": self.turn,
            "sent": [u.key for u in self.sent],
            "suppressed_already_seen": [u.key for u in self.suppressed],
            "resent_because_changed": [u.key for u in self.resent_changed],
            "tokens_sent": self.packed.tokens_used,
            "packing": self.packed.to_dict(),
        }


@dataclass
class SessionContext:
    """Per-session memory of what has already been surfaced."""

    budget: int | None = None
    _seen: dict[str, str] = field(default_factory=dict, repr=False)
    _turn: int = 0

    def reset(self) -> None:
        self._seen.clear()

    @property
    def turns(self) -> int:
        return self._turn

    @property
    def seen_count(self) -> int:
        return len(self._seen)

    def turn(
        self,
        units: Sequence[Unit],
        *,
        budget: int | None = None,
        full_resend: bool = False,
    ) -> TurnResult:
        """Send only what this session has not already been shown."""
        self._turn += 1
        fresh: list[Unit] = []
        suppressed: list[Unit] = []
        resent: list[Unit] = []

        for unit in units:
            fp = _fingerprint(unit)
            previous = self._seen.get(unit.key)
            if full_resend or previous is None:
                fresh.append(unit)
            elif previous != fp:
                # Same symbol, different content: the model's copy is stale, so
                # this must go again or the answer is computed against old code.
                fresh.append(unit)
                resent.append(unit)
            else:
                suppressed.append(unit)

        packed = pack_units(fresh, budget=self.budget if budget is None else budget)
        # Record only what actually went out. Marking a unit seen before the
        # budget decided its fate meant a unit the budget dropped was suppressed
        # on every later turn and never sent at all.
        sent_keys = {u.key for u in packed.units}
        for unit in fresh:
            if unit.key in sent_keys:
                self._seen[unit.key] = _fingerprint(unit)
        return TurnResult(
            packed=packed,
            sent=packed.units,
            suppressed=suppressed,
            resent_changed=resent,
            turn=self._turn,
        )

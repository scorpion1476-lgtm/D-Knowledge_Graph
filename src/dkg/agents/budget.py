"""Simple budget accounting for agent tasks."""

from __future__ import annotations

from dataclasses import dataclass

from ..core.errors import BudgetExceededError


@dataclass
class BudgetAccount:
    total: int
    used: int = 0

    def charge(self, units: int) -> None:
        if units < 0:
            raise ValueError("units must be non-negative")
        if self.used + units > self.total:
            raise BudgetExceededError(
                f"budget exceeded: used={self.used} + charge={units} > total={self.total}"
            )
        self.used += units

    def remaining(self) -> int:
        return max(0, self.total - self.used)

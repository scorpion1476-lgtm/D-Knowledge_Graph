"""Agent base and task types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Task:
    kind: str
    input: dict[str, Any] = field(default_factory=dict)
    parent_id: str | None = None
    budget_units: int = 100
    timeout_seconds: float = 30.0

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "input": self.input,
            "parent_id": self.parent_id,
            "budget_units": self.budget_units,
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass
class TaskResult:
    ok: bool
    output: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    used_units: int = 0


class Agent(ABC):
    name: str

    @abstractmethod
    def handles(self, kind: str) -> bool: ...

    @abstractmethod
    def run(self, task: Task) -> TaskResult: ...

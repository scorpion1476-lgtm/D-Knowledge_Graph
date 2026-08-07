"""Connector contract: health, timeout, retry, circuit breaker, rate limit."""

from __future__ import annotations

import random
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class ConnectorHealth:
    name: str
    ok: bool
    latency_ms: float
    reason: str
    metadata: dict = field(default_factory=dict)


class RateLimiter:
    """Simple token bucket."""

    def __init__(self, rate_per_sec: float, burst: int) -> None:
        if rate_per_sec <= 0 or burst <= 0:
            raise ValueError("rate and burst must be positive")
        self.rate = float(rate_per_sec)
        self.burst = int(burst)
        self._tokens = float(burst)
        self._last = time.monotonic()

    def take(self, tokens: int = 1) -> bool:
        now = time.monotonic()
        elapsed = now - self._last
        self._last = now
        self._tokens = min(self.burst, self._tokens + elapsed * self.rate)
        if self._tokens >= tokens:
            self._tokens -= tokens
            return True
        return False


class CircuitBreaker:
    def __init__(self, threshold: int = 5, cooldown_sec: float = 30.0) -> None:
        self.threshold = int(threshold)
        self.cooldown_sec = float(cooldown_sec)
        self._failures = 0
        self._open_until = 0.0

    def allow(self) -> bool:
        if time.monotonic() < self._open_until:
            return False
        return True

    def record_success(self) -> None:
        self._failures = 0

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.threshold:
            self._open_until = time.monotonic() + self.cooldown_sec
            self._failures = 0


class Connector(ABC):
    name: str

    @abstractmethod
    def health(self) -> ConnectorHealth: ...


def with_retry(
    func: Callable[[], object],
    *,
    attempts: int = 3,
    base_delay: float = 0.1,
    jitter: float = 0.05,
) -> object:
    last_exc: Exception | None = None
    for i in range(max(1, attempts)):
        try:
            return func()
        except Exception as e:
            last_exc = e
            if i == attempts - 1:
                break
            time.sleep(base_delay * (2**i) + random.random() * jitter)
    assert last_exc is not None
    raise last_exc

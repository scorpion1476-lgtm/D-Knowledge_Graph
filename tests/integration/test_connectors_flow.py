"""End-to-end tests for connector primitives: rate limiter, breaker, retry.

Covers B-10. The connector primitives underpin every outbound adapter,
so these tests exercise their contract end to end including at least one
failure path per primitive.
"""

from __future__ import annotations

import pytest

from dkg.adapters.connectors import CircuitBreaker, RateLimiter, with_retry


def test_rate_limiter_grants_up_to_burst_and_denies_over():
    rl = RateLimiter(rate_per_sec=1.0, burst=3)
    assert rl.take() is True
    assert rl.take() is True
    assert rl.take() is True
    # Bucket empty and no time has elapsed; next take must be denied.
    assert rl.take() is False


def test_rate_limiter_rejects_invalid_construction():
    with pytest.raises(ValueError):
        RateLimiter(rate_per_sec=0, burst=1)
    with pytest.raises(ValueError):
        RateLimiter(rate_per_sec=1.0, burst=0)


def test_circuit_breaker_opens_after_threshold_failures_and_denies():
    cb = CircuitBreaker(threshold=3, cooldown_sec=10.0)
    assert cb.allow() is True
    cb.record_failure()
    cb.record_failure()
    assert cb.allow() is True  # not yet at threshold
    cb.record_failure()
    # Third failure crosses the threshold; breaker opens and denies.
    assert cb.allow() is False


def test_circuit_breaker_recovers_on_success_reset():
    cb = CircuitBreaker(threshold=3, cooldown_sec=10.0)
    cb.record_failure()
    cb.record_failure()
    cb.record_success()  # resets the failure counter
    cb.record_failure()
    cb.record_failure()
    # Only 2 failures since reset; must still allow.
    assert cb.allow() is True


def test_with_retry_returns_value_on_first_success():
    calls = {"n": 0}

    def fn() -> str:
        calls["n"] += 1
        return "ok"

    assert with_retry(fn, attempts=3) == "ok"
    assert calls["n"] == 1


def test_with_retry_gives_up_and_raises_last_error():
    calls = {"n": 0}

    def fn() -> str:
        calls["n"] += 1
        raise RuntimeError(f"attempt {calls['n']}")

    with pytest.raises(RuntimeError, match="attempt 3"):
        with_retry(fn, attempts=3, base_delay=0.0, jitter=0.0)
    assert calls["n"] == 3


def test_with_retry_recovers_on_late_success():
    calls = {"n": 0}

    def fn() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient")
        return "ok"

    assert with_retry(fn, attempts=5, base_delay=0.0, jitter=0.0) == "ok"
    assert calls["n"] == 3

import time

import pytest

from dkg.adapters.connectors import CircuitBreaker, RateLimiter, with_retry


def test_rate_limiter_blocks_over_budget():
    rl = RateLimiter(rate_per_sec=2, burst=2)
    assert rl.take()
    assert rl.take()
    assert not rl.take()


def test_circuit_breaker_opens_after_failures():
    cb = CircuitBreaker(threshold=2, cooldown_sec=1.0)
    cb.record_failure()
    cb.record_failure()
    assert not cb.allow()
    time.sleep(1.1)
    assert cb.allow()


def test_with_retry_eventually_succeeds():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("fail")
        return "ok"

    assert with_retry(flaky, attempts=3, base_delay=0.0, jitter=0.0) == "ok"


def test_with_retry_reraises_last():
    def always_fail():
        raise ValueError("nope")

    with pytest.raises(ValueError):
        with_retry(always_fail, attempts=2, base_delay=0.0, jitter=0.0)

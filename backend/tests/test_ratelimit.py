"""Tests for the science service's abuse control.

The limiter is driven with an injected clock throughout. A rate limiter tested against real time
either sleeps -- slow, and flaky on a loaded CI runner -- or only ever exercises the first
window, which is the half that already works.

The middleware is exercised separately against the real app, because the part most likely to be
wrong is not the arithmetic but the wiring: which paths are exempt, whether the limiter is
enforced at all in this environment, and whether a refusal carries the typed error body the rest
of the API contract promises.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.ratelimit import (
    DEFAULT_LIMITS,
    Limit,
    SlidingWindowLimiter,
    rate_limiting_enabled,
)

T0 = 1_000_000_000.0

# Small enough to reach in a few calls, so the tests read as policy rather than as loops.
TEST_LIMITS = (Limit(max_requests=3, window_s=60), Limit(max_requests=5, window_s=3600))


def test_admits_requests_up_to_the_burst_limit() -> None:
    """A caller inside its allowance is never refused."""
    limiter = SlidingWindowLimiter(TEST_LIMITS)

    for index in range(3):
        assert limiter.check("a", T0 + index).allowed, f"request {index} should pass"


def test_refuses_past_the_burst_limit_with_a_usable_wait() -> None:
    """The refusal states a wait that actually clears the window."""
    limiter = SlidingWindowLimiter(TEST_LIMITS)
    for _ in range(3):
        limiter.check("a", T0)

    decision = limiter.check("a", T0)

    assert not decision.allowed
    assert "per minute" in decision.message
    assert limiter.check("a", T0 + decision.retry_after_s).allowed


def test_window_slides() -> None:
    """Old hits stop counting once the window has passed."""
    limiter = SlidingWindowLimiter(TEST_LIMITS)
    for _ in range(3):
        limiter.check("a", T0)

    assert not limiter.check("a", T0).allowed
    assert limiter.check("a", T0 + 61).allowed


def test_clients_are_counted_separately() -> None:
    """One caller's exhaustion does not refuse another."""
    limiter = SlidingWindowLimiter(TEST_LIMITS)
    for _ in range(3):
        limiter.check("a", T0)

    assert not limiter.check("a", T0).allowed
    assert limiter.check("b", T0).allowed


def test_retrying_through_a_refusal_does_not_extend_the_window() -> None:
    """A caller that hammers the endpoint still recovers on schedule.

    If refused requests were recorded, a client retrying in a loop would hold its own window open
    forever -- a limiter that permanently bans whoever retries hardest, which is not the intent.
    """
    limiter = SlidingWindowLimiter(TEST_LIMITS)
    for _ in range(3):
        limiter.check("a", T0)

    for index in range(50):
        limiter.check("a", T0 + 1 + index * 0.1)

    assert limiter.check("a", T0 + 61).allowed


def test_sustained_window_catches_a_caller_pacing_under_the_burst_limit() -> None:
    """Spacing requests out defeats the burst window but not the hourly one."""
    limiter = SlidingWindowLimiter(TEST_LIMITS)

    for index in range(5):
        assert limiter.check("a", T0 + index * 61).allowed

    decision = limiter.check("a", T0 + 5 * 61)

    assert not decision.allowed
    assert "per hour" in decision.message


def test_tracked_clients_stay_under_the_ceiling() -> None:
    """Memory is bounded even when every request invents a new identity."""
    limiter = SlidingWindowLimiter(TEST_LIMITS, max_tracked_clients=4)

    for index in range(500):
        limiter.check(f"client-{index}", T0 + index)

    assert limiter.tracked_clients() == 4


def test_eviction_drops_the_least_recently_seen_client() -> None:
    """Recency, not arrival order, decides who is forgotten."""
    limiter = SlidingWindowLimiter(TEST_LIMITS, max_tracked_clients=4)
    for index in range(4):
        limiter.check(f"client-{index}", T0 + index)

    limiter.check("client-0", T0 + 1000)  # refresh the oldest arrival
    limiter.check("newcomer", T0 + 1001)

    assert limiter.tracked_clients() == 4
    # client-1 was the stalest, so it was dropped and starts with a full allowance again.
    for index in range(3):
        assert limiter.check("client-1", T0 + 2000 + index).allowed


def test_default_limits_leave_room_for_interactive_use() -> None:
    """The per-minute allowance must exceed what a person scrubbing the time slider produces.

    The solar-system view debounces at 120 ms, so sustained dragging tops out near 8 requests a
    second in the worst case and far less in practice. A limit that a human can trip is a limit
    that gets removed rather than tuned.
    """
    per_minute = min(DEFAULT_LIMITS, key=lambda limit: limit.window_s)

    assert per_minute.max_requests >= 60
    assert all(limit.max_requests >= per_minute.max_requests for limit in DEFAULT_LIMITS)


def test_limits_are_not_enforced_outside_a_deployment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Local runs, including the evaluation harness, are never throttled."""
    monkeypatch.delenv("VERCEL_ENV", raising=False)

    assert rate_limiting_enabled() is False


def test_limits_are_enforced_on_a_deployment(monkeypatch: pytest.MonkeyPatch) -> None:
    """A deployment enforces them."""
    monkeypatch.setenv("VERCEL_ENV", "production")

    assert rate_limiting_enabled() is True


def test_middleware_refuses_with_the_api_error_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """A throttled response is a typed error, not a bare 429.

    Every other failure in this service returns ``{"error": {"code", "message"}}``; a limiter
    that returned something else would be a hole in the contract clients are written against.
    """
    monkeypatch.setenv("VERCEL_ENV", "production")

    from api import ratelimit

    monkeypatch.setattr(
        ratelimit, "limiter", ratelimit.SlidingWindowLimiter((Limit(max_requests=2, window_s=60),))
    )

    # /api/stars reads a bundled catalogue, so this exercises the middleware without reaching
    # Celestrak. A limiter test that depends on a third party fails for reasons unrelated to the
    # limiter.
    with TestClient(app) as client:
        headers = {"x-forwarded-for": "198.51.100.42"}
        for _ in range(2):
            assert client.get("/api/stars", headers=headers).status_code == 200

        response = client.get("/api/stars", headers=headers)

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"
    assert int(response.headers["Retry-After"]) >= 1


def test_health_is_never_throttled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Monitoring must not be able to make the service look unhealthy.

    A limiter that can 429 the health check turns a traffic spike into an apparent outage, which
    is the problem it exists to prevent.
    """
    monkeypatch.setenv("VERCEL_ENV", "production")

    from api import ratelimit

    monkeypatch.setattr(
        ratelimit, "limiter", ratelimit.SlidingWindowLimiter((Limit(max_requests=1, window_s=60),))
    )

    with TestClient(app) as client:
        headers = {"x-forwarded-for": "198.51.100.43"}
        for _ in range(5):
            assert client.get("/api/health", headers=headers).status_code == 200

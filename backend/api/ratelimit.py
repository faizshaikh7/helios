"""Abuse control for the public science endpoints.

Every route below ``/api`` is unauthenticated, and most of them are cheap. The one that is not
cheap is the catalog: a request naming an unseen NORAD id reaches Celestrak, which is a shared
third-party service that degrades under bursts -- 500s and 503s have been observed from it. An
unthrottled public endpoint in front of it turns one scripted caller into this project's outage
*and* a nuisance to Celestrak.

So the limits here exist to protect an upstream dependency and this deployment's CPU, not a paid
quota. That is why there is no global budget as there is on the agent route: there is no shared
spend to exhaust, only a shared service to be rude to.

**Limits are deliberately generous.** The solar-system view fetches on a debounced time slider,
so a person scrubbing through dates legitimately produces a steady stream of requests. A limit
tight enough to feel like protection would break ordinary use, which is the failure mode that
gets a limiter deleted rather than tuned.

What this does not guarantee: state lives in this process's memory, so with several instances
each carries its own counters. The effective ceiling is ``instances x limit``. This narrows the
blast radius; it is not a hard cap. A hard cap needs shared state, which is a provisioned
dependency and a hosting decision.
"""

from __future__ import annotations

import os
import time
from collections import OrderedDict, deque
from dataclasses import dataclass

from fastapi import Request
from fastapi.responses import JSONResponse

# Paths that are never limited. Health is polled by monitoring, and a limiter that can make a
# service look unhealthy has made the problem it was meant to prevent.
EXEMPT_PATHS = frozenset({"/api/health"})


@dataclass(frozen=True)
class Limit:
    """One sliding window: how many requests, over how long."""

    max_requests: int
    window_s: float

    def describe(self) -> str:
        """Human-readable form of the limit, for the error a caller actually reads."""
        if self.window_s >= 3600:
            return f"{self.max_requests} requests per hour"
        if self.window_s >= 60:
            return f"{self.max_requests} requests per minute"
        return f"{self.max_requests} requests per {self.window_s:g} seconds"


# A person dragging the time slider produces a debounced but steady stream, so the per-minute
# figure is set well above interactive use and aimed at scripted traffic instead.
DEFAULT_LIMITS: tuple[Limit, ...] = (
    Limit(max_requests=120, window_s=60),
    Limit(max_requests=1200, window_s=3600),
)

MAX_TRACKED_CLIENTS = 10_000


@dataclass(frozen=True)
class Decision:
    """Outcome of a limit check."""

    allowed: bool
    message: str = ""
    retry_after_s: int = 0


class SlidingWindowLimiter:
    """Per-client sliding-window limiter holding its counters in process memory.

    Several windows are checked together, so a caller can be held to a burst rate and a sustained
    rate at once. Checking only a burst window lets a caller sustain that burst rate forever;
    checking only a long window lets it spend the whole hour's allowance in a second.
    """

    def __init__(
        self,
        limits: tuple[Limit, ...] = DEFAULT_LIMITS,
        max_tracked_clients: int = MAX_TRACKED_CLIENTS,
    ) -> None:
        """Build a limiter.

        Args:
            limits: Windows to enforce. The widest window decides how long history is kept.
            max_tracked_clients: Ceiling on remembered clients, so the map cannot grow unbounded.
        """
        self._limits = limits
        self._max_tracked_clients = max_tracked_clients
        self._widest_window_s = max(limit.window_s for limit in limits)
        # Ordered by recency of last request, so eviction can drop the stalest client in O(1).
        self._clients: OrderedDict[str, deque[float]] = OrderedDict()

    def check(self, client_id: str, now: float | None = None) -> Decision:
        """Decide whether a request may proceed, recording it if so.

        Args:
            client_id: Stable identifier for the caller.
            now: Current monotonic-ish time in seconds; injectable so the policy is testable
                without sleeping.

        Returns:
            Whether the request is allowed, and if not, why and for how long to wait.
        """
        moment = time.time() if now is None else now

        hits = self._clients.get(client_id)
        if hits is None:
            hits = deque()
        else:
            self._clients.move_to_end(client_id)

        cutoff = moment - self._widest_window_s
        while hits and hits[0] <= cutoff:
            hits.popleft()

        for limit in self._limits:
            window_start = moment - limit.window_s
            in_window = [at for at in hits if at > window_start]

            if len(in_window) >= limit.max_requests:
                # Store the pruned history even on refusal, so retrying through a refusal cannot
                # keep a caller's window from expiring.
                self._clients[client_id] = hits
                self._evict_if_crowded()
                oldest = in_window[0]
                return Decision(
                    allowed=False,
                    message=(
                        f"Too many requests. This service allows {limit.describe()} per client."
                    ),
                    retry_after_s=max(1, int(oldest + limit.window_s - moment) + 1),
                )

        hits.append(moment)
        self._clients[client_id] = hits
        self._clients.move_to_end(client_id)
        self._evict_if_crowded()

        return Decision(allowed=True)

    def tracked_clients(self) -> int:
        """How many clients are currently held in memory. Exposed so the bound is assertable."""
        return len(self._clients)

    def _evict_if_crowded(self) -> None:
        """Drop least-recently-seen clients so memory stays bounded.

        Expiry alone is not enough: a flood of distinct identifiers adds entries faster than the
        widest window retires them, and an unbounded map in a reused instance is a leak.
        """
        while len(self._clients) > self._max_tracked_clients:
            self._clients.popitem(last=False)


def client_id_from_request(request: Request) -> str:
    """Identify the caller from proxy headers.

    Vercel sets ``x-forwarded-for`` with the client address first. The header is caller-supplied
    in principle, so this is a cooperative identifier rather than proof of identity. It is the
    right granularity for protecting an upstream from accidental hammering, which is what this
    limiter is for; it is not an authentication mechanism and is not used as one.

    Args:
        request: Incoming request.

    Returns:
        A stable key for the caller, or a shared fallback when no address is present.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first

    real = (request.headers.get("x-real-ip") or "").strip()
    if real:
        return real

    if request.client and request.client.host:
        return request.client.host

    return "unidentified"


def rate_limiting_enabled() -> bool:
    """Whether limits apply in this environment.

    Enforced only on a deployment, detected by ``VERCEL_ENV``. The evaluation harness drives
    hundreds of requests through a local server in one run, and a limiter that throttled that
    would be measuring itself rather than the tools. The policy is still verified in
    development -- by its own tests, which drive the limiter directly.

    Returns:
        True when running on a Vercel deployment.
    """
    return bool(os.environ.get("VERCEL_ENV"))


limiter = SlidingWindowLimiter()


async def rate_limit_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    """Refuse requests from a client that is over its allowance.

    Args:
        request: Incoming request.
        call_next: The rest of the application stack.

    Returns:
        The downstream response, or a 429 with a typed error body and ``Retry-After``.
    """
    if not rate_limiting_enabled() or request.url.path in EXEMPT_PATHS:
        return await call_next(request)

    decision = limiter.check(client_id_from_request(request))
    if decision.allowed:
        return await call_next(request)

    return JSONResponse(
        status_code=429,
        content={"error": {"code": "rate_limited", "message": decision.message}},
        headers={"Retry-After": str(decision.retry_after_s)},
    )

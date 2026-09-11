/**
 * Tests for the agent endpoint's abuse control.
 *
 * Run with `npm run test:web`. Node 24 executes TypeScript directly, so this needs no test
 * framework and no build step -- one fewer dependency to justify, and nothing to keep in sync
 * with the compiler the rest of the project uses.
 *
 * Every test drives the limiter with an injected clock rather than real time. A limiter tested
 * against `Date.now()` either sleeps (slow, and flaky under load) or only ever exercises the
 * first window, which is the half that works.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  clientIdFromHeaders,
  DEFAULT_RATE_LIMIT,
  RateLimiter,
  type RateLimitConfig,
} from "./rateLimit.ts";

/** A deliberately small policy, so limits are reachable in a few calls. */
const TEST_CONFIG: RateLimitConfig = {
  burstLimit: 3,
  burstWindowMs: 60_000,
  sustainedLimit: 5,
  sustainedWindowMs: 3_600_000,
  budgetLimit: 10,
  budgetWindowMs: 86_400_000,
  maxTrackedClients: 4,
};

const T0 = 1_000_000_000_000;

describe("per-client burst limit", () => {
  it("admits requests up to the limit", () => {
    const limiter = new RateLimiter(TEST_CONFIG);

    for (let index = 0; index < TEST_CONFIG.burstLimit; index += 1) {
      assert.equal(limiter.check("a", T0 + index).allowed, true, `request ${index} should pass`);
    }
  });

  it("refuses the request past the limit, and says how long to wait", () => {
    const limiter = new RateLimiter(TEST_CONFIG);
    for (let index = 0; index < TEST_CONFIG.burstLimit; index += 1) limiter.check("a", T0);

    const decision = limiter.check("a", T0);

    assert.equal(decision.allowed, false);
    assert.equal(decision.allowed === false && decision.code, "rate_limited");
    // The window opened at T0, so the oldest hit clears a full window later.
    assert.equal(decision.allowed === false && decision.retryAfterS, 60);
  });

  it("admits again once the window has slid past the old hits", () => {
    const limiter = new RateLimiter(TEST_CONFIG);
    for (let index = 0; index < TEST_CONFIG.burstLimit; index += 1) limiter.check("a", T0);

    assert.equal(limiter.check("a", T0).allowed, false);
    assert.equal(limiter.check("a", T0 + TEST_CONFIG.burstWindowMs + 1).allowed, true);
  });

  it("counts each client separately", () => {
    const limiter = new RateLimiter(TEST_CONFIG);
    for (let index = 0; index < TEST_CONFIG.burstLimit; index += 1) limiter.check("a", T0);

    assert.equal(limiter.check("a", T0).allowed, false);
    assert.equal(limiter.check("b", T0).allowed, true);
  });

  it("does not let refused requests extend the window", () => {
    const limiter = new RateLimiter(TEST_CONFIG);
    for (let index = 0; index < TEST_CONFIG.burstLimit; index += 1) limiter.check("a", T0);

    // Hammering through the refusal must not push the expiry outward; the caller that keeps
    // retrying should still be admitted exactly one window after its last *successful* request.
    for (let index = 0; index < 20; index += 1) limiter.check("a", T0 + 1000 + index);

    assert.equal(limiter.check("a", T0 + TEST_CONFIG.burstWindowMs + 1).allowed, true);
  });
});

describe("per-client sustained limit", () => {
  it("refuses a caller that paces itself under the burst limit but exceeds the hourly one", () => {
    const limiter = new RateLimiter(TEST_CONFIG);
    const spacing = TEST_CONFIG.burstWindowMs + 1;

    for (let index = 0; index < TEST_CONFIG.sustainedLimit; index += 1) {
      assert.equal(limiter.check("a", T0 + index * spacing).allowed, true);
    }

    const decision = limiter.check("a", T0 + TEST_CONFIG.sustainedLimit * spacing);

    assert.equal(decision.allowed, false);
    assert.equal(decision.allowed === false && decision.code, "rate_limited");
  });

  it("admits again once the hourly window has passed", () => {
    const limiter = new RateLimiter(TEST_CONFIG);
    const spacing = TEST_CONFIG.burstWindowMs + 1;
    for (let index = 0; index < TEST_CONFIG.sustainedLimit; index += 1) {
      limiter.check("a", T0 + index * spacing);
    }

    assert.equal(limiter.check("a", T0 + TEST_CONFIG.sustainedWindowMs + 1).allowed, true);
  });
});

describe("shared daily budget", () => {
  it("is charged in model calls, not in requests", () => {
    const limiter = new RateLimiter(TEST_CONFIG);

    limiter.check("a", T0);
    limiter.recordUsage(4, T0);

    assert.equal(limiter.spent(T0), 4);
  });

  it("floors a charge at one, so a failed call still costs something", () => {
    const limiter = new RateLimiter(TEST_CONFIG);

    limiter.recordUsage(0, T0);

    assert.equal(limiter.spent(T0), 1);
  });

  it("refuses everyone once exhausted, including a client with no history", () => {
    const limiter = new RateLimiter(TEST_CONFIG);
    limiter.recordUsage(TEST_CONFIG.budgetLimit, T0);

    const decision = limiter.check("never-seen-before", T0);

    assert.equal(decision.allowed, false);
    assert.equal(decision.allowed === false && decision.code, "daily_budget_exhausted");
  });

  it("holds against many distinct clients, which per-client limits alone would not", () => {
    const limiter = new RateLimiter(TEST_CONFIG);

    // Every caller is a fresh identity well inside its own allowance. The budget is the only
    // thing standing between that and an exhausted provider quota.
    let admitted = 0;
    for (let index = 0; index < 100; index += 1) {
      if (limiter.check(`client-${index}`, T0).allowed) {
        admitted += 1;
        limiter.recordUsage(1, T0);
      }
    }

    assert.equal(admitted, TEST_CONFIG.budgetLimit);
  });

  it("recovers once the budget window has passed", () => {
    const limiter = new RateLimiter(TEST_CONFIG);
    limiter.recordUsage(TEST_CONFIG.budgetLimit, T0);

    assert.equal(limiter.check("a", T0).allowed, false);
    assert.equal(limiter.check("a", T0 + TEST_CONFIG.budgetWindowMs + 1).allowed, true);
  });

  it("reports a wait that actually clears the budget", () => {
    const limiter = new RateLimiter(TEST_CONFIG);
    limiter.recordUsage(TEST_CONFIG.budgetLimit, T0);

    const decision = limiter.check("a", T0 + 1000);
    assert.equal(decision.allowed, false);

    const waitMs = (decision.allowed === false ? decision.retryAfterS : 0) * 1000;
    assert.equal(limiter.check("a", T0 + 1000 + waitMs).allowed, true);
  });
});

describe("memory bounds", () => {
  it("keeps the tracked-client map under its ceiling", () => {
    // A budget large enough that eviction, not exhaustion, is what bounds the map.
    const limiter = new RateLimiter({ ...TEST_CONFIG, budgetLimit: 1_000_000 });

    for (let index = 0; index < 500; index += 1) {
      limiter.check(`client-${index}`, T0 + index);
    }

    assert.equal(limiter.trackedClients(), TEST_CONFIG.maxTrackedClients);
  });

  it("evicts the least recently seen client first", () => {
    const limiter = new RateLimiter({ ...TEST_CONFIG, budgetLimit: 1_000_000 });

    for (let index = 0; index < TEST_CONFIG.maxTrackedClients; index += 1) {
      limiter.check(`client-${index}`, T0 + index);
    }
    // Refresh the oldest client, so recency rather than arrival order decides who goes.
    limiter.check("client-0", T0 + 10_000);
    limiter.check("newcomer", T0 + 10_001);

    assert.equal(limiter.trackedClients(), TEST_CONFIG.maxTrackedClients);

    // client-1 is now the least recently seen, so it was dropped: its burst window starts over,
    // and it gets a full allowance again. client-0 was kept, so its history still counts.
    for (let index = 0; index < TEST_CONFIG.burstLimit; index += 1) {
      assert.equal(limiter.check("client-1", T0 + 20_000 + index).allowed, true);
    }
  });
});

describe("client identification", () => {
  it("takes the first address from x-forwarded-for", () => {
    const headers = new Headers({ "x-forwarded-for": "203.0.113.7, 70.41.3.18, 150.172.238.178" });

    assert.equal(clientIdFromHeaders(headers), "203.0.113.7");
  });

  it("falls back to x-real-ip", () => {
    assert.equal(clientIdFromHeaders(new Headers({ "x-real-ip": "203.0.113.9" })), "203.0.113.9");
  });

  it("puts unidentifiable callers in one shared bucket", () => {
    assert.equal(clientIdFromHeaders(new Headers()), "unidentified");
  });

  it("ignores an empty x-forwarded-for rather than keying on blank", () => {
    assert.equal(clientIdFromHeaders(new Headers({ "x-forwarded-for": "" })), "unidentified");
  });
});

describe("default policy", () => {
  it("keeps the daily budget under the Gemini free tier's 500 requests per day", () => {
    // The budget exists to fail with this endpoint's own clear error rather than the provider's
    // opaque one. If it ever rises above the provider's cap, it stops doing that.
    assert.ok(DEFAULT_RATE_LIMIT.budgetLimit < 500);
  });

  it("allows a burst smaller than the sustained allowance", () => {
    assert.ok(DEFAULT_RATE_LIMIT.burstLimit < DEFAULT_RATE_LIMIT.sustainedLimit);
  });
});

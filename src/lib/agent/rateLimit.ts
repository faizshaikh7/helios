/**
 * Abuse control for the public agent endpoint.
 *
 * `/api/agent` is unauthenticated by design -- a stranger has to be able to ask a question for
 * the project to make its argument at all. That openness spends a real provider quota, so the
 * endpoint needs a budget rather than a door.
 *
 * Two limits, because they defend against different things:
 *
 * - **Per client**, which stops one caller monopolising the service.
 * - **A global daily budget**, which stops the provider quota being drained at all. Per-client
 *   limits alone do not defend it: the quota is shared, so enough distinct callers exhaust it
 *   while every one of them stays politely inside its own allowance.
 *
 * The global budget is charged in **model calls, after the fact**, not in requests. A grounded
 * question makes one model call per tool-calling round, so counting requests would either
 * under-count real usage or have to assume the worst case on every question. The agent reports
 * how many steps it actually took; that is what gets charged.
 *
 * ## What this does not guarantee
 *
 * State lives in this process's memory. Vercel's Fluid Compute reuses instances, so a sustained
 * caller does keep hitting the same counters -- but under concurrency there can be several
 * instances, and each carries its own. The effective global ceiling is therefore
 * `instances x DAILY_MODEL_CALL_BUDGET`, not `DAILY_MODEL_CALL_BUDGET`.
 *
 * That is stated rather than papered over: this is a meaningful reduction in blast radius, not
 * a hard cap. A hard cap needs shared state (Redis or similar), which is a hosting decision and
 * a provisioned dependency. The honest version of this control is the one that says which of the
 * two it is.
 */

/** Tunables, kept together so the policy is readable in one place. */
export type RateLimitConfig = {
  /** Requests allowed per client inside `burstWindowMs`. */
  burstLimit: number;
  burstWindowMs: number;
  /** Requests allowed per client inside `sustainedWindowMs`. */
  sustainedLimit: number;
  sustainedWindowMs: number;
  /** Model calls allowed across all clients inside `budgetWindowMs`. */
  budgetLimit: number;
  budgetWindowMs: number;
  /** Ceiling on tracked clients, so the map cannot grow without bound. */
  maxTrackedClients: number;
};

const SECOND = 1000;
const MINUTE = 60 * SECOND;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/**
 * Default policy.
 *
 * The daily budget is set below the Gemini free tier's 500 requests per day for the lite model,
 * so that exhaustion produces *this* endpoint's clear 429 rather than the provider's opaque one.
 * A caller who sees "the service has used its budget for today" can act on it; a raw provider
 * quota error reads like a bug.
 *
 * **The figure is per instance, so it is chosen for the aggregate, not for one process.** The
 * budget cannot be shared without shared state, so under concurrency the real ceiling is
 * `instances x budgetLimit`. Setting this to the provider's own cap would therefore overshoot it
 * by whatever the instance count happens to be. At 150 the aggregate stays near 500 across the
 * handful of instances this workload actually spreads over, while a single-instance deployment
 * -- the common case -- still serves 150 questions a day, far more than a demo needs.
 *
 * The provider's free tier is itself a hard cap, and it is the one that bounds *spend*: with no
 * billing attached, exceeding it costs nothing and simply fails. This budget exists to make that
 * boundary legible and to keep one caller from consuming the day's allowance, not to be the last
 * line of defence against a bill. If a paid key is ever attached, that changes: the budget
 * becomes the only thing between a caller and real money, and a shared counter earns its keep.
 */
export const DEFAULT_RATE_LIMIT: RateLimitConfig = {
  burstLimit: 5,
  burstWindowMs: MINUTE,
  sustainedLimit: 40,
  sustainedWindowMs: HOUR,
  budgetLimit: 150,
  budgetWindowMs: DAY,
  maxTrackedClients: 10_000,
};

/** Why a request was refused, and how long the caller should wait. */
export type RateLimitDecision =
  | { allowed: true }
  | {
      allowed: false;
      code: "rate_limited" | "daily_budget_exhausted";
      message: string;
      retryAfterS: number;
    };

/** One client's request timestamps, newest last. */
type ClientRecord = {
  /** Millisecond timestamps of recent requests, ascending. */
  hits: number[];
};

/** A charge against the shared budget: when it happened, and how many model calls it cost. */
type BudgetEntry = {
  at: number;
  cost: number;
};

/**
 * Drop timestamps that have fallen out of a sliding window.
 *
 * @param hits - Ascending timestamps; mutated in place.
 * @param cutoff - Anything at or before this instant is expired.
 */
function pruneHits(hits: number[], cutoff: number): void {
  let keepFrom = 0;
  while (keepFrom < hits.length && hits[keepFrom] <= cutoff) keepFrom += 1;
  if (keepFrom > 0) hits.splice(0, keepFrom);
}

/**
 * Seconds a caller must wait for the oldest hit in a window to expire.
 *
 * Reported rather than a fixed guess, so a caller that honours `Retry-After` comes back exactly
 * when it can succeed instead of being told to wait a round number and failing again.
 *
 * @param hits - Ascending timestamps inside the window.
 * @param windowMs - Width of the window.
 * @param now - Current instant.
 * @returns Whole seconds to wait, at least one.
 */
function retryAfterSeconds(hits: number[], windowMs: number, now: number): number {
  const oldest = hits[0];
  if (oldest === undefined) return 1;
  return Math.max(1, Math.ceil((oldest + windowMs - now) / SECOND));
}

/**
 * Sliding-window limiter holding its counters in process memory.
 *
 * Instantiated once per module load; see the file header for what that does and does not
 * guarantee in a serverless environment.
 */
export class RateLimiter {
  private readonly config: RateLimitConfig;
  private readonly clients = new Map<string, ClientRecord>();
  private budget: BudgetEntry[] = [];

  /**
   * @param config - Policy to enforce. Defaults to {@link DEFAULT_RATE_LIMIT}.
   */
  constructor(config: RateLimitConfig = DEFAULT_RATE_LIMIT) {
    this.config = config;
  }

  /**
   * Decide whether a request may proceed, recording it if so.
   *
   * The shared budget is checked here but charged in {@link recordUsage}, once the real cost is
   * known. A request admitted on the last unit of budget is therefore allowed to finish; the
   * budget goes negative-ish by at most one question, which is the right trade against refusing
   * a caller for a cost that had not been incurred yet.
   *
   * @param clientId - Stable identifier for the caller, from {@link clientIdFromHeaders}.
   * @param now - Current instant in milliseconds; injectable so the policy is testable.
   * @returns Whether the request may proceed, and if not, why and for how long.
   */
  check(clientId: string, now: number = Date.now()): RateLimitDecision {
    this.pruneBudget(now);

    const spent = this.budget.reduce((total, entry) => total + entry.cost, 0);
    if (spent >= this.config.budgetLimit) {
      const oldest = this.budget[0]?.at ?? now;
      return {
        allowed: false,
        code: "daily_budget_exhausted",
        message:
          "This deployment has used its model budget for today. The tools themselves are " +
          "unaffected -- every /api endpoint below the agent keeps working, and the agent " +
          "returns tomorrow. Running locally with your own provider key has no such limit.",
        retryAfterS: Math.max(1, Math.ceil((oldest + this.config.budgetWindowMs - now) / SECOND)),
      };
    }

    const record = this.clients.get(clientId) ?? { hits: [] };
    pruneHits(record.hits, now - this.config.sustainedWindowMs);

    const burstHits = record.hits.filter((at) => at > now - this.config.burstWindowMs);
    if (burstHits.length >= this.config.burstLimit) {
      // Store the pruned record even on refusal, so a caller hammering the endpoint cannot use
      // rejected requests to keep its own window from being cleaned up.
      this.clients.set(clientId, record);
      return {
        allowed: false,
        code: "rate_limited",
        message: `Too many requests. This endpoint allows ${this.config.burstLimit} questions per minute.`,
        retryAfterS: retryAfterSeconds(burstHits, this.config.burstWindowMs, now),
      };
    }

    if (record.hits.length >= this.config.sustainedLimit) {
      this.clients.set(clientId, record);
      return {
        allowed: false,
        code: "rate_limited",
        message: `Too many requests. This endpoint allows ${this.config.sustainedLimit} questions per hour.`,
        retryAfterS: retryAfterSeconds(record.hits, this.config.sustainedWindowMs, now),
      };
    }

    record.hits.push(now);
    this.clients.set(clientId, record);
    this.evictIfCrowded();

    return { allowed: true };
  }

  /**
   * Charge the shared budget for work that actually happened.
   *
   * @param modelCalls - Model calls the request consumed. Floored at one, because a request that
   *   reached the provider cost something even if it failed before reporting a step.
   * @param now - Current instant in milliseconds.
   */
  recordUsage(modelCalls: number, now: number = Date.now()): void {
    this.budget.push({ at: now, cost: Math.max(1, Math.round(modelCalls)) });
    this.pruneBudget(now);
  }

  /** Model calls charged inside the current budget window. Exposed for tests and diagnostics. */
  spent(now: number = Date.now()): number {
    this.pruneBudget(now);
    return this.budget.reduce((total, entry) => total + entry.cost, 0);
  }

  /** How many clients are currently held in memory. Exposed so the bound can be asserted. */
  trackedClients(): number {
    return this.clients.size;
  }

  /** Drop budget charges that have aged out of the window. */
  private pruneBudget(now: number): void {
    const cutoff = now - this.config.budgetWindowMs;
    let keepFrom = 0;
    while (keepFrom < this.budget.length && this.budget[keepFrom].at <= cutoff) keepFrom += 1;
    if (keepFrom > 0) this.budget = this.budget.slice(keepFrom);
  }

  /**
   * Bound memory by discarding the least recently seen clients.
   *
   * Expiry alone is not enough: a flood of distinct identifiers can add entries faster than the
   * sustained window retires them, and an unbounded map in a long-lived instance is a leak.
   */
  private evictIfCrowded(): void {
    if (this.clients.size <= this.config.maxTrackedClients) return;

    const byLastSeen = [...this.clients.entries()].sort(
      (a, b) => (a[1].hits.at(-1) ?? 0) - (b[1].hits.at(-1) ?? 0),
    );

    const excess = this.clients.size - this.config.maxTrackedClients;
    for (let index = 0; index < excess; index += 1) {
      this.clients.delete(byLastSeen[index][0]);
    }
  }
}

/**
 * Identify the caller from proxy headers.
 *
 * Vercel sets `x-forwarded-for` on every request, with the client address first, and **it
 * overwrites whatever the caller sent**. Measured against the deployment rather than assumed:
 * 130 requests each claiming a different address were limited at exactly 120, the per-minute
 * allowance, instead of passing as 130 separate clients. So identity here cannot be rotated by
 * spoofing the header.
 *
 * It is still not proof of identity -- a real botnet has real addresses, and a NAT puts many
 * people behind one. That is what the global budget is for: it holds regardless of how many
 * distinct addresses a caller actually has.
 *
 * @param headers - Request headers.
 * @returns A stable key for the caller, or a shared fallback when no address is present.
 */
export function clientIdFromHeaders(headers: Headers): string {
  const forwarded = headers.get("x-forwarded-for");
  if (forwarded) {
    const first = forwarded.split(",")[0]?.trim();
    if (first) return first;
  }

  const real = headers.get("x-real-ip")?.trim();
  if (real) return real;

  // Everything unidentifiable shares one bucket. Local development has no proxy header, which is
  // the usual way to land here.
  return "unidentified";
}

/**
 * Whether limits apply in this environment.
 *
 * Enforced only on a deployment. The evaluation harness fires 157 questions at a local server in
 * one run, and a limiter that blocked that would be measuring itself instead of the agent. The
 * policy is still verified in development -- by its own tests, which drive {@link RateLimiter}
 * directly rather than through the route.
 *
 * @returns True when the process is a production build.
 */
export function rateLimitingEnabled(): boolean {
  return process.env.NODE_ENV === "production";
}

/**
 * Process-wide limiter for the agent route.
 *
 * Module scope is deliberate: it must survive between requests handled by the same instance,
 * which is exactly what makes it effective under Fluid Compute's instance reuse.
 */
export const agentRateLimiter = new RateLimiter();

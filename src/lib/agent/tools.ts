import { tool } from "ai";
import { z } from "zod";

/**
 * Where the Python science service listens.
 *
 * Tools run server-side inside a route handler, so they call the service directly rather than
 * through the browser-facing `/api` rewrite -- that rewrite exists for the client and does not
 * apply to server-to-server calls.
 */
const SCIENCE_URL = process.env.SCIENCE_SERVICE_URL ?? "http://127.0.0.1:8787";

/** Every tool result the agent produced, in call order, for provenance rendering. */
export type CollectedCall = {
  tool: string;
  input: unknown;
  output: unknown;
};

/**
 * Call the science service and return its parsed body.
 *
 * Errors are returned as data rather than thrown: a thrown error aborts the agent loop, whereas
 * a returned error lets the model see what went wrong and either correct its parameters or tell
 * the user honestly that it could not compute the answer. Silent failure is the outcome to
 * avoid, not visible failure.
 *
 * @param path - Service path, e.g. `/api/passes`.
 * @param body - JSON body for POST, or undefined for GET.
 * @returns The parsed response, or an error object the model can reason about.
 */
async function callScience(path: string, body?: unknown): Promise<unknown> {
  try {
    const response = await fetch(`${SCIENCE_URL}${path}`, {
      method: body === undefined ? "GET" : "POST",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(120_000),
    });

    const parsed = await response.json().catch(() => null);

    if (!response.ok) {
      return {
        error: true,
        status: response.status,
        detail: parsed ?? `HTTP ${response.status}`,
      };
    }

    return parsed;
  } catch (caught) {
    return {
      error: true,
      detail: caught instanceof Error ? caught.message : String(caught),
    };
  }
}

const noradId = z
  .number()
  .int()
  .min(1)
  .describe("NORAD catalog number, e.g. 25544 for the ISS.");

/**
 * The agent's tool surface.
 *
 * Descriptions are written for the model rather than for a human reader: they state when a tool
 * applies and what units come back, because the commonest agent failure here is not picking the
 * wrong tool but supplying the right tool with the wrong parameters.
 */
export const scienceTools = {
  lookupSatellite: tool({
    description:
      "Look up a satellite's current orbital element set (TLE) by NORAD catalog number. " +
      "Returns its name, the element set, its epoch, and how old the element set is in days. " +
      "Use this first when a question names a satellite, to confirm identity and data freshness.",
    inputSchema: z.object({ norad_id: noradId }),
    execute: async ({ norad_id }) => callScience(`/api/satellite/${norad_id}`),
  }),

  findPasses: tool({
    description:
      "Predict when a satellite is visible from a ground station: rise, culmination and set " +
      "times, maximum elevation in degrees, and duration in seconds. Use for any question " +
      "about when a station can see, contact, or track a satellite. min_elevation_deg is the " +
      "elevation mask -- the angle above the horizon below which the station cannot work the " +
      "satellite; 10 degrees is a typical default.",
    inputSchema: z.object({
      norad_id: noradId,
      latitude_deg: z.number().min(-90).max(90).describe("Station latitude, degrees north."),
      longitude_deg: z.number().min(-180).max(180).describe("Station longitude, degrees east."),
      elevation_m: z.number().default(0).describe("Station height above the ellipsoid, metres."),
      station_name: z.string().default("Ground station"),
      min_elevation_deg: z.number().min(0).max(89).default(10),
      days: z.number().min(0.1).max(10).default(1).describe("Search window length in days."),
    }),
    execute: async (input) => callScience("/api/passes", input),
  }),

  stateAtTime: tool({
    description:
      "Get where a satellite is at a SPECIFIC instant: sub-satellite latitude and longitude in " +
      "degrees, altitude in kilometres above the WGS84 ellipsoid, and speed in metres per " +
      "second. Use this whenever a question names a time, such as 'at 2026-08-12T00:00:00Z'. " +
      "Omit at_utc for the current moment. Prefer this over groundTrack for a single instant.",
    inputSchema: z.object({
      norad_id: noradId,
      at_utc: z
        .string()
        .optional()
        .describe("ISO-8601 UTC instant, e.g. 2026-08-12T00:00:00Z. Omit for now."),
    }),
    execute: async (input) => callScience("/api/state", input),
  }),

  groundTrack: tool({
    description:
      "Sample a satellite's sub-satellite point (latitude, longitude, altitude) over time, " +
      "starting now. Use for questions about where a satellite is or will be over the Earth. " +
      "Returns altitude in kilometres above the WGS84 ellipsoid.",
    inputSchema: z.object({
      norad_id: noradId,
      minutes: z.number().int().min(1).max(1440).default(100),
      step_seconds: z.number().int().min(5).max(600).default(30),
    }),
    execute: async (input) => callScience("/api/groundtrack", input),
  }),

  orbitalElements: tool({
    description:
      "Get a satellite's state vector and orbital elements: semi-major axis, eccentricity, " +
      "inclination, RAAN, argument of perigee, true anomaly, plus orbital period, mean motion, " +
      "and apogee and perigee altitudes. Use for questions about orbit shape, size, " +
      "orientation, period, or speed. Elements are osculating, not the mean values a TLE prints.",
    inputSchema: z.object({
      norad_id: noradId,
      at_epoch: z
        .boolean()
        .default(true)
        .describe("True evaluates at the element set's own epoch, where results are strongest."),
    }),
    execute: async (input) => callScience("/api/elements", input),
  }),

  eclipseAnalysis: tool({
    description:
      "Compute beta angle and eclipse statistics: how many times a satellite enters Earth's " +
      "shadow, the longest eclipse, and the fraction of each orbit spent in shadow. Use for " +
      "questions about power, batteries, thermal design, sunlight, or shadow.",
    inputSchema: z.object({
      norad_id: noradId,
      days: z.number().min(0.1).max(10).default(1),
    }),
    execute: async (input) => callScience("/api/eclipse", input),
  }),
} as const;

/** Instructions given to the agent. */
export const AGENT_INSTRUCTIONS = `
You are Atlas, a scientific assistant for orbital mechanics.

Your defining constraint: **you do not know orbital positions, and you must never guess them.**
Any question about where a satellite is, when it passes over somewhere, how fast it is moving,
its orbit geometry, or its eclipses MUST be answered by calling a tool. A number you recall from
training is not an acceptable answer, even if it sounds right - satellite positions change every
second and element sets are updated daily.

How to answer:
- Call the tools you need. Chain them when a question requires it.
- Report numbers exactly as the tools return them, with their units. Do not round away
  significant digits and do not convert units silently - say so if you convert.
- State the trust tier of what you report. A measured element-set epoch is "observed", a
  quantity computed from it is "derived", and anything propagated to a future time is
  "predicted" - predictions carry error that grows with time from the element set's epoch.
- Give the assumptions that mattered: which satellite, which epoch, which elevation mask.
- If a tool returns an error, say plainly what failed. Do not substitute a remembered value.
- If a question cannot be answered with the available tools, say so and explain what is missing.
  Declining is a better answer than a plausible invention.

**Do not present inference as computation.** The tools compute geometry only. They do not
compute whether a pass is visible to the naked eye, whether it is daylight, whether the
satellite is sunlit, link margin, or weather. If you add any such judgement, you MUST mark it
explicitly as your own inference and not a computed result - for example "not computed: this is
around local sunrise, so contrast may be poor". Silently mixing a reasoned guess in among
computed numbers is the single worst thing you can do here, because the reader cannot tell which
is which.

Read the tool's own notes and uncertainty fields and respect them. If a receipt says results
ignore refraction and terrain, do not claim a pass is workable - only that it is geometrically
visible.

Be concise. Lead with the answer, then the assumptions behind it.
`.trim();

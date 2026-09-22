import { tool } from "ai";
import { z } from "zod";

/**
 * Where the Python science service listens.
 *
 * Tools run server-side inside a route handler, so they call the service directly rather than
 * through the browser-facing `/api` rewrite -- that rewrite exists for the client and does not
 * apply to server-to-server calls.
 *
 * In a deployment this value is injected by a Vercel service binding, which is deployment-aware
 * (a preview's web service reaches that same preview's science service) and bypasses the public
 * request pipeline -- so Deployment Protection does not block an internal call. Locally it comes
 * from .env.local. The fallback is the local dev port.
 *
 * The trailing slash is stripped because callers append absolute paths like `/api/health`, and a
 * base ending in `/` would produce a double slash.
 */
const SCIENCE_URL = (process.env.SCIENCE_SERVICE_URL ?? "http://127.0.0.1:8787").replace(
  /\/+$/,
  "",
);

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
      from_utc: z
        .string()
        .optional()
        .describe(
          "ISO-8601 UTC instant to start the search from, e.g. 2026-08-12T00:00:00Z. " +
            "REQUIRED whenever the question names any time - 'after <time>', 'from <date>', " +
            "'during the 3 days from <date>', or 'the first pass after <time>'. Omitting it " +
            "silently searches from the current moment instead, which returns a DIFFERENT set " +
            "of passes and a different first pass. Only omit it for questions about now.",
        ),
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

  decayLifetime: tool({
    description:
      "Estimate how long an orbit survives atmospheric drag before reentry. Use for questions " +
      "about orbital lifetime, deorbit time, decay, reentry timing, or the 25-year disposal " +
      "guideline. Give either norad_id (reads the altitude from the current orbit) or " +
      "altitude_km (for an orbit that does not exist yet, e.g. mission planning). " +
      "Returns a RANGE across weak, average and strong solar activity, never a single number: " +
      "lifetime depends on solar activity, which cannot be forecast years ahead. Report the " +
      "range as the answer - quoting only the nominal figure misrepresents the result.",
    inputSchema: z.object({
      norad_id: noradId.optional(),
      altitude_km: z
        .number()
        .min(100)
        .max(2000)
        .optional()
        .describe("Circular altitude. Give this OR norad_id, not both."),
      mass_kg: z.number().positive().default(3.3).describe("Default is a 3U cubesat."),
      cross_section_m2: z.number().positive().default(0.03),
      drag_coefficient: z.number().positive().max(5).default(2.2),
    }),
    execute: async (input) => callScience("/api/decay", input),
  }),

  searchLiterature: tool({
    description:
      "Search the scientific literature on arXiv and return real papers with identifiers, " +
      "authors, abstracts and links. Use whenever a question calls for references, prior work, " +
      "published methods, or 'what does the research say'. This is the ONLY acceptable source " +
      "of a citation: every reference you give must come from a result this tool returned. " +
      "arXiv is a preprint server, so a record existing means the paper exists, not that it is " +
      "peer-reviewed or correct.",
    inputSchema: z.object({
      query: z
        .string()
        .min(3)
        .max(400)
        .describe("Search terms. Topic keywords work better than a full sentence."),
      max_results: z.number().int().min(1).max(25).default(8),
    }),
    execute: async (input) => callScience("/api/literature/search", input),
  }),

  verifyCitations: tool({
    description:
      "Check whether claimed arXiv identifiers correspond to real papers. Use this before " +
      "repeating any citation you did not get from searchLiterature - including one you " +
      "remember, one the user supplied, or one you are about to write from memory. Returns " +
      "'verified' (real), 'not_found' (well-formed but no such paper - treat as fabricated), " +
      "'malformed' (not an identifier), or 'unchecked' (service unreachable, which is not " +
      "evidence either way).",
    inputSchema: z.object({
      arxiv_ids: z
        .array(z.string().min(1).max(80))
        .min(1)
        .max(20)
        .describe("Claimed arXiv identifiers, in any common form."),
    }),
    execute: async (input) => callScience("/api/literature/verify", input),
  }),

  planetPosition: tool({
    description:
      "Get where the Sun, Moon or a planet is: barycentric position in AU, plus right " +
      "ascension, declination, distance and light travel time as seen from Earth. Use for " +
      "questions about planets, the Moon, the Sun, where a body is in the sky, or how far away " +
      "it is. Positions come from an analytic series, accurate to between 1e-6 and 3e-4 of the " +
      "body's distance depending on the body - good for orientation and 'where is it now', not " +
      "for navigation or occultation timing. Report the stated accuracy alongside the answer. " +
      "Only the Sun, Moon and eight planets are available: no asteroids, comets or exoplanets.",
    inputSchema: z.object({
      body: z
        .enum([
          "sun",
          "mercury",
          "venus",
          "earth",
          "moon",
          "mars",
          "jupiter",
          "saturn",
          "uranus",
          "neptune",
        ])
        .describe("Which body."),
      at_utc: z
        .string()
        .optional()
        .describe("ISO-8601 UTC instant, e.g. 2026-08-18T00:00:00Z. Omit for now."),
    }),
    execute: async (input) => callScience("/api/ephemeris/body", input),
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
- Report numbers exactly as the tools return them, in the units the tool used. Do not round away
  significant digits. **Do not convert units unless the question asked for a different unit** -
  an unrequested conversion adds a hand-done arithmetic step that nothing checks, and a slip
  there produces a wrong number sitting beside correct ones. If you do convert, say so and show
  the original value too.
- State the trust tier of what you report. A measured element-set epoch is "observed", a
  quantity computed from it is "derived", and anything propagated to a future time is
  "predicted" - predictions carry error that grows with time from the element set's epoch.
- Give the assumptions that mattered: which satellite, which epoch, which elevation mask.
- Copy dataset and source names exactly from receipt fields. Do not rename, expand, or correct
  them. In particular, the orbital catalogue source is "celestrak" when that is what the receipt
  says.
- Report numeric values from structured tool fields carrying a unit and receipt. Do not parse or
  derive additional numbers from raw TLE lines yourself; use orbitalElements for those values.
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

**Never cite from memory.** A reference you recall is indistinguishable, in the text you
write, from one you invented: the authors look right, the title sounds right, the identifier is
well-formed. So a citation is only allowed if searchLiterature returned it, or verifyCitations
confirmed it. If you find yourself about to write a paper title, an author name, or an arXiv
identifier that did not come back from a tool, stop and call one. If a citation comes back
"not_found", say plainly that it does not resolve rather than quietly dropping it or
substituting another - the reader needs to know a check ran.

Presence on arXiv is not peer review. Say what a record is: a preprint unless it carries a DOI
or journal reference, and its conclusions are its authors', not established fact. Citing a paper
is not endorsing it.

**When a tool returns a range, the range is the answer.** Some quantities - orbital decay
lifetime above all - depend on things nobody can forecast, so the tool deliberately returns a
bracket instead of a number. Report the bracket and say what drives it. Collapsing it to the
middle figure, or presenting the nominal value with the range as a footnote, throws away the
most important thing the tool computed and turns a defensible estimate into a false prediction.
If a receipt states a known model bias, carry that across too.

Read the tool's own notes and uncertainty fields and respect them. If a receipt says results
ignore refraction and terrain, do not claim a pass is workable - only that it is geometrically
visible.

**Carry every constraint from the question into the tool call.** If the question names a time,
a date, a window, an elevation mask, or a location, those belong in the parameters. A tool
called with defaults answers a different question than the one asked, and does so without any
error - the numbers come back looking perfectly reasonable. Before answering, check that each
constraint you were given appears somewhere in what you sent.

Be concise. Lead with the answer, then the assumptions behind it.
`.trim();

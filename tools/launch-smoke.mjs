/**
 * Launch smoke test for the public, reviewer-visible application surface.
 *
 * Run before and after deployment:
 *   npm run smoke -- --base-url=https://satpass.vercel.app
 */

const baseUrlArgument = process.argv.find((argument) => argument.startsWith("--base-url="));
const BASE_URL = (baseUrlArgument?.split("=", 2)[1] ?? "http://localhost:3002").replace(/\/$/, "");
const REQUEST_TIMEOUT_MS = 90_000;

/** Fetch JSON and fail with the response body when an endpoint is not healthy. */
async function requestJson(path, init = undefined) {
  const response = await fetch(`${BASE_URL}${path}`, {
    ...init,
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
  const text = await response.text();

  if (!response.ok) {
    throw new Error(`${path} returned ${response.status}: ${text.slice(0, 500)}`);
  }

  try {
    return JSON.parse(text);
  } catch {
    throw new Error(`${path} did not return JSON`);
  }
}

/** POST a JSON document to one science endpoint. */
async function postJson(path, body) {
  return requestJson(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** Throw when a response does not carry a required launch invariant. */
function assert(condition, message) {
  if (!condition) throw new Error(message);
}

/** Run one named check and print a compact, scan-friendly result. */
async function check(name, operation) {
  const started = performance.now();
  await operation();
  const elapsed = Math.round(performance.now() - started);
  console.log(`PASS ${name} (${elapsed} ms)`);
}

/** Exercise the routes used by the landing page without spending model quota. */
async function main() {
  const station = {
    norad_id: 25544,
    latitude_deg: 12.9716,
    longitude_deg: 77.5946,
    elevation_m: 920,
    station_name: "Bangalore launch smoke",
  };

  await check("homepage", async () => {
    const response = await fetch(`${BASE_URL}/`, { signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS) });
    const html = await response.text();
    assert(response.ok, `homepage returned ${response.status}`);
    assert(html.includes("Ask Atlas"), "homepage is missing the agent interface");
    assert(html.includes("Solar system"), "homepage is missing the solar-system interface");
  });

  await check("science health", async () => {
    const body = await requestJson("/api/health");
    assert(body.status === "ok", `science health is ${body.status}`);
    assert(body.checks?.time_scales?.within_tolerance === true, "UTC/TT check failed");
  });

  await check("satellite lookup", async () => {
    const body = await requestJson("/api/satellite/25544");
    assert(body.norad_id === 25544, "ISS lookup returned the wrong catalog number");
    assert(body.epoch?.receipt?.dataset?.source, "ISS lookup is missing dataset provenance");
  });

  await check("pass prediction", async () => {
    const body = await postJson("/api/passes", {
      ...station,
      min_elevation_deg: 10,
      days: 0.25,
    });
    assert(Array.isArray(body.passes), "pass prediction is missing its pass list");
    assert(body.receipt?.frame?.startsWith("ITRF"), "pass prediction is missing its ITRF receipt");
  });

  await check("ground track", async () => {
    const body = await postJson("/api/groundtrack", {
      norad_id: 25544,
      minutes: 20,
      step_seconds: 60,
    });
    assert(Array.isArray(body.samples) && body.samples.length > 10, "ground track is empty");
  });

  await check("orbital elements", async () => {
    const body = await postJson("/api/elements", { norad_id: 25544, at_epoch: true });
    assert(
      body.classical_elements?.inclination?.unit === "deg",
      "inclination does not carry degree units",
    );
    assert(
      body.derived?.orbital_period?.unit === "s",
      "orbital period does not carry second units",
    );
  });

  await check("eclipse prediction", async () => {
    const body = await postJson("/api/eclipse", { norad_id: 25544, days: 0.25 });
    assert(Array.isArray(body.intervals), "eclipse prediction is missing its interval list");
  });

  await check("solar-system datasets", async () => {
    const [snapshot, orbits, stars, moons, asteroids] = await Promise.all([
      postJson("/api/ephemeris/snapshot", {}),
      postJson("/api/ephemeris/orbits", {}),
      requestJson("/api/stars"),
      postJson("/api/moons", {}),
      postJson("/api/asteroids", {}),
    ]);
    assert(Array.isArray(snapshot.bodies) && snapshot.bodies.length >= 9, "snapshot is incomplete");
    assert(Object.keys(orbits.orbits ?? {}).length >= 8, "orbit set is incomplete");
    assert(Array.isArray(stars.stars) && stars.stars.length > 8_000, "star catalogue is incomplete");
    assert(Array.isArray(moons.moons) && moons.moons.length >= 20, "moon catalogue is incomplete");
    assert(
      Array.isArray(asteroids.asteroids) && asteroids.asteroids.length >= 700,
      "asteroid catalogue is incomplete",
    );
  });

  console.log(`\nLaunch smoke passed against ${BASE_URL}`);
}

main().catch((error) => {
  console.error(`FAIL ${error instanceof Error ? error.message : String(error)}`);
  process.exitCode = 1;
});

"use client";

import { useCallback, useEffect, useState } from "react";
import { AskPanel } from "@/components/AskPanel";
import { DecayPanel } from "@/components/DecayPanel";
import dynamic from "next/dynamic";
import { GroundTrack } from "@/components/GroundTrack";
import { LiteraturePanel } from "@/components/LiteraturePanel";
import { ThemeToggle } from "@/components/ThemeToggle";
import { TierLegend, TieredValue } from "@/components/TieredValue";
import type {
  ApiError,
  EclipseResponse,
  ElementsResponse,
  GroundTrackResponse,
  PassesResponse,
  SatelliteResponse,
  SatellitePass,
  Value,
} from "@/lib/types";

/** Cesium is client-only: it touches window and would bloat the server bundle. */
const Globe = dynamic(() => import("@/components/Globe").then((m) => m.Globe), {
  ssr: false,
  loading: () => (
    <div className="flex h-[420px] w-full items-center justify-center rounded-lg border border-edge bg-surface text-xs text-muted">
      loading globe…
    </div>
  ),
});

/** A few well-known objects, so the tool is usable without looking up catalog numbers. */
const SATELLITE_PRESETS = [
  { norad: 25544, label: "ISS (ZARYA)" },
  { norad: 20580, label: "Hubble Space Telescope" },
  { norad: 48274, label: "Tiangong" },
  { norad: 33591, label: "NOAA 19" },
  { norad: 25338, label: "NOAA 15" },
];

/** Ground-station presets covering a spread of latitudes. */
const STATION_PRESETS = [
  { name: "Bangalore", lat: 12.9716, lon: 77.5946, elevation: 920 },
  { name: "London", lat: 51.5072, lon: -0.1276, elevation: 11 },
  { name: "Svalbard", lat: 78.2297, lon: 15.4076, elevation: 450 },
  { name: "Quito", lat: -0.1807, lon: -78.4678, elevation: 2850 },
  { name: "Sydney", lat: -33.8688, lon: 151.2093, elevation: 58 },
];

/**
 * Format an ISO timestamp for display, keeping UTC explicit.
 *
 * Local-time rendering would be a silent source of confusion here: every number the service
 * returns is UTC, and mission work is done in UTC.
 */
function formatUtc(iso: string): string {
  return new Date(iso).toISOString().replace("T", " ").slice(0, 19) + "Z";
}

/** Format a duration in seconds as minutes and seconds. */
function formatDuration(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${Math.round(seconds - minutes * 60)}s`;
}

/** Small labelled input. */
function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-[11px] uppercase tracking-wide text-muted">{label}</span>
      {children}
      {hint && <span className="text-[11px] text-faint">{hint}</span>}
    </label>
  );
}

const inputClass =
  "rounded-md border border-edge bg-surface px-2.5 py-1.5 font-mono text-sm " +
  "text-foreground outline-none focus:border-accent focus:bg-surface-inset";

/**
 * Visual timeline of passes across the search window.
 *
 * This is the artifact an operator would actually screenshot: it answers "when can I talk to my
 * satellite, and where are the gaps" at a glance, which a table of timestamps does not.
 */
function PassTimeline({
  passes,
  fromUtc,
  days,
}: {
  passes: SatellitePass[];
  fromUtc: string;
  days: number;
}) {
  const start = new Date(fromUtc).getTime();
  const span = days * 86400 * 1000;

  return (
    <div className="mt-1">
      <div className="relative h-9 overflow-hidden rounded-md border border-edge bg-surface">
        {passes.map((item, index) => {
          const left = ((new Date(item.rise_utc).getTime() - start) / span) * 100;
          const width = Math.max(((item.duration_s * 1000) / span) * 100, 0.35);
          // Higher passes are better passes; opacity encodes elevation so the strongest
          // opportunities are visible without reading the table.
          const strength = Math.min(item.max_elevation_deg / 90, 1);

          return (
            <div
              key={index}
              className="absolute top-0 h-full bg-track"
              style={{
                left: `${left}%`,
                width: `${width}%`,
                opacity: 0.35 + strength * 0.65,
              }}
              title={`${formatUtc(item.rise_utc)} — ${item.max_elevation_deg.toFixed(1)}° max`}
            />
          );
        })}
      </div>
      <div className="mt-1 flex justify-between text-[11px] text-faint">
        <span>{formatUtc(fromUtc)}</span>
        <span>+{days} day{days === 1 ? "" : "s"}</span>
      </div>
    </div>
  );
}

/**
 * Pass-prediction interface.
 *
 * Answers one question end to end: when can a given ground station work a given satellite, with
 * every number carrying its provenance.
 */
export default function Home() {
  const [noradId, setNoradId] = useState(25544);
  const [station, setStation] = useState(STATION_PRESETS[0]);
  const [minElevation, setMinElevation] = useState(10);
  const [days, setDays] = useState(2);
  const [trackView, setTrackView] = useState<"3D" | "2D">("3D");

  const [satellite, setSatellite] = useState<SatelliteResponse | null>(null);
  const [passes, setPasses] = useState<PassesResponse | null>(null);
  const [track, setTrack] = useState<GroundTrackResponse | null>(null);
  const [orbitElements, setOrbitElements] = useState<ElementsResponse | null>(null);
  const [eclipse, setEclipse] = useState<EclipseResponse | null>(null);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [health, setHealth] = useState<"ok" | "degraded" | "unreachable" | "checking">("checking");

  useEffect(() => {
    fetch("/api/health")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((body) => setHealth(body.status))
      .catch(() => setHealth("unreachable"));
  }, []);

  const run = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const [
        satelliteResponse,
        passesResponse,
        trackResponse,
        elementsResponse,
        eclipseResponse,
      ] = await Promise.all([
        fetch(`/api/satellite/${noradId}`),
        fetch("/api/passes", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            norad_id: noradId,
            latitude_deg: station.lat,
            longitude_deg: station.lon,
            elevation_m: station.elevation,
            station_name: station.name,
            min_elevation_deg: minElevation,
            days,
          }),
        }),
        fetch("/api/groundtrack", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ norad_id: noradId, minutes: 100, step_seconds: 30 }),
        }),
        fetch("/api/elements", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ norad_id: noradId, at_epoch: true }),
        }),
        fetch("/api/eclipse", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ norad_id: noradId, days: 1 }),
        }),
      ]);

      for (const response of [
        satelliteResponse,
        passesResponse,
        trackResponse,
        elementsResponse,
        eclipseResponse,
      ]) {
        if (!response.ok) {
          const body = (await response.json().catch(() => null)) as ApiError | null;
          throw new Error(body?.error?.message ?? `HTTP ${response.status}`);
        }
      }

      setSatellite(await satelliteResponse.json());
      setPasses(await passesResponse.json());
      setTrack(await trackResponse.json());
      setOrbitElements(await elementsResponse.json());
      setEclipse(await eclipseResponse.json());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
      setSatellite(null);
      setPasses(null);
      setTrack(null);
      setOrbitElements(null);
      setEclipse(null);
    } finally {
      setLoading(false);
    }
  }, [noradId, station, minElevation, days]);

  return (
    <div className="min-h-full bg-background px-6 py-10 text-foreground">
      <main className="mx-auto w-full max-w-5xl">
        <header className="flex flex-wrap items-baseline justify-between gap-3">
          <div>
            <h1 className="text-2xl font-medium tracking-tight text-foreground">Helios</h1>
            <p className="mt-1 max-w-xl text-sm leading-6 text-muted">
              Ground-station pass prediction, computed by real orbital mechanics — every value
              carrying its frame, time scale, uncertainty, and source.
            </p>
          </div>
          <div className="flex items-center gap-4">
            <span className="inline-flex items-center gap-2 text-xs text-muted">
              <span
                className={`h-2 w-2 rounded-full ${
                  health === "ok"
                    ? "bg-ok"
                    : health === "checking"
                      ? "bg-faint"
                      : "bg-danger"
                }`}
              />
              science service {health}
            </span>
            <ThemeToggle />
          </div>
        </header>

        <AskPanel />

        {/* Controls */}
        <section className="mt-6 rounded-lg border border-edge bg-surface p-5">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Field label="Satellite">
              <select
                className={inputClass}
                value={noradId}
                onChange={(e) => setNoradId(Number(e.target.value))}
              >
                {SATELLITE_PRESETS.map((preset) => (
                  <option key={preset.norad} value={preset.norad} className="bg-surface">
                    {preset.label}
                  </option>
                ))}
              </select>
            </Field>

            <Field label="NORAD ID" hint="Any catalog number">
              <input
                type="number"
                className={inputClass}
                value={noradId}
                min={1}
                onChange={(e) => setNoradId(Number(e.target.value))}
              />
            </Field>

            <Field label="Ground station">
              <select
                className={inputClass}
                value={station.name}
                onChange={(e) =>
                  setStation(
                    STATION_PRESETS.find((s) => s.name === e.target.value) ?? STATION_PRESETS[0],
                  )
                }
              >
                {STATION_PRESETS.map((preset) => (
                  <option key={preset.name} value={preset.name} className="bg-surface">
                    {preset.name}
                  </option>
                ))}
              </select>
            </Field>

            <Field label="Latitude / Longitude" hint="degrees north / east">
              <div className="flex gap-2">
                <input
                  type="number"
                  step="0.0001"
                  className={`${inputClass} w-full`}
                  value={station.lat}
                  onChange={(e) => setStation({ ...station, lat: Number(e.target.value) })}
                />
                <input
                  type="number"
                  step="0.0001"
                  className={`${inputClass} w-full`}
                  value={station.lon}
                  onChange={(e) => setStation({ ...station, lon: Number(e.target.value) })}
                />
              </div>
            </Field>

            <Field label="Elevation mask" hint="degrees above horizon">
              <input
                type="number"
                className={inputClass}
                value={minElevation}
                min={0}
                max={89}
                onChange={(e) => setMinElevation(Number(e.target.value))}
              />
            </Field>

            <Field label="Search window" hint="days ahead">
              <input
                type="number"
                className={inputClass}
                value={days}
                min={1}
                max={10}
                onChange={(e) => setDays(Number(e.target.value))}
              />
            </Field>

            <div className="flex items-end sm:col-span-2">
              <button
                type="button"
                onClick={run}
                disabled={loading}
                className="w-full rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-contrast transition hover:opacity-90 disabled:opacity-50"
              >
                {loading ? "Computing…" : "Predict passes"}
              </button>
            </div>
          </div>
        </section>

        {error && (
          <p className="mt-4 rounded-md border border-danger bg-surface px-4 py-3 font-mono text-sm text-danger">
            {error}
          </p>
        )}

        {/* Satellite identity */}
        {satellite && (
          <section className="mt-6 rounded-lg border border-edge bg-surface p-5">
            <div className="flex flex-wrap items-baseline justify-between gap-3">
              <h2 className="text-sm font-medium text-foreground">
                {satellite.name}{" "}
                <span className="font-mono text-xs text-muted">#{satellite.norad_id}</span>
              </h2>
              <TierLegend />
            </div>

            <div className="mt-4 grid gap-5 sm:grid-cols-2">
              <div>
                <p className="mb-1 text-[11px] uppercase tracking-wide text-muted">
                  Element set epoch
                </p>
                <TieredValue
                  value={satellite.epoch}
                  format={(raw) => formatUtc(String(raw))}
                />
              </div>
              <div>
                <p className="mb-1 text-[11px] uppercase tracking-wide text-muted">
                  Element set age
                </p>
                <TieredValue value={satellite.element_set_age} />
              </div>
            </div>

            <pre className="mt-4 overflow-x-auto rounded-md border border-edge bg-surface-inset p-3 font-mono text-[11px] leading-relaxed text-muted">
              {satellite.tle.line1}
              {"\n"}
              {satellite.tle.line2}
            </pre>
          </section>
        )}

        {/* Passes */}
        {passes && (
          <section className="mt-6 rounded-lg border border-edge bg-surface p-5">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="text-sm font-medium text-foreground">
                {passes.count} pass{passes.count === 1 ? "" : "es"} over {station.name}
              </h2>
              <span className="text-[11px] text-muted">
                mask {passes.min_elevation_deg}° · next {passes.searched_days} day
                {passes.searched_days === 1 ? "" : "s"}
              </span>
            </div>

            <div className="mt-4">
              <PassTimeline
                passes={passes.passes}
                fromUtc={passes.searched_from_utc}
                days={passes.searched_days}
              />
            </div>

            {passes.count === 0 ? (
              <p className="mt-4 text-sm text-muted">
                No passes clear a {passes.min_elevation_deg}° mask from this site in the search
                window. Lower the mask or extend the window — or the orbit may simply never reach
                this latitude.
              </p>
            ) : (
              <div className="mt-4 overflow-x-auto">
                <table className="w-full min-w-[36rem] text-left text-sm">
                  <thead>
                    <tr className="border-b border-edge text-[11px] uppercase tracking-wide text-muted">
                      <th className="py-2 pr-4 font-normal">Rise (UTC)</th>
                      <th className="py-2 pr-4 font-normal">Culmination</th>
                      <th className="py-2 pr-4 font-normal">Set</th>
                      <th className="py-2 pr-4 font-normal">Max elev.</th>
                      <th className="py-2 font-normal">Duration</th>
                    </tr>
                  </thead>
                  <tbody className="font-mono text-xs text-foreground">
                    {passes.passes.map((item, index) => (
                      <tr key={index} className="border-b border-edge last:border-0">
                        <td className="py-2 pr-4">{formatUtc(item.rise_utc)}</td>
                        <td className="py-2 pr-4 text-muted">
                          {formatUtc(item.culmination_utc).slice(11)}
                        </td>
                        <td className="py-2 pr-4 text-muted">
                          {formatUtc(item.set_utc).slice(11)}
                        </td>
                        <td className="py-2 pr-4 text-predicted">
                          {item.max_elevation_deg.toFixed(1)}°
                        </td>
                        <td className="py-2">{formatDuration(item.duration_s)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <details className="mt-5 group">
              <summary className="cursor-pointer text-xs text-muted hover:text-foreground">
                How these were computed — assumptions, frame, uncertainty
              </summary>
              <div className="mt-2">
                <TieredValue
                  value={{
                    value: `${passes.count} predicted passes`,
                    unit: "none",
                    tier: passes.tier,
                    receipt: passes.receipt,
                  }}
                />
              </div>
            </details>
          </section>
        )}

        {/* Ground track */}
        {track && (
          <section className="mt-6 rounded-lg border border-edge bg-surface p-5">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="text-sm font-medium text-foreground">
                Ground track — next {track.minutes} minutes
              </h2>
              <div className="flex items-center gap-3">
                <span className="text-[11px] text-muted">
                  from {formatUtc(track.start_utc)}
                </span>
                <div
                  className="inline-flex items-center gap-0.5 rounded-md border border-edge p-0.5"
                  role="group"
                  aria-label="Ground track view"
                >
                  {(["3D", "2D"] as const).map((mode) => (
                    <button
                      key={mode}
                      type="button"
                      onClick={() => setTrackView(mode)}
                      aria-pressed={trackView === mode}
                      className={`rounded px-2 py-1 text-[11px] transition-colors ${
                        trackView === mode
                          ? "bg-accent text-accent-contrast"
                          : "text-faint hover:text-foreground"
                      }`}
                    >
                      {mode}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            <div className="mt-4">
              {/*
                Both views earn their place. The 3D globe shows the orbit at true altitude and
                the access geometry to the station; the 2D projection is what an operator
                actually reads, because a whole revolution is visible at once without rotating
                anything.
              */}
              {trackView === "3D" ? (
                <Globe
                  samples={track.samples}
                  station={{ lat: station.lat, lon: station.lon, name: station.name }}
                  passes={passes?.passes}
                />
              ) : (
                <GroundTrack
                  samples={track.samples}
                  station={{ lat: station.lat, lon: station.lon, name: station.name }}
                  current={{
                    lat: Number(track.current.latitude.value),
                    lon: Number(track.current.longitude.value),
                  }}
                />
              )}
            </div>

            <div className="mt-4 grid gap-5 sm:grid-cols-3">
              <div>
                <p className="mb-1 text-[11px] uppercase tracking-wide text-muted">Latitude</p>
                <TieredValue value={track.current.latitude} />
              </div>
              <div>
                <p className="mb-1 text-[11px] uppercase tracking-wide text-muted">Longitude</p>
                <TieredValue value={track.current.longitude} />
              </div>
              <div>
                <p className="mb-1 text-[11px] uppercase tracking-wide text-muted">Altitude</p>
                <TieredValue value={track.current.altitude} />
              </div>
            </div>
          </section>
        )}

        {/* Orbital elements */}
        {orbitElements && (
          <section className="mt-6 rounded-lg border border-edge bg-surface p-5">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="text-sm font-medium text-foreground">Orbit</h2>
              <span className="text-[11px] text-muted">
                at element-set epoch · osculating
              </span>
            </div>

            <div className="mt-4 grid gap-x-6 gap-y-4 sm:grid-cols-2 lg:grid-cols-4">
              {[
                ["Semi-major axis", orbitElements.classical_elements.semi_major_axis],
                ["Eccentricity", orbitElements.classical_elements.eccentricity],
                ["Inclination", orbitElements.classical_elements.inclination],
                ["RAAN", orbitElements.classical_elements.raan],
                ["Period", orbitElements.derived.orbital_period],
                ["Mean motion", orbitElements.derived.mean_motion],
                ["Perigee altitude", orbitElements.derived.perigee_altitude],
                ["Apogee altitude", orbitElements.derived.apogee_altitude],
              ].map(([label, value]) => (
                <div key={label as string}>
                  <p className="mb-1 text-[11px] uppercase tracking-wide text-muted">
                    {label as string}
                  </p>
                  <TieredValue value={value as Value} />
                </div>
              ))}
            </div>

            <p className="mt-4 text-[11px] leading-5 text-faint">
              {orbitElements.element_convention}
            </p>
          </section>
        )}

        {/* Eclipse and power */}
        {eclipse && (
          <section className="mt-6 rounded-lg border border-edge bg-surface p-5">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="text-sm font-medium text-foreground">Eclipse &amp; power</h2>
              <span className="text-[11px] text-muted">next {eclipse.days} day</span>
            </div>

            <div className="mt-4 grid gap-x-6 gap-y-4 sm:grid-cols-2 lg:grid-cols-4">
              <div>
                <p className="mb-1 text-[11px] uppercase tracking-wide text-muted">
                  Beta angle
                </p>
                <TieredValue value={eclipse.beta_angle} />
              </div>
              <div>
                <p className="mb-1 text-[11px] uppercase tracking-wide text-muted">
                  Eclipses
                </p>
                <TieredValue value={eclipse.summary.eclipse_count} />
              </div>
              <div>
                <p className="mb-1 text-[11px] uppercase tracking-wide text-muted">
                  Longest eclipse
                </p>
                <TieredValue
                  value={eclipse.summary.max_eclipse_duration}
                  format={(raw) => `${(Number(raw) / 60).toFixed(1)} min`}
                />
              </div>
              <div>
                <p className="mb-1 text-[11px] uppercase tracking-wide text-muted">
                  Max orbit in shadow
                </p>
                <TieredValue
                  value={eclipse.summary.max_orbit_fraction}
                  format={(raw) => `${(Number(raw) * 100).toFixed(1)} %`}
                />
              </div>
            </div>

            {/* Sunlight/shadow bar for the first orbit: the shape of the duty cycle a power
                system has to survive, which a number alone does not convey. */}
            {eclipse.intervals.length > 0 && (
              <div className="mt-5">
                <div className="flex h-6 overflow-hidden rounded-md border border-edge">
                  <div
                    className="bg-sunlit"
                    style={{
                      width: `${(1 - eclipse.intervals[0].orbit_fraction) * 100}%`,
                    }}
                    title="sunlit"
                  />
                  <div
                    className="bg-shadow"
                    style={{ width: `${eclipse.intervals[0].orbit_fraction * 100}%` }}
                    title="eclipse"
                  />
                </div>
                <div className="mt-1 flex justify-between text-[11px] text-faint">
                  <span>sunlit</span>
                  <span>
                    eclipse — {(eclipse.intervals[0].duration_s / 60).toFixed(1)} min, of which{" "}
                    {(eclipse.intervals[0].umbra_duration_s / 60).toFixed(1)} min umbra
                  </span>
                </div>
              </div>
            )}

            <p className="mt-4 text-[11px] leading-5 text-faint">
              Beta angle is the angle between the orbit plane and the Sun. It sets how much of
              each orbit is spent in shadow, and so drives battery sizing and thermal design.
              Umbra is full shadow; the remainder is penumbra, where the Sun is partly occulted.
            </p>
          </section>
        )}

        {/* Independent of the satellite query above: this asks about an orbit, not an object,
            which is the form the question takes during mission design. */}
        <DecayPanel />

        {/* Also independent of the satellite query: this asks about the published record,
            not about an object in orbit. */}
        <LiteraturePanel />

        <footer className="mt-8 border-t border-edge pt-5 text-[11px] leading-5 text-faint">
          {passes?.notice ??
            "Research and educational use only. Do not use for mission operations, collision " +
              "avoidance, or launch decisions. Verify independently before acting."}
          <br />
          Orbital data from Celestrak. Propagation verified against Orekit.
        </footer>
      </main>
    </div>
  );
}

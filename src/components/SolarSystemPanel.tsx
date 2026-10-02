"use client";

import dynamic from "next/dynamic";
import { useEffect, useRef, useState } from "react";
import type { DistanceMode } from "@/components/SolarSystem";
import type {
  ApiError,
  AsteroidsResponse,
  MoonsResponse,
  OrbitsResponse,
  SnapshotBody,
  SnapshotResponse,
  StarsResponse,
} from "@/lib/types";

/** three.js touches WebGL and window, so it must not run on the server. */
const SolarSystem = dynamic(
  () => import("@/components/SolarSystem").then((module) => module.SolarSystem),
  {
    ssr: false,
    loading: () => (
      <div className="flex h-[520px] w-full items-center justify-center rounded-lg bg-[#03050b] text-xs text-muted">
        loading solar system…
      </div>
    ),
  },
);

/** Days either side of today the time control spans. */
const RANGE_DAYS = 365 * 6;

/** Astronomical unit in kilometres, used only to display a delta between two API positions. */
const AU_KM = 149_597_870.7;

type EphemerisModel = "precision" | "analytic";

/** Bodies offered as camera targets, in orbital order. */
const FOCUS_TARGETS = [
  "sun",
  "mercury",
  "venus",
  "earth",
  "mars",
  "jupiter",
  "saturn",
  "uranus",
  "neptune",
] as const;

/** Format a day offset from now as a UTC date. */
function dateFromOffset(days: number): Date {
  return new Date(Date.now() + days * 86_400_000);
}

/** Find one body in a rendering snapshot without weakening the response type. */
function bodyFrom(snapshot: SnapshotResponse | undefined, name: string): SnapshotBody | undefined {
  return snapshot?.bodies.find((body) => body.body === name);
}

/** Calculate live heliocentric separation between the two model outputs, in kilometres. */
function modelSeparationKm(precision: SnapshotBody, analytic: SnapshotBody): number {
  return (
    Math.hypot(
      precision.ecliptic_x_au - analytic.ecliptic_x_au,
      precision.ecliptic_y_au - analytic.ecliptic_y_au,
      precision.ecliptic_z_au - analytic.ecliptic_z_au,
    ) * AU_KM
  );
}

/**
 * Solar-system view: the second renderer, and a lesson about scale.
 *
 * Every solar-system diagram ever printed lies about size, because a picture with true relative
 * radii has nothing visible in it. Most lie silently. This one states which compromise is active
 * and lets a reader switch to the honest version and see for themselves why it is never used.
 */
export function SolarSystemPanel() {
  const [offsetDays, setOffsetDays] = useState(0);
  const [ephemerisModel, setEphemerisModel] = useState<EphemerisModel>("precision");
  const [distanceMode, setDistanceMode] = useState<DistanceMode>("log");
  const [focus, setFocus] = useState<string | null>(null);
  const [resetViewKey, setResetViewKey] = useState(0);

  const [snapshots, setSnapshots] = useState<Partial<Record<EphemerisModel, SnapshotResponse>>>({});
  const [orbits, setOrbits] = useState<OrbitsResponse | null>(null);
  const [starCatalogue, setStarCatalogue] = useState<StarsResponse | null>(null);
  const [moons, setMoons] = useState<MoonsResponse | null>(null);
  const [asteroids, setAsteroids] = useState<AsteroidsResponse | null>(null);
  const [showAsteroids, setShowAsteroids] = useState(true);
  const [fullscreen, setFullscreen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  /** Return to the elevated whole-system camera and clear any highlighted body. */
  const resetSystemView = (): void => {
    setFocus(null);
    setResetViewKey((current) => current + 1);
  };

  // Orbit paths are fetched once. They are the same curves whatever date is shown, and tracing
  // Neptune's costs 180 ephemeris evaluations.
  useEffect(() => {
    const controller = new AbortController();

    fetch("/api/ephemeris/orbits", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ samples: 220 }),
      signal: controller.signal,
    })
      .then((response) => (response.ok ? (response.json() as Promise<OrbitsResponse>) : null))
      .then((body) => body && setOrbits(body))
      .catch(() => {
        // A missing orbit path degrades the picture; it does not break it. The bodies are still
        // placed correctly, so this failure is deliberately not surfaced as an error.
      });

    return () => controller.abort();
  }, []);

  // The star catalogue is fetched once. It is eight thousand records and none of them move on
  // any timescale this view cares about.
  useEffect(() => {
    const controller = new AbortController();

    fetch("/api/stars", { signal: controller.signal })
      .then((response) => (response.ok ? (response.json() as Promise<StarsResponse>) : null))
      .then((body) => body && setStarCatalogue(body))
      .catch(() => {
        // A missing star catalogue leaves an empty sky, which is a degraded picture rather than
        // a broken one. The planets are unaffected, so this is not surfaced as an error.
      });

    return () => controller.abort();
  }, []);

  // Moons and asteroids track the date, so they are refetched alongside the planet snapshot.
  useEffect(() => {
    const controller = new AbortController();
    const at = dateFromOffset(offsetDays).toISOString();

    const post = <T,>(path: string, body: unknown) =>
      fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: controller.signal,
      }).then((response) => (response.ok ? (response.json() as Promise<T>) : null));

    const load = setTimeout(() => {
      void post<MoonsResponse>("/api/moons", { at_utc: at })
        .then((body) => body && setMoons(body))
        .catch(() => undefined);

      void post<AsteroidsResponse>("/api/asteroids", { at_utc: at, limit: 760 })
        .then((body) => body && setAsteroids(body))
        .catch(() => undefined);
    }, 180);

    return () => {
      controller.abort();
      clearTimeout(load);
    };
  }, [offsetDays]);

  // Escape leaves fullscreen, which is what every viewer will try first.
  useEffect(() => {
    if (!fullscreen) return;

    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setFullscreen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fullscreen]);

  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    const controller = new AbortController();

    timer.current = setTimeout(() => {
      const atUtc = dateFromOffset(offsetDays).toISOString();

      /** Fetch one explicitly selected model so the comparison never confuses requested/effective. */
      const fetchSnapshot = async (model: EphemerisModel): Promise<SnapshotResponse> => {
        const response = await fetch("/api/ephemeris/snapshot", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ at_utc: atUtc, model }),
          signal: controller.signal,
        });
        if (!response.ok) {
          const parsed = (await response.json().catch(() => null)) as ApiError | null;
          throw new Error(parsed?.error?.message ?? `HTTP ${response.status}`);
        }
        return response.json() as Promise<SnapshotResponse>;
      };

      Promise.all([fetchSnapshot("precision"), fetchSnapshot("analytic")])
        .then(([precision, analytic]) => {
          setSnapshots({ precision, analytic });
          setError(null);
        })
        .catch((caught: unknown) => {
          if (caught instanceof DOMException && caught.name === "AbortError") return;
          setError(caught instanceof Error ? caught.message : String(caught));
        });
    }, 120);

    return () => {
      controller.abort();
      if (timer.current) clearTimeout(timer.current);
    };
  }, [offsetDays]);

  const shown = dateFromOffset(offsetDays);

  const snapshot = snapshots[ephemerisModel] ?? null;

  const focused = focus ? snapshot?.bodies.find((item) => item.body === focus) : undefined;
  const comparisonBodyName = focus ?? "uranus";
  const precisionAvailable = snapshots.precision?.model === "precision";
  const precisionBody = bodyFrom(snapshots.precision, comparisonBodyName);
  const analyticBody = bodyFrom(snapshots.analytic, comparisonBodyName);
  const liveModelSeparation =
    precisionBody && analyticBody ? modelSeparationKm(precisionBody, analyticBody) : null;
  const boundImprovement =
    precisionAvailable && precisionBody && analyticBody && precisionBody.max_error_km > 0
      ? analyticBody.max_error_km / precisionBody.max_error_km
      : null;

  return (
    <section
      className={
        fullscreen
          ? "fixed inset-0 z-50 overflow-auto bg-background p-5"
          : "scroll-mt-16 mt-8 rounded-xl border border-edge bg-surface p-5 sm:p-6"
      }
      id="solar-system"
    >
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-medium tracking-tight text-foreground">Solar system</h2>
        <div className="flex items-center gap-2">
          {snapshot && (
            <span className="text-[10px] font-medium text-derived">
              {snapshot.model === "precision" ? "JPL DE421 precision" : "analytic fallback"}
            </span>
          )}
          <span className="text-[11px] text-faint">
            positions, orbits, tilts and illumination are real
          </span>
        </div>
      </header>

      <p className="mt-2 max-w-2xl text-xs leading-6 text-muted">
        Where the planets actually are, on their real traced orbits, lit by the Sun from its real
        direction. Drag to orbit, scroll to zoom, pick a body to fly to it, and move the date to
        watch the system run.
      </p>

      <div className="mt-5 grid gap-3 lg:grid-cols-[1fr_1fr_0.9fr]">
        <button
          type="button"
          onClick={() => setEphemerisModel("precision")}
          className={`rounded-lg border p-4 text-left ${
            ephemerisModel === "precision"
              ? "border-derived/60 bg-derived/5"
              : "border-edge bg-background/40 hover:border-derived/40"
          }`}
        >
          <span className="flex items-center justify-between gap-3">
            <span className="text-xs font-semibold text-foreground">JPL DE421 precision</span>
            <span className="rounded-full bg-derived/10 px-2 py-0.5 text-[9px] uppercase tracking-wide text-derived">
              {snapshots.precision && !precisionAvailable ? "fallback active" : "default"}
            </span>
          </span>
          <span className="mt-2 block text-[11px] leading-5 text-muted">
            Think high-fidelity trajectory file: numerically integrated JPL positions, packaged
            and checksum-verified for its fixed 1899–2053 coverage window.
          </span>
        </button>

        <button
          type="button"
          onClick={() => setEphemerisModel("analytic")}
          className={`rounded-lg border p-4 text-left ${
            ephemerisModel === "analytic"
              ? "border-predicted/60 bg-predicted/5"
              : "border-edge bg-background/40 hover:border-predicted/40"
          }`}
        >
          <span className="flex items-center justify-between gap-3">
            <span className="text-xs font-semibold text-foreground">Analytic fallback</span>
            <span className="rounded-full bg-predicted/10 px-2 py-0.5 text-[9px] uppercase tracking-wide text-predicted">resilience</span>
          </span>
          <span className="mt-2 block text-[11px] leading-5 text-muted">
            Think compact equation set: ERFA analytical series with no kernel dependency,
            available beyond the finite JPL window but with weaker measured bounds.
          </span>
        </button>

        <div className="rounded-lg border border-edge bg-surface-inset/70 p-4">
          <div className="flex items-center justify-between gap-3">
            <span className="text-[10px] uppercase tracking-[0.15em] text-faint">Precision lab</span>
            <span className="text-[10px] capitalize text-foreground">{comparisonBodyName}</span>
          </div>
          {precisionAvailable &&
          precisionBody &&
          analyticBody &&
          liveModelSeparation !== null &&
          boundImprovement ? (
            <>
              <p className="mt-2 font-mono text-xl text-foreground">
                {boundImprovement.toFixed(0)}×
                <span className="ml-2 font-sans text-[10px] uppercase tracking-wide text-observed">
                  tighter bound
                </span>
              </p>
              <div className="mt-2 grid grid-cols-2 gap-2 text-[10px]">
                <span className="text-derived">
                  JPL measured max {precisionBody.max_error_km.toLocaleString()} km
                </span>
                <span className="text-predicted">
                  analytic measured max {analyticBody.max_error_km.toLocaleString()} km
                </span>
              </div>
              <p className="mt-2 text-[10px] leading-4 text-faint">
                Live model separation:{" "}
                {liveModelSeparation.toLocaleString(undefined, {
                  maximumFractionDigits: liveModelSeparation < 10 ? 2 : 0,
                })}{" "}
                km. This is model spread, not ground-truth error.
              </p>
            </>
          ) : (
            <p className="mt-3 text-[11px] text-muted">
              {snapshots.precision && !precisionAvailable
                ? "The verified JPL asset is unavailable; both views are using the labelled analytic fallback."
                : "Loading both independently measured models…"}
            </p>
          )}
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-end gap-x-6 gap-y-3">
        <label className="flex min-w-[15rem] flex-1 flex-col gap-1.5">
          <span className="flex items-baseline justify-between text-[11px] uppercase tracking-wide text-muted">
            <span>Date</span>
            <span className="font-mono text-sm text-foreground">
              {shown.toISOString().slice(0, 10)}
            </span>
          </span>
          <input
            type="range"
            min={-RANGE_DAYS}
            max={RANGE_DAYS}
            step={1}
            value={offsetDays}
            onChange={(event) => setOffsetDays(Number(event.target.value))}
            className="accent-derived"
            aria-label="Date offset in days from today"
          />
        </label>

        <div className="flex flex-col gap-1.5">
          <span className="text-[11px] uppercase tracking-wide text-muted">Ephemeris</span>
          <div className="flex gap-1.5">
            {(["precision", "analytic"] as const).map((model) => (
              <button
                key={model}
                type="button"
                onClick={() => setEphemerisModel(model)}
                className={`rounded-md border px-2.5 py-1 text-xs ${
                  ephemerisModel === model
                    ? "border-derived bg-surface-inset text-foreground"
                    : "border-edge text-muted hover:border-derived"
                }`}
              >
                {model === "precision" ? "JPL precision" : "analytic fallback"}
              </button>
            ))}
          </div>
        </div>

        <div className="flex flex-col gap-1.5">
          <span className="text-[11px] uppercase tracking-wide text-muted">Distance</span>
          <div className="flex gap-1.5">
            {(["log", "linear"] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                onClick={() => setDistanceMode(mode)}
                className={`rounded-md border px-2.5 py-1 text-xs ${
                  distanceMode === mode
                    ? "border-accent bg-surface-inset text-foreground"
                    : "border-edge text-muted hover:border-accent"
                }`}
              >
                {mode === "log" ? "compressed" : "true scale"}
              </button>
            ))}
          </div>
        </div>

        <div className="flex flex-col gap-1.5">
          <span className="text-[11px] uppercase tracking-wide text-muted">View</span>
          <div className="flex gap-1.5">
            <button
              type="button"
              onClick={resetSystemView}
              className="rounded-md border border-edge px-2.5 py-1 text-xs text-muted hover:border-accent"
            >
              reset camera
            </button>
            <button
              type="button"
              onClick={() => setShowAsteroids((previous) => !previous)}
              className={`rounded-md border px-2.5 py-1 text-xs ${
                showAsteroids
                  ? "border-accent bg-surface-inset text-foreground"
                  : "border-edge text-muted hover:border-accent"
              }`}
            >
              asteroids
            </button>
            <button
              type="button"
              onClick={() => setFullscreen((previous) => !previous)}
              className="rounded-md border border-edge px-2.5 py-1 text-xs text-muted hover:border-accent"
            >
              {fullscreen ? "exit full screen" : "full screen"}
            </button>
            {offsetDays !== 0 && (
              <button
                type="button"
                onClick={() => setOffsetDays(0)}
                className="rounded-md border border-edge px-2.5 py-1 text-xs text-muted hover:border-accent"
              >
                today
              </button>
            )}
          </div>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-[11px] uppercase tracking-wide text-muted">Fly to</span>
        <button
          type="button"
          onClick={resetSystemView}
          className={`rounded-md border px-2 py-1 text-[11px] ${
            focus === null
              ? "border-accent bg-surface-inset text-foreground"
              : "border-edge text-muted hover:border-accent"
          }`}
        >
          system
        </button>
        {FOCUS_TARGETS.map((body) => (
          <button
            key={body}
            type="button"
            onClick={() => setFocus(body)}
            className={`rounded-md border px-2 py-1 text-[11px] capitalize ${
              focus === body
                ? "border-accent bg-surface-inset text-foreground"
                : "border-edge text-muted hover:border-accent"
            }`}
          >
            {body}
          </button>
        ))}
      </div>

      {error && (
        <p className="mt-3 rounded-md border border-speculative/40 bg-surface-inset px-3 py-2 text-xs text-speculative">
          {error}
        </p>
      )}

      <div
        className={`mt-4 overflow-hidden rounded-lg border border-edge ${
          fullscreen ? "h-[calc(100vh-19rem)]" : ""
        }`}
      >
        <SolarSystem
          snapshot={snapshot}
          orbits={orbits}
          starCatalogue={starCatalogue}
          moons={moons}
          asteroids={showAsteroids ? asteroids : null}
          distanceMode={distanceMode}
          focus={focus}
          resetViewKey={resetViewKey}
          onSelect={setFocus}
        />
      </div>

      {focused && (
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-[11px] leading-5 text-muted">
          <p>
            <span className="capitalize text-foreground">{focused.body}</span> ·{" "}
            <span className="font-mono">{focused.distance_from_sun_au.toFixed(4)}</span> AU from
            the Sun · radius{" "}
            <span className="font-mono">{(focused.radius_m / 1000).toLocaleString()}</span> km ·
            independently measured max disagreement{" "}
            <span className="font-mono">{focused.max_error_km.toLocaleString()}</span> km
          </p>
          {focused.body !== "sun" && focused.body !== "moon" && (
            <span className="text-derived">Own trajectory highlighted · body centred on path</span>
          )}
        </div>
      )}

      {/* What the picture is, and what it is not. */}
      <dl className="mt-4 grid gap-3 text-[11px] leading-5 sm:grid-cols-2">
        <div>
          <dt className="uppercase tracking-wide text-muted">Body size</dt>
          <dd className="mt-1 text-faint">
            <span className="text-observed">Always true to scale.</span> Nothing is exaggerated.
            A body whose real angular size falls below a few pixels is drawn as a labelled marker
            instead of a sphere, and becomes a sphere once you are close enough for its true size
            to mean something — so the picture is never legible at the cost of being wrong.
          </dd>
        </div>

        <div>
          <dt className="uppercase tracking-wide text-muted">Distance</dt>
          <dd className="mt-1 text-faint">
            {distanceMode === "log" ? (
              <>
                <span className="text-speculative">Radially compressed</span> (logarithmic), so
                the inner planets are separable while Neptune stays in frame. Direction is exact;
                only the radial distance is remapped.
              </>
            ) : (
              <>
                <span className="text-observed">To scale.</span> Neptune is 30 AU out and the
                inner system is a knot near the centre — which is what the solar system is
                actually like.
              </>
            )}
          </dd>
        </div>

        <div>
          <dt className="uppercase tracking-wide text-muted">What is real</dt>
          <dd className="mt-1 text-faint">
            Positions and orbit paths are tiered <span className="text-derived">derived</span> in
            heliocentric ecliptic coordinates. Every planet is centred on its own coloured
            trajectory; the paths intentionally occupy different planes because real orbital
            inclinations are not zero. Positions default to packaged JPL DE421; full
            outer-planet paths use the measured analytic fallback because DE421 ends before one
            Neptune revolution. The live JPL position is inserted as an exact path vertex, while
            the model difference remains far below one rendered path sample. Axial tilts,
            rotation directions and the illumination direction are real too:{" "}
            <span className="text-foreground">Uranus lies on its side</span> and Venus turns
            backwards because they do.
          </dd>
        </div>

        <div>
          <dt className="uppercase tracking-wide text-muted">Stars, moons and asteroids</dt>
          <dd className="mt-1 text-faint">
            {starCatalogue ? (
              <>
                <span className="text-observed">{starCatalogue.count.toLocaleString()} real
                stars</span> from the Yale Bright Star Catalogue via CDS VizieR — the
                constellations are the actual constellations.{" "}
              </>
            ) : null}
            {moons ? `${moons.count} major moons ` : ""}from JPL Horizons and{" "}
            {asteroids ? `${asteroids.count} ` : ""}catalogued asteroids from JPL&apos;s
            Small-Body Database, propagated from real elements — so the belt&apos;s resonance
            gaps are in the data, not drawn. Both are selections, not complete catalogues.
          </dd>
        </div>

        <div>
          <dt className="uppercase tracking-wide text-muted">Imagery</dt>
          <dd className="mt-1 text-faint">
            Surface maps are <span className="text-observed">real observed imagery</span> — the
            Moon from NASA&apos;s LRO, the planets from Solar System Scope&apos;s NASA-derived
            maps (CC BY 4.0). Cloud cover on Earth is one fixed snapshot, not current weather.
            The starfield is decorative and is <em>not</em> a star catalogue. Rotation is sped up
            to be watchable; only its direction and relative rate are true.
          </dd>
        </div>

      </dl>
    </section>
  );
}

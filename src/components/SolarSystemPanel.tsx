"use client";

import dynamic from "next/dynamic";
import { useEffect, useRef, useState } from "react";
import type { DistanceMode, SizeMode } from "@/components/SolarSystem";
import type { ApiError, OrbitsResponse, SnapshotResponse } from "@/lib/types";

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

/**
 * Solar-system view: the second renderer, and a lesson about scale.
 *
 * Every solar-system diagram ever printed lies about size, because a picture with true relative
 * radii has nothing visible in it. Most lie silently. This one states which compromise is active
 * and lets a reader switch to the honest version and see for themselves why it is never used.
 */
export function SolarSystemPanel() {
  const [offsetDays, setOffsetDays] = useState(0);
  const [sizeMode, setSizeMode] = useState<SizeMode>("legible");
  const [distanceMode, setDistanceMode] = useState<DistanceMode>("log");
  const [focus, setFocus] = useState<string | null>(null);

  const [snapshot, setSnapshot] = useState<SnapshotResponse | null>(null);
  const [orbits, setOrbits] = useState<OrbitsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

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

  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    const controller = new AbortController();

    timer.current = setTimeout(() => {
      fetch("/api/ephemeris/snapshot", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ at_utc: dateFromOffset(offsetDays).toISOString() }),
        signal: controller.signal,
      })
        .then(async (response) => {
          if (!response.ok) {
            const parsed = (await response.json().catch(() => null)) as ApiError | null;
            throw new Error(parsed?.error?.message ?? `HTTP ${response.status}`);
          }
          return response.json() as Promise<SnapshotResponse>;
        })
        .then((body) => {
          setSnapshot(body);
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

  const focused = focus ? snapshot?.bodies.find((item) => item.body === focus) : undefined;

  return (
    <section className="mt-10 rounded-lg border border-edge bg-surface p-5">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-medium tracking-tight text-foreground">Solar system</h2>
        <span className="text-[11px] text-faint">
          positions, orbits, tilts and illumination are real
        </span>
      </header>

      <p className="mt-2 max-w-2xl text-xs leading-6 text-muted">
        Where the planets actually are, on their real traced orbits, lit by the Sun from its real
        direction. Drag to orbit, scroll to zoom, pick a body to fly to it, and move the date to
        watch the system run.
      </p>

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
          <span className="text-[11px] uppercase tracking-wide text-muted">Body size</span>
          <div className="flex gap-1.5">
            {(["legible", "true"] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                onClick={() => setSizeMode(mode)}
                className={`rounded-md border px-2.5 py-1 text-xs ${
                  sizeMode === mode
                    ? "border-accent bg-surface-inset text-foreground"
                    : "border-edge text-muted hover:border-accent"
                }`}
              >
                {mode === "legible" ? "legible" : "true scale"}
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

      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-[11px] uppercase tracking-wide text-muted">Fly to</span>
        <button
          type="button"
          onClick={() => setFocus(null)}
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

      <div className="mt-4 overflow-hidden rounded-lg border border-edge">
        <SolarSystem
          snapshot={snapshot}
          orbits={orbits}
          sizeMode={sizeMode}
          distanceMode={distanceMode}
          focus={focus}
        />
      </div>

      {focused && (
        <p className="mt-2 text-[11px] leading-5 text-muted">
          <span className="capitalize text-foreground">{focused.body}</span> ·{" "}
          <span className="font-mono">{focused.distance_from_sun_au.toFixed(4)}</span> AU from the
          Sun · radius <span className="font-mono">{(focused.radius_m / 1000).toLocaleString()}</span>{" "}
          km · position accurate to{" "}
          <span className="font-mono">{focused.max_error_km.toLocaleString()}</span> km
        </p>
      )}

      {/* What the picture is, and what it is not. */}
      <dl className="mt-4 grid gap-3 text-[11px] leading-5 sm:grid-cols-2">
        <div>
          <dt className="uppercase tracking-wide text-muted">Body size</dt>
          <dd className="mt-1 text-faint">
            {sizeMode === "legible" ? (
              <>
                <span className="text-speculative">Not to scale, and not in true proportion.</span>{" "}
                A compressive scaling is applied so the Sun and Mercury fit one view — the Sun is
                really 16,000 times the Moon&apos;s radius. Do not read size ratios off this.
              </>
            ) : (
              <>
                <span className="text-observed">True proportion.</span> Every radius is to the
                same scale as the distances. If the planets have vanished, that is the honest
                picture — this is why every diagram you have seen distorts them.
              </>
            )}
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
            Positions and orbit paths, tiered <span className="text-derived">derived</span>, in
            heliocentric ecliptic coordinates — each orbit is traced from the same ephemeris that
            places the planet, so a body sits on its own path by construction. Axial tilts,
            rotation directions and the illumination direction are real too:{" "}
            <span className="text-foreground">Uranus lies on its side</span> and Venus turns
            backwards because they do.
          </dd>
        </div>

        <div>
          <dt className="uppercase tracking-wide text-muted">What is an impression</dt>
          <dd className="mt-1 text-faint">
            <span className="text-speculative">Surfaces are procedural, not photographs.</span>{" "}
            Cloud patterns, continents, bands and craters are generated — the right kind of
            feature in the right place, but not a map of anything. The starfield is decorative,
            not a star catalogue. Rotation is sped up to be watchable; only its direction and
            relative rate are true.
          </dd>
        </div>
      </dl>
    </section>
  );
}

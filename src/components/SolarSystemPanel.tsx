"use client";

import dynamic from "next/dynamic";
import { useEffect, useRef, useState } from "react";
import type { DistanceMode, SizeMode } from "@/components/SolarSystem";
import type { ApiError, SnapshotResponse } from "@/lib/types";

/** three.js touches WebGL and window, so it must not run on the server. */
const SolarSystem = dynamic(
  () => import("@/components/SolarSystem").then((module) => module.SolarSystem),
  {
    ssr: false,
    loading: () => (
      <div className="flex h-[440px] w-full items-center justify-center rounded-lg border border-edge bg-surface text-xs text-muted">
        loading solar system…
      </div>
    ),
  },
);

/** Days either side of today the time control spans. */
const RANGE_DAYS = 365 * 4;

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

  const [snapshot, setSnapshot] = useState<SnapshotResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

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
    }, 150);

    return () => {
      controller.abort();
      if (timer.current) clearTimeout(timer.current);
    };
  }, [offsetDays]);

  const shown = dateFromOffset(offsetDays);

  return (
    <section className="mt-10 rounded-lg border border-edge bg-surface p-5">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-medium tracking-tight text-foreground">Solar system</h2>
        <span className="text-[11px] text-faint">positions are real; sizes cannot be</span>
      </header>

      <p className="mt-2 max-w-2xl text-xs leading-6 text-muted">
        Where the planets actually are, computed from the same ephemeris the agent uses. Drag to
        orbit, scroll to zoom, and move the date to watch them run.
      </p>

      <div className="mt-4 flex flex-wrap items-end gap-x-6 gap-y-3">
        <label className="flex min-w-[16rem] flex-1 flex-col gap-1.5">
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
            now
          </button>
        )}
      </div>

      {error && (
        <p className="mt-3 rounded-md border border-speculative/40 bg-surface-inset px-3 py-2 text-xs text-speculative">
          {error}
        </p>
      )}

      <div className="mt-4">
        <SolarSystem snapshot={snapshot} sizeMode={sizeMode} distanceMode={distanceMode} />
      </div>

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
          <dt className="uppercase tracking-wide text-muted">Positions</dt>
          <dd className="mt-1 text-faint">
            Real, tiered <span className="text-derived">derived</span>, in{" "}
            {snapshot?.frame ?? "ICRF"}.{" "}
            {snapshot?.accuracy ?? "Accuracy is stated per body by the service."}
          </dd>
        </div>

        <div>
          <dt className="uppercase tracking-wide text-muted">What is decorative</dt>
          <dd className="mt-1 text-faint">
            The starfield is decoration, not a star catalogue — those are not real stars at real
            positions. The rings mark each body&apos;s <em>current</em> distance, not its orbital
            path: the real orbit is an ellipse, and drawing a circle through one sampled point
            would assert a shape the data does not contain.
          </dd>
        </div>
      </dl>
    </section>
  );
}

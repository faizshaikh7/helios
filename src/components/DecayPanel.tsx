"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { TieredValue } from "@/components/TieredValue";
import type { ApiError, DecayResponse } from "@/lib/types";

/**
 * Spacecraft classes, so the panel is usable without knowing a ballistic coefficient.
 *
 * Drag scales with `Cd * A / m`, and that ratio spans two orders of magnitude across real
 * hardware: a cubesat is nearly all surface, a dense smallsat nearly all mass. Presets make that
 * span visible instead of leaving it as a number nobody would think to change.
 */
const SPACECRAFT_PRESETS = [
  { label: "3U cubesat", mass_kg: 3.3, cross_section_m2: 0.03, note: "high area-to-mass" },
  { label: "12U cubesat", mass_kg: 16, cross_section_m2: 0.09, note: "typical smallsat bus" },
  { label: "Smallsat, 150 kg", mass_kg: 150, cross_section_m2: 1.2, note: "dense, decays slowly" },
  { label: "ISS-class", mass_kg: 450_000, cross_section_m2: 1500, note: "huge, but heavy" },
] as const;

/**
 * Log scale for the bar: lifetimes span days to centuries, which a linear axis cannot show.
 *
 * The maximum sits deliberately *past* the service's 500-year reporting cap, so a capped result
 * has room to render as an open-ended bar running off the end rather than as a zero-width sliver
 * pinned to the edge.
 */
const MIN_YEARS = 0.05;
const MAX_YEARS = 1000;

/** Position a lifetime along the logarithmic axis, as a percentage. */
function axisPosition(years: number): number {
  const clamped = Math.min(Math.max(years, MIN_YEARS), MAX_YEARS);
  const span = Math.log10(MAX_YEARS) - Math.log10(MIN_YEARS);
  return ((Math.log10(clamped) - Math.log10(MIN_YEARS)) / span) * 100;
}

/** Format a lifetime with a sensible unit: days below a year, years above. */
function formatYears(years: number): string {
  if (years >= 100) return `${Math.round(years)} yr`;
  if (years >= 1) return `${years.toFixed(1)} yr`;
  return `${Math.round(years * 365.25)} d`;
}

/**
 * Format a lifetime in bare years, for contexts that print the unit separately.
 *
 * `TieredValue` appends the value's own unit, so returning "6.4 yr" there would render
 * "6.4 yr years".
 */
function formatBareYears(years: number): string {
  return years >= 1 ? years.toFixed(1) : years.toFixed(2);
}

/** Decade ticks, so the logarithmic axis is readable rather than merely present. */
const AXIS_TICKS = [0.1, 1, 10, 100] as const;

/**
 * Whether the service stopped estimating rather than reporting a real lifetime.
 *
 * Above the atmosphere table's ceiling the integration cannot proceed on evidence, so it returns
 * a floor and flags it. Treating that flag as optional is how a "we don't know" turns into a
 * number in someone's slide deck.
 */
function isCapped(result: DecayResponse): boolean {
  return result.lifetime.nominal.receipt.uncertainty?.capped === true;
}

/** Decade markers along the logarithmic axis. */
function AxisTicks() {
  return (
    <>
      {AXIS_TICKS.map((tick) => (
        <span key={tick} aria-hidden>
          <span
            className="absolute top-[1.4rem] h-1.5 w-px bg-edge"
            style={{ left: `${axisPosition(tick)}%` }}
          />
          <span
            className="absolute top-8 -translate-x-1/2 font-mono text-[10px] text-faint"
            style={{ left: `${axisPosition(tick)}%` }}
          >
            {tick} yr
          </span>
        </span>
      ))}
    </>
  );
}

/**
 * The bracket, drawn on a log axis with the disposal guideline marked.
 *
 * The whole point of this panel is that the *width* of the bar is the finding. A reader who
 * takes away only "about 13 years" has missed that the same orbit is 6 years under strong solar
 * activity and 29 under weak, and that the guideline sits inside that band.
 */
function LifetimeBar({ result }: { result: DecayResponse }) {
  const shortest = Number(result.lifetime.shortest.value);
  const longest = Number(result.lifetime.longest.value);
  const nominal = Number(result.lifetime.nominal.value);

  const capped = isCapped(result);

  const left = axisPosition(shortest);
  const right = axisPosition(longest);
  const guideline = axisPosition(result.disposal_guideline.years);

  const straddles =
    !result.disposal_guideline.met_under_every_scenario &&
    !result.disposal_guideline.met_under_no_scenario;

  // Above the atmosphere table the three scenarios all hit the reporting cap, so the bracket
  // degenerates to a point. Drawing that as a narrow bar labelled "500 yr - 500 yr" would read
  // as an unusually confident prediction when it means the opposite: the model has run out of
  // data and is stating a floor. So the bar runs open-ended off the right edge instead.
  if (capped) {
    return (
      <div className="mt-5">
        <div className="relative h-[4.6rem]">
          <div className="absolute inset-x-0 top-6 h-px bg-edge" />
          <AxisTicks />

          <div
            className="absolute top-1 h-6 w-px bg-speculative"
            style={{ left: `${guideline}%` }}
            aria-hidden
          />
          <span
            className="absolute top-0 -translate-x-1/2 text-[10px] whitespace-nowrap text-speculative"
            style={{ left: `${guideline}%` }}
          >
            {result.disposal_guideline.years} yr
          </span>

          <div
            className="absolute top-[1.05rem] right-0 h-2.5 rounded-l-full bg-predicted/20 ring-1 ring-predicted/40"
            style={{ left: `${axisPosition(nominal)}%` }}
          />

          <span className="absolute top-[2.9rem] left-0 font-mono text-[11px] text-predicted">
            &gt; {formatYears(nominal)}
          </span>
        </div>

        <p className="mt-1 text-xs leading-6 text-muted">
          Beyond the range this model estimates. At this altitude drag is negligible on any
          timescale a mission plans around, so {formatYears(nominal)} is a floor, not a
          prediction — extrapolating the atmosphere model past its data would produce a confident
          number with nothing behind it.
        </p>
      </div>
    );
  }

  return (
    <div className="mt-5">
      <div className="relative h-[4.6rem]">
        {/* Axis, with decade ticks: the scale is logarithmic because lifetimes here span four
            orders of magnitude, and an unlabelled log axis silently misleads. */}
        <div className="absolute inset-x-0 top-6 h-px bg-edge" />
        <AxisTicks />

        {/* The 25-year disposal guideline. */}
        <div
          className="absolute top-1 h-6 w-px bg-speculative"
          style={{ left: `${guideline}%` }}
          aria-hidden
        />
        <span
          className="absolute top-0 -translate-x-1/2 text-[10px] whitespace-nowrap text-speculative"
          style={{ left: `${guideline}%` }}
        >
          {result.disposal_guideline.years} yr
        </span>

        {/* The bracket itself. */}
        <div
          className="absolute top-[1.05rem] h-2.5 rounded-full bg-predicted/35 ring-1 ring-predicted/60"
          style={{ left: `${left}%`, width: `${Math.max(right - left, 0.6)}%` }}
        />
        <div
          className="absolute top-[0.8rem] h-5 w-0.5 bg-predicted"
          style={{ left: `${axisPosition(nominal)}%` }}
          aria-hidden
        />

        {/* The bracket is labelled by the figures below rather than inline: at narrow ranges
            two end labels overlap each other and the decade ticks. */}
        <span className="absolute top-[2.9rem] left-0 font-mono text-[11px] text-predicted">
          {formatYears(shortest)} – {formatYears(longest)}
        </span>
      </div>

      <p className="mt-1 text-xs leading-6 text-muted">
        {straddles ? (
          <>
            <span className="text-speculative">
              The {result.disposal_guideline.years}-year guideline falls inside the range.
            </span>{" "}
            Whether this orbit complies depends on solar activity over the mission — which is not
            knowable in advance. That is a real finding, not a limitation of the tool.
          </>
        ) : result.disposal_guideline.met_under_every_scenario ? (
          <>
            Clears the {result.disposal_guideline.years}-year guideline under every solar
            scenario.
          </>
        ) : (
          <>
            Exceeds the {result.disposal_guideline.years}-year guideline under every solar
            scenario, including the most favourable one.
          </>
        )}
      </p>
    </div>
  );
}

/**
 * Interactive orbital-decay explorer.
 *
 * This is the one place in the app where the uncertainty *is* the answer, so the panel is built
 * around the range rather than around a headline number. Dragging altitude and watching the
 * bracket swing across the disposal guideline teaches the point faster than any caption.
 */
export function DecayPanel() {
  const [altitude, setAltitude] = useState(550);
  const [preset, setPreset] = useState<(typeof SPACECRAFT_PRESETS)[number]>(SPACECRAFT_PRESETS[0]);

  const [result, setResult] = useState<DecayResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  // Dragging a slider fires continuously; without debouncing this would issue a request per
  // pixel. The integration is cheap but not free, and the service is shared with everything else
  // on the page.
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);

    const controller = new AbortController();

    timer.current = setTimeout(() => {
      // Set inside the timeout, not in the effect body: during the debounce window no request
      // exists yet, and setting state synchronously in an effect forces an extra render pass.
      setPending(true);

      fetch("/api/decay", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          altitude_km: altitude,
          mass_kg: preset.mass_kg,
          cross_section_m2: preset.cross_section_m2,
        }),
        signal: controller.signal,
      })
        .then(async (response) => {
          if (!response.ok) {
            const body = (await response.json().catch(() => null)) as ApiError | null;
            throw new Error(body?.error?.message ?? `HTTP ${response.status}`);
          }
          return response.json() as Promise<DecayResponse>;
        })
        .then((body) => {
          setResult(body);
          setError(null);
        })
        .catch((caught: unknown) => {
          if (caught instanceof DOMException && caught.name === "AbortError") return;
          setError(caught instanceof Error ? caught.message : String(caught));
        })
        .finally(() => setPending(false));
    }, 180);

    return () => {
      controller.abort();
      if (timer.current) clearTimeout(timer.current);
    };
  }, [altitude, preset]);

  const spread = useMemo(() => result?.spread_factor ?? null, [result]);

  return (
    <section className="mt-10 rounded-lg border border-edge bg-surface p-5">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-medium tracking-tight text-foreground">Orbital lifetime</h2>
        <span className="text-[11px] text-faint">
          the range is the answer, not a hedge around one
        </span>
      </header>

      <p className="mt-2 max-w-2xl text-xs leading-6 text-muted">
        How long an orbit survives depends on how much atmosphere it flies through, and density at
        these altitudes swings several-fold across the solar cycle. Predicting a lifetime means
        predicting the Sun years ahead, which nobody can do — so this returns the bracket that the
        physics supports rather than a single confident figure.
      </p>

      <div className="mt-5 grid gap-5 sm:grid-cols-[1fr_auto]">
        <label className="flex flex-col gap-2">
          <span className="flex items-baseline justify-between text-[11px] uppercase tracking-wide text-muted">
            <span>Circular altitude</span>
            <span className="font-mono text-sm text-foreground">{altitude} km</span>
          </span>
          <input
            type="range"
            min={200}
            max={1000}
            step={10}
            value={altitude}
            onChange={(event) => setAltitude(Number(event.target.value))}
            className="accent-predicted"
            aria-label="Circular altitude in kilometres"
          />
        </label>

        <div className="flex flex-wrap gap-1.5">
          {SPACECRAFT_PRESETS.map((item) => (
            <button
              key={item.label}
              type="button"
              onClick={() => setPreset(item)}
              title={item.note}
              className={`rounded-md border px-2.5 py-1.5 text-xs transition-colors ${
                preset.label === item.label
                  ? "border-accent bg-surface-inset text-foreground"
                  : "border-edge text-muted hover:border-accent hover:text-foreground"
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <p className="mt-4 rounded-md border border-speculative/40 bg-surface-inset px-3 py-2 text-xs text-speculative">
          {error}
        </p>
      )}

      {result && (
        <div className={pending ? "opacity-60 transition-opacity" : "transition-opacity"}>
          <LifetimeBar result={result} />

          <dl className="mt-5 grid gap-4 sm:grid-cols-3">
            {(["shortest", "nominal", "longest"] as const).map((key) => (
              <div key={key}>
                <dt className="text-[11px] uppercase tracking-wide text-muted">
                  {key === "shortest"
                    ? "Strong solar activity"
                    : key === "nominal"
                      ? "Average"
                      : "Weak solar activity"}
                </dt>
                <dd className="mt-1">
                  <TieredValue
                    value={result.lifetime[key]}
                    // The ">" is not decoration: a capped figure is a floor, and printing it
                    // bare would turn "we stopped estimating" into a precise-looking answer.
                    format={(raw) =>
                      isCapped(result)
                        ? `> ${formatBareYears(Number(raw))}`
                        : formatBareYears(Number(raw))
                    }
                  />
                </dd>
              </div>
            ))}
          </dl>

          <p className="mt-4 text-[11px] leading-6 text-faint">
            {spread !== null && !isCapped(result) && (
              <>
                Solar activity alone moves this by <span className="font-mono">{spread}×</span>.{" "}
              </>
            )}
            Ballistic term{" "}
            <span className="font-mono">
              {result.spacecraft.ballistic_term_m2_per_kg} m²/kg
            </span>
            . {result.assumptions} Click any value for its full provenance.
          </p>
        </div>
      )}
    </section>
  );
}

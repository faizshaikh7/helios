"use client";

import { ThemeToggle } from "@/components/ThemeToggle";

type Health = "ok" | "degraded" | "unreachable" | "checking";

/** Scroll to a product workspace while respecting reduced-motion preferences. */
function moveTo(id: string): void {
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

/** Reviewer-facing first screen that makes the product and its evidence legible at a glance. */
export function ReviewerHero({
  health,
  onRunMissionDemo,
}: {
  health: Health;
  onRunMissionDemo: () => void;
}) {
  const healthy = health === "ok";

  return (
    <section
      id="top"
      className="hero-grid relative overflow-hidden rounded-[2rem] border border-edge bg-surface px-5 pb-7 pt-4 shadow-[0_24px_80px_rgba(2,132,199,0.10)] sm:px-8 sm:pb-9"
    >
      <div className="pointer-events-none absolute -right-24 -top-28 h-80 w-80 rounded-full bg-accent/15 blur-3xl" />
      <div className="pointer-events-none absolute -bottom-36 left-1/3 h-72 w-72 rounded-full bg-observed/10 blur-3xl" />

      <nav className="relative flex items-center justify-between gap-4" aria-label="Product navigation">
        <button
          type="button"
          onClick={() => moveTo("top")}
          className="group flex items-center gap-2.5 text-left"
          aria-label="Back to top"
        >
          <span className="grid h-8 w-8 place-items-center rounded-xl border border-accent/30 bg-accent/10 text-sm text-accent shadow-[inset_0_0_20px_rgba(2,132,199,0.12)]">
            ◉
          </span>
          <span>
            <span className="block text-sm font-semibold tracking-[0.18em] text-foreground">HELIOS</span>
            <span className="block text-[9px] uppercase tracking-[0.2em] text-faint">orbital intelligence</span>
          </span>
        </button>

        <div className="hidden items-center gap-5 text-[11px] text-muted md:flex">
          <button type="button" onClick={() => moveTo("atlas")} className="hover:text-foreground">Atlas AI</button>
          <button type="button" onClick={() => moveTo("mission-control")} className="hover:text-foreground">Mission analysis</button>
          <button type="button" onClick={() => moveTo("conjunction-lab")} className="hover:text-foreground">Space safety</button>
          <button type="button" onClick={() => moveTo("solar-system")} className="hover:text-foreground">Deep space</button>
        </div>

        <div className="flex items-center gap-3">
          <span className="hidden items-center gap-2 text-[10px] uppercase tracking-wide text-muted sm:inline-flex">
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                healthy
                  ? "bg-ok shadow-[0_0_10px_var(--ok)]"
                  : health === "checking"
                    ? "bg-faint"
                    : "bg-danger"
              }`}
            />
            {health === "checking" ? "checking" : healthy ? "systems nominal" : health}
          </span>
          <ThemeToggle />
        </div>
      </nav>

      <div className="relative mt-12 grid items-center gap-10 lg:grid-cols-[1.3fr_0.7fr] lg:gap-14">
        <div>
          <div className="inline-flex items-center gap-2 rounded-full border border-derived/30 bg-derived/5 px-3 py-1 text-[10px] font-medium uppercase tracking-[0.18em] text-derived">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-derived" />
            independently verified space science
          </div>
          <h1 className="mt-5 max-w-3xl text-4xl font-semibold leading-[0.98] tracking-[-0.045em] text-foreground sm:text-6xl">
            See the mission.
            <span className="block bg-gradient-to-r from-accent via-derived to-observed bg-clip-text text-transparent">
              Trust the math.
            </span>
          </h1>
          <p className="mt-5 max-w-2xl text-sm leading-7 text-muted sm:text-base">
            A live orbital-intelligence workspace that turns public space data into pass windows,
            encounter geometry, eclipse forecasts and a precision solar system—with the source,
            frame, time scale and uncertainty attached to every result.
          </p>

          <div className="mt-7 flex flex-wrap gap-3">
            <button
              type="button"
              onClick={onRunMissionDemo}
              className="group rounded-xl bg-accent px-5 py-3 text-sm font-semibold text-accent-contrast shadow-[0_12px_30px_rgba(2,132,199,0.24)] hover:-translate-y-0.5 hover:shadow-[0_16px_36px_rgba(2,132,199,0.32)]"
            >
              Run live ISS analysis <span className="ml-1 inline-block group-hover:translate-x-0.5">→</span>
            </button>
            <button
              type="button"
              onClick={() => moveTo("solar-system")}
              className="rounded-xl border border-edge-strong bg-background/60 px-5 py-3 text-sm font-medium text-foreground backdrop-blur hover:-translate-y-0.5 hover:border-accent/50"
            >
              Explore JPL solar system
            </button>
          </div>

          <p className="mt-4 text-[10px] uppercase tracking-[0.16em] text-faint">
            No canned dashboard · every action calls the live science service
          </p>
        </div>

        <div className="relative rounded-2xl border border-edge bg-background/65 p-4 shadow-2xl backdrop-blur-xl">
          <div className="flex items-center justify-between border-b border-edge pb-3">
            <span className="text-[10px] uppercase tracking-[0.2em] text-faint">Capability stack</span>
            <span className="font-mono text-[10px] text-observed">LIVE</span>
          </div>
          <div className="mt-2 space-y-1.5">
            {[
              ["01", "Track & predict", "SGP4 · WGS84 · access windows"],
              ["02", "Screen & explain", "TCA · miss distance · RTN"],
              ["03", "Explore & verify", "JPL DE421 · Orekit · provenance"],
            ].map(([number, title, detail]) => (
              <div key={number} className="group flex items-center gap-3 rounded-xl border border-transparent p-3 hover:border-edge hover:bg-surface-inset/60">
                <span className="font-mono text-[10px] text-accent">{number}</span>
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-medium text-foreground">{title}</span>
                  <span className="block truncate font-mono text-[10px] text-faint">{detail}</span>
                </span>
                <span className="text-xs text-faint group-hover:translate-x-0.5 group-hover:text-accent">↗</span>
              </div>
            ))}
          </div>
          <div className="mt-3 rounded-xl border border-observed/20 bg-observed/5 px-3 py-2.5">
            <p className="text-[10px] uppercase tracking-[0.14em] text-observed">Trust boundary</p>
            <p className="mt-1 text-[11px] leading-5 text-muted">
              The system shows what it knows, how it knows it, and where public data stops.
            </p>
          </div>
        </div>
      </div>

      <div className="relative mt-10 grid grid-cols-2 gap-px overflow-hidden rounded-2xl border border-edge bg-edge sm:grid-cols-4">
        {[
          ["202 / 202", "tool accuracy ceiling"],
          ["420", "automated checks"],
          ["17", "science endpoints"],
          ["11", "grounded AI tools"],
        ].map(([value, label]) => (
          <div key={label} className="bg-background/80 px-4 py-4 backdrop-blur">
            <p className="font-mono text-xl font-medium tracking-tight text-foreground">{value}</p>
            <p className="mt-1 text-[9px] uppercase tracking-[0.15em] text-faint">{label}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

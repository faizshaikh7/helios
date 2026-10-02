"use client";

import { ThemeToggle } from "@/components/ThemeToggle";

type Health = "ok" | "degraded" | "unreachable" | "checking";

/** Scroll to a product workspace while respecting the browser's native focus and history. */
function moveTo(id: string): void {
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

/** Translate service health into concise reviewer-facing language. */
function healthLabel(health: Health): string {
  if (health === "ok") return "All systems operational";
  if (health === "checking") return "Checking systems";
  if (health === "degraded") return "Service degraded";
  return "Service unreachable";
}

/** Product header and first-run workflow selector for technical and non-technical reviewers. */
export function ReviewerHero({
  health,
  onRunMissionDemo,
}: {
  health: Health;
  onRunMissionDemo: () => void;
}) {
  const healthy = health === "ok";

  return (
    <>
      <header id="top" className="border-b border-edge bg-background">
        <div className="mx-auto max-w-[1600px] px-5 sm:px-8">
          <div className="flex min-h-16 items-center justify-between gap-5 border-b border-edge">
            <button
              type="button"
              onClick={() => moveTo("top")}
              className="flex items-center gap-3 text-left"
              aria-label="Back to overview"
            >
              <span className="grid h-8 w-8 place-items-center border border-foreground bg-foreground text-[10px] font-bold text-background">
                H
              </span>
              <span>
                <span className="block text-sm font-semibold tracking-[0.12em] text-foreground">
                  HELIOS
                </span>
                <span className="block text-[9px] tracking-wide text-faint">
                  Mission intelligence
                </span>
              </span>
            </button>

            <div className="flex items-center gap-4">
              <span className="hidden items-center gap-2 text-[11px] text-muted sm:inline-flex">
                <span
                  className={`h-2 w-2 rounded-full ${
                    healthy
                      ? "bg-ok"
                      : health === "checking"
                        ? "bg-faint"
                        : "bg-danger"
                  }`}
                />
                {healthLabel(health)}
              </span>
              <ThemeToggle />
            </div>
          </div>

          <div className="grid gap-10 py-12 lg:grid-cols-12 lg:items-end lg:py-16">
            <div className="lg:col-span-7">
              <p className="text-xs font-medium text-derived">Auditable orbital analysis</p>
              <h1 className="mt-4 max-w-4xl text-4xl font-semibold leading-[1.02] tracking-[-0.045em] text-foreground sm:text-6xl lg:text-7xl">
                Mission analysis,
                <span className="block text-muted">with the evidence attached.</span>
              </h1>
              <p className="mt-6 max-w-2xl text-base leading-7 text-muted">
                Compute satellite access, encounter geometry, eclipse exposure and speculative
                concept trades. Every result keeps its source, units, assumptions and trust
                boundary visible.
              </p>
              <div className="mt-8 flex flex-wrap gap-3">
                <button
                  type="button"
                  onClick={onRunMissionDemo}
                  className="rounded-lg bg-foreground px-5 py-3 text-sm font-medium text-background hover:opacity-85"
                >
                  Run the ISS example
                </button>
                <button
                  type="button"
                  onClick={() => moveTo("mission-control")}
                  className="rounded-lg border border-edge-strong px-5 py-3 text-sm font-medium text-foreground hover:bg-surface-inset"
                >
                  Open mission workspace
                </button>
              </div>
            </div>

            <div className="lg:col-span-5 lg:pl-8">
              <div className="border-t border-edge-strong">
                {[
                  [
                    "01",
                    "Analyze a live mission",
                    "Passes, orbit, ground track and eclipse",
                    "mission-control",
                  ],
                  [
                    "02",
                    "Ask the mission copilot",
                    "Typed tools with a visible execution trace",
                    "atlas",
                  ],
                  [
                    "03",
                    "Explore a new concept",
                    "Editable constraints and exportable calculations",
                    "concept-lab",
                  ],
                ].map(([number, title, detail, target]) => (
                  <button
                    key={number}
                    type="button"
                    onClick={() => moveTo(target)}
                    className="group grid w-full grid-cols-[2rem_1fr_auto] items-center gap-3 border-b border-edge py-4 text-left"
                  >
                    <span className="font-mono text-[10px] text-faint">{number}</span>
                    <span>
                      <span className="block text-sm font-medium text-foreground">{title}</span>
                      <span className="mt-0.5 block text-[11px] text-muted">{detail}</span>
                    </span>
                    <span className="text-sm text-faint group-hover:translate-x-1 group-hover:text-foreground">
                      →
                    </span>
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="grid border-t border-edge sm:grid-cols-4">
            {[
              ["202 / 202", "verified tool ceiling"],
              ["427", "automated checks"],
              ["17", "science endpoints"],
              ["12", "typed agent tools"],
            ].map(([value, label]) => (
              <div
                key={label}
                className="border-b border-edge py-4 sm:border-b-0 sm:border-r sm:px-5 sm:first:pl-0 sm:last:border-r-0"
              >
                <span className="font-mono text-base font-medium text-foreground">{value}</span>
                <span className="ml-2 text-[10px] text-faint">{label}</span>
              </div>
            ))}
          </div>
        </div>
      </header>

      <nav
        className="sticky top-0 z-40 border-b border-edge bg-background/95 backdrop-blur"
        aria-label="Workspace navigation"
      >
        <div className="mx-auto flex max-w-[1600px] items-center gap-1 overflow-x-auto px-5 py-2 sm:px-8">
          {[
            ["Overview", "top"],
            ["Mission analysis", "mission-control"],
            ["Atlas", "atlas"],
            ["Space safety", "conjunction-lab"],
            ["Concept lab", "concept-lab"],
            ["Deep space", "solar-system"],
          ].map(([label, target]) => (
            <button
              key={target}
              type="button"
              onClick={() => moveTo(target)}
              className="shrink-0 rounded-md px-3 py-2 text-[11px] font-medium text-muted hover:bg-surface-inset hover:text-foreground"
            >
              {label}
            </button>
          ))}
          <span className="ml-auto hidden whitespace-nowrap text-[10px] text-faint lg:block">
            Research use · assumptions remain visible
          </span>
        </div>
      </nav>
    </>
  );
}

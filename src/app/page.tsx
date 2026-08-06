"use client";

import { useEffect, useState } from "react";

/** Result of the deployed science service's time-scale self-check. */
type TimeScaleCheck = {
  measured_tt_minus_utc_s?: number;
  expected_tt_minus_utc_s?: number;
  within_tolerance: boolean;
  error?: string;
};

/** Payload returned by the Python science service at `/api/health`. */
type Health = {
  status: "ok" | "degraded";
  service: string;
  python: string;
  platform: string;
  libraries: Record<string, string>;
  checks: { time_scales: TimeScaleCheck };
};

/**
 * Renders a labelled value in the status grid.
 *
 * @param label - Field name shown in muted text.
 * @param children - The value, rendered monospaced.
 */
function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-6 border-b border-white/5 py-2 last:border-0">
      <span className="text-sm text-zinc-500">{label}</span>
      <span className="font-mono text-sm text-zinc-200">{children}</span>
    </div>
  );
}

/**
 * M0 status page.
 *
 * M0's definition of done is that both runtimes are live on one URL, so this page exists to
 * demonstrate exactly that: it is served by Next.js and reports the state of the Python
 * science service it fetched. It is replaced by the real interface at M5.
 */
export default function Home() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/health")
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then(setHealth)
      .catch((err: Error) => setError(err.message));
  }, []);

  const ok = health?.status === "ok";

  return (
    <div className="flex flex-1 items-center justify-center bg-black px-6 py-20">
      <main className="w-full max-w-xl">
        <h1 className="text-2xl font-medium tracking-tight text-zinc-100">space-sim</h1>
        <p className="mt-2 max-w-md text-sm leading-6 text-zinc-500">
          Orbital mechanics answers computed by real tools, with explicit assumptions,
          uncertainty, and sources.
        </p>

        <section className="mt-10 rounded-lg border border-white/10 bg-white/[0.02] p-5">
          <div className="mb-4 flex items-center gap-2.5">
            <span
              className={`inline-block h-2 w-2 rounded-full ${
                error ? "bg-red-500" : ok ? "bg-emerald-500" : "bg-zinc-600"
              }`}
              aria-hidden
            />
            <h2 className="text-sm font-medium text-zinc-300">Science service</h2>
            <span className="ml-auto font-mono text-xs text-zinc-500">
              {error ? "unreachable" : (health?.status ?? "checking…")}
            </span>
          </div>

          {error && (
            <p className="font-mono text-sm text-red-400">
              {error} — is the Python function running?
            </p>
          )}

          {health && (
            <>
              <Row label="Python">{health.python}</Row>
              {Object.entries(health.libraries).map(([name, version]) => (
                <Row key={name} label={name}>
                  {version}
                </Row>
              ))}
              <Row label="TT − UTC">
                {health.checks.time_scales.measured_tt_minus_utc_s !== undefined ? (
                  <span
                    className={
                      health.checks.time_scales.within_tolerance
                        ? "text-emerald-400"
                        : "text-red-400"
                    }
                  >
                    {health.checks.time_scales.measured_tt_minus_utc_s} s
                  </span>
                ) : (
                  <span className="text-red-400">{health.checks.time_scales.error}</span>
                )}
              </Row>
            </>
          )}
        </section>

        <p className="mt-6 text-xs leading-5 text-zinc-600">
          Milestone M0 — skeleton. The TT − UTC check verifies the deployed environment
          converts time scales correctly; a 69-second error here would move a low-Earth-orbit
          satellite roughly 500 km along-track.
        </p>
      </main>
    </div>
  );
}

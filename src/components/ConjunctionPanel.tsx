"use client";

import { useState } from "react";
import { ReceiptPanel, TieredValue } from "@/components/TieredValue";
import type { ApiError, ConjunctionResponse, Value } from "@/lib/types";

const inputClass =
  "rounded-md border border-edge bg-background px-2.5 py-1.5 font-mono text-sm " +
  "text-foreground outline-none focus:border-accent focus:bg-surface-inset";

/** Format an API UTC timestamp without allowing the browser to silently switch time zones. */
function formatUtc(value: number | string): string {
  return new Date(String(value)).toISOString().replace("T", " ").slice(0, 23) + "Z";
}

/** One labelled RTN component with a compact explanation of its encounter-plane meaning. */
function GeometryValue({ label, value, note }: { label: string; value: Value; note: string }) {
  return (
    <div className="rounded-md border border-edge bg-surface-inset p-3">
      <p className="text-[11px] uppercase tracking-wide text-muted">{label}</p>
      <div className="mt-1">
        <TieredValue value={value} />
      </div>
      <p className="mt-1 text-[10px] text-faint">{note}</p>
    </div>
  );
}

/** Agency-style two-object screening workspace with explicit limits on risk interpretation. */
export function ConjunctionPanel() {
  const [primaryId, setPrimaryId] = useState(25544);
  const [secondaryId, setSecondaryId] = useState(33591);
  const [durationHours, setDurationHours] = useState(24);
  const [thresholdKm, setThresholdKm] = useState(10);
  const [fromUtc, setFromUtc] = useState("");
  const [result, setResult] = useState<ConjunctionResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /** Submit a bounded conjunction screen to the science service. */
  async function runScreen(): Promise<void> {
    setLoading(true);
    setError(null);

    try {
      const response = await fetch("/api/conjunction", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          primary_norad_id: primaryId,
          secondary_norad_id: secondaryId,
          duration_hours: durationHours,
          step_seconds: 60,
          screening_threshold_km: thresholdKm,
          ...(fromUtc.trim() ? { from_utc: fromUtc.trim() } : {}),
        }),
      });
      const body = (await response.json()) as ConjunctionResponse | ApiError;
      if (!response.ok) {
        throw new Error("error" in body ? body.error.message : `HTTP ${response.status}`);
      }
      setResult(body as ConjunctionResponse);
    } catch (caught) {
      setResult(null);
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }

  return (
    <section id="conjunction-lab" className="scroll-mt-16 mt-8 rounded-xl border border-edge bg-surface p-5 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-medium text-foreground">Conjunction screening lab</h2>
          <p className="mt-1 max-w-2xl text-xs leading-5 text-muted">
            Find the closest geometric approach between two catalog objects. SGP4 screens the
            full interval and refines every candidate minimum in TEME.
          </p>
        </div>
        <span className="text-[10px] text-predicted">
          screening · not manoeuvre advice
        </span>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <label className="flex flex-col gap-1">
          <span className="text-[11px] uppercase tracking-wide text-muted">Primary NORAD</span>
          <input
            className={inputClass}
            type="number"
            min={1}
            value={primaryId}
            onChange={(event) => setPrimaryId(Number(event.target.value))}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[11px] uppercase tracking-wide text-muted">Secondary NORAD</span>
          <input
            className={inputClass}
            type="number"
            min={1}
            value={secondaryId}
            onChange={(event) => setSecondaryId(Number(event.target.value))}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[11px] uppercase tracking-wide text-muted">Window</span>
          <select
            className={inputClass}
            value={durationHours}
            onChange={(event) => setDurationHours(Number(event.target.value))}
          >
            <option value={6}>6 hours</option>
            <option value={24}>24 hours</option>
            <option value={48}>48 hours</option>
            <option value={72}>72 hours</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[11px] uppercase tracking-wide text-muted">Report threshold</span>
          <div className="flex items-center gap-2">
            <input
              className={`${inputClass} min-w-0 flex-1`}
              type="number"
              min={0.001}
              max={1000}
              step="any"
              value={thresholdKm}
              onChange={(event) => setThresholdKm(Number(event.target.value))}
            />
            <span className="text-xs text-muted">km</span>
          </div>
        </label>
        <div className="flex items-end">
          <button
            className="w-full rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-contrast transition hover:opacity-90 disabled:opacity-50"
            type="button"
            onClick={runScreen}
            disabled={loading}
          >
            {loading ? "Screening…" : "Run screen"}
          </button>
        </div>
      </div>

      <label className="mt-3 flex max-w-lg flex-col gap-1">
        <span className="text-[11px] uppercase tracking-wide text-muted">
          Start UTC <span className="normal-case text-faint">· blank means now</span>
        </span>
        <input
          className={inputClass}
          type="text"
          placeholder="2026-10-01T00:00:00Z"
          value={fromUtc}
          onChange={(event) => setFromUtc(event.target.value)}
        />
      </label>

      {error && (
        <p className="mt-4 rounded-md border border-danger/40 bg-danger/5 px-3 py-2 font-mono text-xs text-danger">
          {error}
        </p>
      )}

      {result && (
        <div className="mt-5 border-t border-edge pt-5">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <p className="text-sm text-foreground">
              {result.primary.name} <span className="font-mono text-xs text-muted">#{result.primary.norad_id}</span>
              <span className="mx-2 text-faint">↔</span>
              {result.secondary.name} <span className="font-mono text-xs text-muted">#{result.secondary.norad_id}</span>
            </p>
            <p className="text-[11px] text-muted">
              {result.screen.local_minima_screened} candidate approaches refined
            </p>
          </div>

          <div className="mt-4 grid gap-5 sm:grid-cols-3">
            <div>
              <p className="mb-1 text-[11px] uppercase tracking-wide text-muted">Time of closest approach</p>
              <TieredValue value={result.tca} format={formatUtc} />
            </div>
            <div>
              <p className="mb-1 text-[11px] uppercase tracking-wide text-muted">Miss distance</p>
              <TieredValue value={result.miss_distance} />
            </div>
            <div>
              <p className="mb-1 text-[11px] uppercase tracking-wide text-muted">Relative speed</p>
              <TieredValue value={result.relative_speed} />
            </div>
          </div>

          <p className={`mt-4 rounded-md border px-3 py-2 text-xs ${
            result.screening.inside_threshold
              ? "border-danger/40 bg-danger/5 text-danger"
              : "border-edge bg-surface-inset text-muted"
          }`}>
            {result.screening.inside_threshold
              ? `Inside the ${result.screening.threshold_km} km reporting volume.`
              : `Outside the ${result.screening.threshold_km} km reporting volume.`}{" "}
            {result.screening.meaning}
          </p>

          <div className="mt-4 grid gap-3 sm:grid-cols-3">
            <GeometryValue label="Radial separation" value={result.relative_position_rtn.radial} note="toward / away from Earth" />
            <GeometryValue label="In-track separation" value={result.relative_position_rtn.in_track} note="along the primary orbit" />
            <GeometryValue label="Cross-track separation" value={result.relative_position_rtn.cross_track} note="normal to the orbit plane" />
          </div>

          <div className="mt-4 rounded-md border border-speculative/40 bg-speculative/5 p-3">
            <p className="text-xs font-medium text-speculative">Collision probability unavailable</p>
            <p className="mt-1 text-xs leading-5 text-muted">{result.risk_assessment.reason}</p>
            <p className="mt-1 text-[11px] text-faint">
              Required next: {result.risk_assessment.required_inputs.join(" · ")}
            </p>
          </div>

          <details className="mt-4">
            <summary className="cursor-pointer text-xs text-muted hover:text-foreground">
              Audit method, element epochs, frame and uncertainty
            </summary>
            <ReceiptPanel receipt={result.receipt} />
          </details>
        </div>
      )}
    </section>
  );
}

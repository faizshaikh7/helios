"use client";

import { useState } from "react";
import type { Receipt, Tier, Value } from "@/lib/types";

/**
 * Visual encoding per trust tier.
 *
 * Line style carries the distinction, with colour only reinforcing it, so the meaning survives
 * colourblind viewers and greyscale screenshots. Colour alone would make the taxonomy decorative.
 */
const TIER_STYLE: Record<Tier, { line: string; text: string; label: string; blurb: string }> = {
  observed: {
    line: "underline decoration-solid decoration-emerald-400/70 decoration-2 underline-offset-4",
    text: "text-emerald-300",
    label: "observed",
    blurb: "Measured data, as published by its source.",
  },
  derived: {
    line: "underline decoration-dashed decoration-sky-400/70 decoration-2 underline-offset-4",
    text: "text-sky-300",
    label: "derived",
    blurb: "Computed from observed inputs using accepted physics.",
  },
  predicted: {
    line: "underline decoration-dotted decoration-amber-400/80 decoration-2 underline-offset-4",
    text: "text-amber-300",
    label: "predicted",
    blurb: "Estimated by propagating a model forward. Error grows with time from epoch.",
  },
  speculative: {
    line: "underline decoration-wavy decoration-rose-400/70 decoration-2 underline-offset-4",
    text: "text-rose-300",
    label: "speculative",
    blurb: "Insufficient evidence. Treat as a hypothesis, not a result.",
  },
};

/**
 * Renders one field of a receipt, skipping empties so the panel stays readable.
 *
 * @param label - Field name.
 * @param value - Field content; nullish and empty values render nothing.
 */
function ReceiptRow({ label, value }: { label: string; value: unknown }) {
  if (value === null || value === undefined || value === "") return null;

  const rendered =
    typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);

  return (
    <div className="grid grid-cols-[7rem_1fr] gap-3 py-1.5">
      <dt className="text-[11px] uppercase tracking-wide text-zinc-500">{label}</dt>
      <dd className="font-mono text-xs leading-relaxed whitespace-pre-wrap break-words text-zinc-300">
        {rendered}
      </dd>
    </div>
  );
}

/**
 * The full provenance record for a value.
 *
 * @param receipt - Provenance to display.
 */
export function ReceiptPanel({ receipt }: { receipt: Receipt }) {
  return (
    <dl className="mt-2 divide-y divide-white/5 rounded-md border border-white/10 bg-black/40 px-3 py-1">
      <ReceiptRow label="Tool" value={receipt.tool} />
      <ReceiptRow label="Frame" value={receipt.frame} />
      <ReceiptRow label="Time scale" value={receipt.time_scale} />
      <ReceiptRow label="Equation" value={receipt.equation} />
      <ReceiptRow label="Inputs" value={receipt.inputs} />
      <ReceiptRow label="Dataset" value={receipt.dataset} />
      <ReceiptRow label="Uncertainty" value={receipt.uncertainty} />
      <ReceiptRow label="Notes" value={receipt.notes} />
    </dl>
  );
}

/**
 * A value rendered with its trust tier, expanding to its receipt on click.
 *
 * Provenance attaches to the value rather than the surrounding answer, because a single view
 * routinely mixes a measured epoch, a derived age, and a predicted pass time.
 *
 * @param value - The quantity and its provenance.
 * @param format - Optional formatter for the displayed number.
 */
export function TieredValue({
  value,
  format,
}: {
  value: Value;
  format?: (raw: number | string) => string;
}) {
  const [open, setOpen] = useState(false);
  const style = TIER_STYLE[value.tier];

  const shown = format
    ? format(value.value)
    : typeof value.value === "number"
      ? value.value.toLocaleString(undefined, { maximumFractionDigits: 4 })
      : value.value;

  return (
    <span className="inline-block">
      <button
        type="button"
        onClick={() => setOpen((previous) => !previous)}
        title={`${style.label} — ${style.blurb} Click for provenance.`}
        aria-expanded={open}
        className={`font-mono text-sm ${style.line} ${style.text} cursor-pointer hover:brightness-125`}
      >
        {shown}
        {value.unit !== "none" && <span className="ml-1 text-zinc-500">{value.unit}</span>}
      </button>

      {open && (
        <span className="block">
          <span className="mt-1 block text-[11px] text-zinc-500">
            <span className={style.text}>{style.label}</span> — {style.blurb}
          </span>
          <ReceiptPanel receipt={value.receipt} />
        </span>
      )}
    </span>
  );
}

/**
 * Standalone legend explaining the four tiers.
 *
 * Shown once near the results rather than repeated per value, so the encoding is learnable
 * without cluttering every number.
 */
export function TierLegend() {
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-[11px] text-zinc-500">
      {(Object.keys(TIER_STYLE) as Tier[]).map((tier) => (
        <span key={tier} className="inline-flex items-center gap-1.5" title={TIER_STYLE[tier].blurb}>
          <span className={`${TIER_STYLE[tier].line} ${TIER_STYLE[tier].text} font-mono`}>
            abc
          </span>
          <span>{TIER_STYLE[tier].label}</span>
        </span>
      ))}
    </div>
  );
}

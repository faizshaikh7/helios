/**
 * Remove raw TLE lines from a satellite lookup before it reaches the model.
 *
 * The public API and UI keep the original lines for inspection. The agent has a structured
 * orbital-elements tool for numeric answers, so exposing the raw fixed-width record only gives
 * the model an unchecked second path for hand-parsing numbers without units or receipts.
 */
export function omitRawTle(result: unknown): unknown {
  if (!result || typeof result !== "object" || !("tle" in result)) return result;

  const structured = { ...(result as Record<string, unknown>) };
  delete structured.tle;
  return structured;
}

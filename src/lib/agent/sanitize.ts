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

/** Unicode superscripts used to turn common LaTeX exponents into readable plain text. */
const SUPERSCRIPTS: Record<string, string> = {
  "-": "⁻",
  "+": "⁺",
  "0": "⁰",
  "1": "¹",
  "2": "²",
  "3": "³",
  "4": "⁴",
  "5": "⁵",
  "6": "⁶",
  "7": "⁷",
  "8": "⁸",
  "9": "⁹",
};

/** Convert an integer exponent into displayable Unicode superscript characters. */
function superscript(value: string): string {
  const normalized = value.startsWith("+") ? value.slice(1) : value;
  return [...normalized].map((character) => SUPERSCRIPTS[character] ?? character).join("");
}

/**
 * Convert model-authored Markdown and lightweight LaTeX into safe, readable plain text.
 *
 * The client deliberately does not execute model HTML. This boundary removes presentation
 * syntax deterministically so providers cannot leak raw `**`, `###`, `$` or `\text{}` tokens
 * into the product even when they ignore the response-style instruction.
 */
export function normalizeAgentAnswer(answer: string): string {
  return answer
    .replace(/\r\n?/g, "\n")
    .replace(/```[a-zA-Z0-9_-]*\n?/g, "")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\\text\{([^{}]*)\}/g, "$1")
    .replace(/\\mathrm\{([^{}]*)\}/g, "$1")
    .replace(/\\operatorname\{([^{}]*)\}/g, "$1")
    .replace(/\\times/g, "×")
    .replace(/\\cdot/g, "·")
    .replace(/\\pm/g, "±")
    .replace(/(\d(?:[\d.]*\d)?)[eE]([+-]?\d+)/g, (_, coefficient: string, exponent: string) =>
      `${coefficient} × 10${superscript(exponent)}`,
    )
    .replace(/\^\{([+-]?\d+)\}/g, (_, exponent: string) => superscript(exponent))
    .replace(/\^([+-]?\d+)/g, (_, exponent: string) => superscript(exponent))
    .replace(/\$+/g, "")
    .replace(/^[ \t]{0,3}#{1,6}[ \t]*/gm, "")
    .replace(/^[ \t]*[-*+][ \t]+/gm, "• ")
    .replace(/^[ \t]*\d+[.)][ \t]+/gm, "• ")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/__([^_]+)__/g, "$1")
    .replace(/(?<!\*)\*([^*\n]+)\*(?!\*)/g, "$1")
    .replace(/(?<!_)_([^_\n]+)_(?!_)/g, "$1")
    .replace(/\\([#$%&_{}])/g, "$1")
    .replace(
      /\b(?:comfortably|massive|generous|excellent|ample)\s+(?=(?:positive\s+)?thermal margin\b|inside\b|below\b)/gi,
      "",
    )
    .replace(/\bthermally trivial\b/gi, "inside the stated thermal limit under this model")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/[ \t]{2,}/g, " ")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

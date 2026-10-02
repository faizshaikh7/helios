import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { normalizeAgentAnswer, omitRawTle } from "./sanitize.ts";

describe("model-facing satellite lookup", () => {
  it("removes raw TLE lines while retaining structured provenance", () => {
    const result = omitRawTle({
      norad_id: 25544,
      tle: { line1: "raw-1", line2: "raw-2" },
      epoch: { value: "2026-09-22T00:00:00Z", receipt: { dataset: { source: "celestrak" } } },
    });

    assert.deepEqual(result, {
      norad_id: 25544,
      epoch: { value: "2026-09-22T00:00:00Z", receipt: { dataset: { source: "celestrak" } } },
    });
  });

  it("leaves error results unchanged", () => {
    const error = { error: true, status: 502, detail: "catalog unavailable" };
    assert.equal(omitRawTle(error), error);
  });
});

describe("agent answer presentation boundary", () => {
  it("removes Markdown controls while preserving readable structure", () => {
    const answer = [
      "**Mission assessment**",
      "",
      "### Key outputs",
      "- **Trust tier:** speculative",
      "- **Power:** $408300000\\text{ W}$",
    ].join("\n");

    assert.equal(
      normalizeAgentAnswer(answer),
      [
        "Mission assessment",
        "",
        "Key outputs",
        "• Trust tier: speculative",
        "• Power: 4.083 × 10⁸ W",
      ].join("\n"),
    );
  });

  it("turns common LaTeX units and exponents into plain scientific notation", () => {
    const answer =
      "Flux: $1361\\text{ W/m}^2$; pressure: $8.62\\times 10^{-6}\\text{ Pa}$; power: 4.08e+15 W.";

    assert.equal(
      normalizeAgentAnswer(answer),
      "Flux: 1,361 W/m²; pressure: 8.62× 10⁻⁶ Pa; power: 4.08 × 10¹⁵ W.",
    );
  });

  it("does not remove minus signs or hyphens used as scientific content", () => {
    const answer = "The Sun-facing surface is at -12 °C in this two-sided model.";

    assert.equal(normalizeAgentAnswer(answer), answer);
  });

  it("removes unsupported thermal-confidence language without changing the result", () => {
    const answer =
      "The case sits comfortably below the limit with a massive positive thermal margin. " +
      "The case is thermally trivial.";

    assert.equal(
      normalizeAgentAnswer(answer),
      "The case sits below the limit with a positive thermal margin. The case is inside the stated thermal limit under this model.",
    );
  });

  it("presents tool fields as rounded, human-readable engineering values", () => {
    const answer = [
      "Mission Assessment: power is 136100000000000 W with a thermal margin of 208.907575385105 K.",
      "",
      "Decisive values",
      "- incidentFluxWm2: 1361 W/m^2",
      "- equilibriumTemperatureK: 191.092424614895 K",
      "- electricalPowerW: 136100000000000 W",
      "- thermalStatus: inside-limit",
      "- oneWayLightTimeSeconds: 499.004783836156 s",
    ].join("\n");

    assert.equal(
      normalizeAgentAnswer(answer),
      [
        "Mission Assessment: power is 1.361 × 10¹⁴ W with a thermal margin of 208.908 K.",
        "",
        "Decisive values",
        "• Incident flux — 1,361 W/m²",
        "• Equilibrium temperature — 191.092 K",
        "• Electrical power — 1.361 × 10¹⁴ W",
        "• Thermal status — Inside stated limit",
        "• One-way light time — 499.005 s",
      ].join("\n"),
    );
  });
});

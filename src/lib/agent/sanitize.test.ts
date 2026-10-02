import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { normalizeAgentAnswer, omitRawTle, presentGroundedAgentAnswer } from "./sanitize.ts";

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

  it("uses stellar-collector tool evidence instead of a model-transcribed number", () => {
    const answer = "Electrical power is 1.361 × 10¹¹ W.";
    const presented = presentGroundedAgentAnswer(answer, [
      {
        tool: "stellarCollectorTrade",
        output: {
          inputs: {
            orbitalRadiusAu: 1,
            collectorAreaKm2: 1000000,
            conversionEfficiencyPercent: 10,
            absorptivity: 0.1,
            reflectivity: 0.9,
            emissivity: 0.9,
            maximumTemperatureK: 400,
            arealDensityKgM2: 0.01,
            stellarLuminositySolar: 1,
            stellarMassSolar: 1,
          },
          results: {
            incidentFluxWm2: 1361,
            electricalPowerW: 136100000000000,
            equilibriumTemperatureK: 191.092424614895,
            thermalMarginK: 208.907575385105,
            thermalStatus: "inside-limit",
            collectorMassKg: 10000000000,
            minimumThermalRadiusAu: 0.228226967157496,
            powerAtMinimumThermalRadiusW: 2612908532275200,
            orbitalPeriodDays: 365.256898384042,
            oneWayLightTimeSeconds: 499.004783836156,
            interceptedLuminosityPercent: 3.55581626282282e-10,
            radiationPressurePa: 8.62563393772901e-6,
            radiationAccelerationMmS2: 0.862563393772901,
          },
          assumptions: ["circular orbit around an isolated star"],
          omissions: ["mutual shadowing and mutual heating"],
          sources: ["IAU nominal constants"],
          warning: "Concept screening only; not validated for mission operations.",
        },
      },
    ]);

    assert.match(presented, /Electrical power — 1\.361 × 10¹⁴ W/);
    assert.doesNotMatch(presented, /10¹¹ W/);
    assert.match(presented, /Collector area — 1 × 10⁶ km²/);
    assert.match(presented, /Conversion efficiency — 10 %/);
    assert.match(presented, /Radiation pressure — 8\.62563 × 10⁻⁶ Pa/);
    assert.match(presented, /Radiation acceleration — 0\.862563 mm\/s²/);
    assert.match(presented, /Assumptions\n• circular orbit/);
    assert.match(presented, /Decision boundary\nConcept screening only/);
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

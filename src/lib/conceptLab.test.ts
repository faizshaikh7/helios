import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  calculateStellarCollector,
  createConceptReceipt,
  type StellarCollectorInputs,
} from "./conceptLab.ts";

const BASELINE: StellarCollectorInputs = {
  orbitalRadiusAu: 1,
  collectorAreaKm2: 1,
  conversionEfficiencyPercent: 30,
  absorptivity: 0.9,
  emissivity: 0.85,
  maximumTemperatureK: 400,
  arealDensityKgM2: 2,
  reflectivity: 0.1,
  stellarLuminositySolar: 1,
  stellarMassSolar: 1,
};

describe("stellar collector concept model", () => {
  it("reproduces the IAU nominal solar irradiance at one astronomical unit", () => {
    const result = calculateStellarCollector(BASELINE);
    assert.equal(result.incidentFluxWm2, 1_361);
  });

  it("agrees with NASA's published Earth sidereal year", () => {
    const result = calculateStellarCollector(BASELINE);
    assert.ok(Math.abs(result.orbitalPeriodDays - 365.2569) < 0.0001);
  });

  it("obeys the independently known inverse-square irradiance relationship", () => {
    const near = calculateStellarCollector(BASELINE);
    const far = calculateStellarCollector({ ...BASELINE, orbitalRadiusAu: 2 });
    assert.equal(near.incidentFluxWm2 / far.incidentFluxWm2, 4);
    assert.equal(near.radiationPressurePa / far.radiationPressurePa, 4);
  });

  it("agrees with NASA's published absorbing-surface radiation pressure at one AU", () => {
    const result = calculateStellarCollector({
      ...BASELINE,
      absorptivity: 1,
      reflectivity: 0,
    });
    assert.ok(Math.abs(result.radiationPressurePa - 4.53e-6) < 0.02e-6);
  });

  it("flags an architecture inside its thermal limit", () => {
    const result = calculateStellarCollector(BASELINE);
    assert.equal(result.thermalStatus, "inside-limit");
    assert.ok(result.minimumThermalRadiusAu < BASELINE.orbitalRadiusAu);
  });

  it("rejects invalid physical assumptions", () => {
    assert.throws(
      () => calculateStellarCollector({ ...BASELINE, emissivity: 0 }),
      /emissivity must be between/,
    );
    assert.throws(
      () => calculateStellarCollector({ ...BASELINE, absorptivity: 0.95, reflectivity: 0.2 }),
      /cannot exceed 1/,
    );
  });

  it("exports assumptions, units, formulas, sources, and a speculative trust tier", () => {
    const results = calculateStellarCollector(BASELINE);
    const receipt = createConceptReceipt(BASELINE, results);
    assert.equal(receipt.trust_tier, "speculative");
    assert.equal(receipt.units.incidentFluxWm2, "W/m^2");
    for (const key of Object.keys(receipt.inputs)) {
      assert.ok(key in receipt.units, `input ${key} is missing its unit`);
    }
    for (const key of Object.keys(receipt.results)) {
      if (key.endsWith("Status")) continue;
      assert.ok(key in receipt.units, `result ${key} is missing its unit`);
    }
    assert.ok(receipt.assumptions.length >= 3);
    assert.ok(receipt.omissions.length >= 5);
    assert.ok(receipt.sources.some((source) => source.includes("IAU")));
  });
});

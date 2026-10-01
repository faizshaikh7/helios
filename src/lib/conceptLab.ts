/** Exact astronomical unit in metres (IAU 2012 definition). */
export const AU_M = 149_597_870_700;

/** IAU 2015 nominal total solar irradiance at one astronomical unit. */
export const NOMINAL_SOLAR_IRRADIANCE_W_M2 = 1_361;

/** IAU 2015 nominal solar mass parameter in cubic metres per square second. */
export const NOMINAL_SOLAR_GM_M3_S2 = 1.327_124_4e20;

/** Exact speed of light in vacuum in metres per second. */
export const SPEED_OF_LIGHT_M_S = 299_792_458;

/** Stefan-Boltzmann constant in watts per square metre per kelvin to the fourth. */
export const STEFAN_BOLTZMANN_W_M2_K4 = 5.670_374_419e-8;

/** Editable assumptions for the idealized stellar-collector trade study. */
export interface StellarCollectorInputs {
  orbitalRadiusAu: number;
  collectorAreaKm2: number;
  conversionEfficiencyPercent: number;
  absorptivity: number;
  emissivity: number;
  maximumTemperatureK: number;
  arealDensityKgM2: number;
  reflectivity: number;
  stellarLuminositySolar: number;
  stellarMassSolar: number;
}

/** Explicit-unit outputs from the idealized stellar-collector trade study. */
export interface StellarCollectorResults {
  incidentFluxWm2: number;
  electricalPowerW: number;
  equilibriumTemperatureK: number;
  thermalMarginK: number;
  minimumThermalRadiusAu: number;
  powerAtMinimumThermalRadiusW: number;
  orbitalPeriodDays: number;
  oneWayLightTimeSeconds: number;
  interceptedLuminosityPercent: number;
  collectorMassKg: number;
  radiationPressurePa: number;
  radiationAccelerationMmS2: number;
  thermalStatus: "inside-limit" | "over-temperature";
  coverageStatus: "dispersed" | "overlapping-area";
}

/** Reject a non-finite or out-of-range assumption before it reaches a physical equation. */
function requireRange(name: string, value: number, minimum: number, maximum: number): void {
  if (!Number.isFinite(value) || value < minimum || value > maximum) {
    throw new RangeError(`${name} must be between ${minimum} and ${maximum}`);
  }
}

/** Validate every trade-study input against deliberately broad conceptual-design bounds. */
export function validateStellarCollectorInputs(inputs: StellarCollectorInputs): void {
  requireRange("orbitalRadiusAu", inputs.orbitalRadiusAu, 0.01, 1_000);
  requireRange("collectorAreaKm2", inputs.collectorAreaKm2, 0.001, 1e18);
  requireRange("conversionEfficiencyPercent", inputs.conversionEfficiencyPercent, 0.01, 100);
  requireRange("absorptivity", inputs.absorptivity, 0.001, 1);
  requireRange("emissivity", inputs.emissivity, 0.001, 1);
  requireRange("maximumTemperatureK", inputs.maximumTemperatureK, 10, 10_000);
  requireRange("arealDensityKgM2", inputs.arealDensityKgM2, 0.000_001, 100_000);
  requireRange("reflectivity", inputs.reflectivity, 0, 1);
  requireRange("stellarLuminositySolar", inputs.stellarLuminositySolar, 0.000_1, 1_000_000);
  requireRange("stellarMassSolar", inputs.stellarMassSolar, 0.01, 1_000);
  if (inputs.absorptivity + inputs.reflectivity > 1) {
    throw new RangeError("absorptivity plus reflectivity cannot exceed 1");
  }
}

/**
 * Solve one idealized, circular stellar-collector architecture.
 *
 * The temperature assumes a flat Sun-facing collector radiating from both faces into deep
 * space. Radiation pressure treats the remainder after absorption and reflection as transmitted.
 * It excludes shadow, mutual heating, structural loads, solar wind, station keeping, degradation
 * and construction.
 */
export function calculateStellarCollector(
  inputs: StellarCollectorInputs,
): StellarCollectorResults {
  validateStellarCollectorInputs(inputs);

  const radiusM = inputs.orbitalRadiusAu * AU_M;
  const areaM2 = inputs.collectorAreaKm2 * 1_000_000;
  const efficiency = inputs.conversionEfficiencyPercent / 100;
  const incidentFluxWm2 =
    (NOMINAL_SOLAR_IRRADIANCE_W_M2 * inputs.stellarLuminositySolar) /
    inputs.orbitalRadiusAu ** 2;
  const equilibriumTemperatureK = (
    (inputs.absorptivity * incidentFluxWm2) /
    (2 * inputs.emissivity * STEFAN_BOLTZMANN_W_M2_K4)
  ) ** 0.25;
  const minimumThermalRadiusAu =
    inputs.orbitalRadiusAu * (equilibriumTemperatureK / inputs.maximumTemperatureK) ** 2;
  const fluxAtMinimumRadiusWm2 =
    (NOMINAL_SOLAR_IRRADIANCE_W_M2 * inputs.stellarLuminositySolar) /
    minimumThermalRadiusAu ** 2;
  const orbitalPeriodSeconds =
    2 * Math.PI * Math.sqrt(radiusM ** 3 / (NOMINAL_SOLAR_GM_M3_S2 * inputs.stellarMassSolar));
  const radiationPressurePa =
    (incidentFluxWm2 * (inputs.absorptivity + 2 * inputs.reflectivity)) /
    SPEED_OF_LIGHT_M_S;

  return {
    incidentFluxWm2,
    electricalPowerW: incidentFluxWm2 * areaM2 * efficiency,
    equilibriumTemperatureK,
    thermalMarginK: inputs.maximumTemperatureK - equilibriumTemperatureK,
    minimumThermalRadiusAu,
    powerAtMinimumThermalRadiusW: fluxAtMinimumRadiusWm2 * areaM2 * efficiency,
    orbitalPeriodDays: orbitalPeriodSeconds / 86_400,
    oneWayLightTimeSeconds: radiusM / SPEED_OF_LIGHT_M_S,
    interceptedLuminosityPercent: (areaM2 / (4 * Math.PI * radiusM ** 2)) * 100,
    collectorMassKg: areaM2 * inputs.arealDensityKgM2,
    radiationPressurePa,
    radiationAccelerationMmS2:
      (radiationPressurePa / inputs.arealDensityKgM2) * 1_000,
    thermalStatus:
      equilibriumTemperatureK <= inputs.maximumTemperatureK
        ? "inside-limit"
        : "over-temperature",
    coverageStatus: areaM2 <= 4 * Math.PI * radiusM ** 2 ? "dispersed" : "overlapping-area",
  };
}

/** Produce a complete, machine-readable calculation receipt without browser-specific objects. */
export function createConceptReceipt(
  inputs: StellarCollectorInputs,
  results: StellarCollectorResults,
) {
  return {
    schema: "helios.concept-lab.stellar-collector.v1",
    generated_at_utc: new Date().toISOString(),
    trust_tier: "speculative",
    model: "idealized circular stellar-collector trade study",
    inputs,
    results,
    units: {
      orbitalRadiusAu: "au",
      collectorAreaKm2: "km^2",
      conversionEfficiencyPercent: "%",
      absorptivity: "1",
      emissivity: "1",
      maximumTemperatureK: "K",
      arealDensityKgM2: "kg/m^2",
      reflectivity: "1",
      stellarLuminositySolar: "nominal solar luminosity ratio",
      stellarMassSolar: "nominal solar mass ratio",
      incidentFluxWm2: "W/m^2",
      electricalPowerW: "W",
      equilibriumTemperatureK: "K",
      thermalMarginK: "K",
      minimumThermalRadiusAu: "au",
      powerAtMinimumThermalRadiusW: "W",
      orbitalPeriodDays: "day",
      oneWayLightTimeSeconds: "s",
      interceptedLuminosityPercent: "%",
      collectorMassKg: "kg",
      radiationPressurePa: "Pa",
      radiationAccelerationMmS2: "mm/s^2",
    },
    assumptions: [
      "circular orbit around an isolated star",
      "flat Sun-facing collector radiating thermally from two faces",
      "uniform optical and thermal properties; non-absorbed/non-reflected light is transmitted",
      "no mutual shadowing or mutual heating",
      "no solar wind, degradation, structural, control, logistics, or station-keeping model",
    ],
    formulas: {
      flux: "S_nominal * luminosity_ratio / radius_au^2",
      electrical_power: "flux * collector_area * conversion_efficiency",
      thermal_equilibrium: "(absorptivity * flux / (2 * emissivity * sigma))^(1/4)",
      orbital_period: "2*pi*sqrt(radius_m^3 / (GM_sun_nominal * mass_ratio))",
      radiation_pressure: "flux * (absorptivity + 2 * reflectivity) / c",
    },
    constants: {
      astronomical_unit_m: AU_M,
      nominal_solar_irradiance_w_m2: NOMINAL_SOLAR_IRRADIANCE_W_M2,
      nominal_solar_gm_m3_s2: NOMINAL_SOLAR_GM_M3_S2,
      speed_of_light_m_s: SPEED_OF_LIGHT_M_S,
      stefan_boltzmann_w_m2_k4: STEFAN_BOLTZMANN_W_M2_K4,
    },
    sources: [
      "IAU 2012 Resolution B2 — astronomical unit",
      "IAU 2015 Resolution B3 — nominal solar irradiance and solar mass parameter",
      "NIST SI/CODATA — c and Stefan-Boltzmann constant",
    ],
    warning: "Concept screening only; not validated for design, procurement, or mission operations.",
  };
}

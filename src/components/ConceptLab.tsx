"use client";

import { useMemo, useState } from "react";
import {
  calculateStellarCollector,
  createConceptReceipt,
  type StellarCollectorInputs,
} from "@/lib/conceptLab";

const PRESETS: Array<{ name: string; detail: string; inputs: StellarCollectorInputs }> = [
  {
    name: "Mercury swarm",
    detail: "High flux, demanding thermal environment",
    inputs: {
      orbitalRadiusAu: 0.4,
      collectorAreaKm2: 1_000_000,
      conversionEfficiencyPercent: 35,
      absorptivity: 0.9,
      emissivity: 0.85,
      maximumTemperatureK: 650,
      arealDensityKgM2: 1.5,
      reflectivity: 0.1,
      stellarLuminositySolar: 1,
      stellarMassSolar: 1,
    },
  },
  {
    name: "Earth-orbit demonstrator",
    detail: "Cooler, lower-flux technology pathfinder",
    inputs: {
      orbitalRadiusAu: 1,
      collectorAreaKm2: 100,
      conversionEfficiencyPercent: 30,
      absorptivity: 0.85,
      emissivity: 0.9,
      maximumTemperatureK: 400,
      arealDensityKgM2: 4,
      reflectivity: 0.15,
      stellarLuminositySolar: 1,
      stellarMassSolar: 1,
    },
  },
  {
    name: "Large Dyson swarm",
    detail: "Civilization-scale area, still a sparse swarm",
    inputs: {
      orbitalRadiusAu: 0.7,
      collectorAreaKm2: 1_000_000_000_000,
      conversionEfficiencyPercent: 40,
      absorptivity: 0.9,
      emissivity: 0.9,
      maximumTemperatureK: 500,
      arealDensityKgM2: 0.5,
      reflectivity: 0.1,
      stellarLuminositySolar: 1,
      stellarMassSolar: 1,
    },
  },
];

const fieldClass =
  "w-full rounded-lg border border-edge bg-background/70 px-3 py-2 font-mono text-xs text-foreground outline-none focus:border-accent";

/** Format a result across very small and very large engineering orders of magnitude. */
function engineering(value: number, unit: string, digits = 3): string {
  const absolute = Math.abs(value);
  if ((absolute > 0 && absolute < 0.01) || absolute >= 1_000_000) {
    return `${value.toExponential(digits - 1)} ${unit}`;
  }
  return `${value.toLocaleString(undefined, { maximumFractionDigits: digits })} ${unit}`;
}

/** Reusable explicit-unit input for one concept assumption. */
function ConceptField({
  label,
  unit,
  value,
  minimum,
  maximum,
  step,
  onChange,
}: {
  label: string;
  unit: string;
  value: number;
  minimum: number;
  maximum: number;
  step: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 flex justify-between gap-3 text-[10px] uppercase tracking-wide text-muted">
        <span>{label}</span>
        <span className="normal-case text-faint">{unit}</span>
      </span>
      <input
        className={fieldClass}
        type="number"
        min={minimum}
        max={maximum}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  );
}

/** Download a JSON receipt containing the exact assumptions, units, equations and results. */
function downloadReceipt(inputs: StellarCollectorInputs): void {
  const receipt = createConceptReceipt(inputs, calculateStellarCollector(inputs));
  const blob = new Blob([JSON.stringify(receipt, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "helios-stellar-collector-receipt.json";
  anchor.click();
  URL.revokeObjectURL(url);
}

/** Interactive, assumption-first trade study for speculative stellar-collector architectures. */
export function ConceptLab() {
  const [inputs, setInputs] = useState<StellarCollectorInputs>(PRESETS[0].inputs);
  const [activePreset, setActivePreset] = useState(PRESETS[0].name);
  const results = useMemo(() => {
    try {
      return calculateStellarCollector(inputs);
    } catch {
      return null;
    }
  }, [inputs]);

  /** Replace one numeric assumption while keeping the remainder of the scenario intact. */
  const update = (key: keyof StellarCollectorInputs, value: number): void => {
    setInputs((current) => ({ ...current, [key]: value }));
    setActivePreset("Custom architecture");
  };

  const sensitivityRadii = results
    ? Array.from(
        new Set(
          [
            results.minimumThermalRadiusAu,
            inputs.orbitalRadiusAu,
            inputs.orbitalRadiusAu * 1.5,
            inputs.orbitalRadiusAu * 2,
          ].map((value) => Number(value.toPrecision(6))),
        ),
      ).sort((left, right) => left - right)
    : [];

  return (
    <section
      id="concept-lab"
      className="scroll-mt-4 mt-10 overflow-hidden rounded-2xl border border-edge bg-surface"
    >
      <div className="border-b border-edge bg-[radial-gradient(circle_at_85%_0%,color-mix(in_srgb,var(--tier-speculative)_14%,transparent),transparent_40%)] p-5 sm:p-7">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="text-[10px] uppercase tracking-[0.2em] text-speculative">
              Concept design · speculative tier
            </p>
            <h2 className="mt-2 text-2xl font-medium tracking-tight text-foreground">
              Turn a wild idea into a bounded trade study.
            </h2>
            <p className="mt-2 max-w-3xl text-xs leading-6 text-muted">
              Explore a Dyson-swarm collector around a star. Change its position, area, material,
              optical properties and conversion efficiency; Helios exposes the power, heat,
              orbital period, light delay, radiation pressure and nearest thermally allowable
              orbit—with the equations and omissions attached.
            </p>
          </div>
          <span className="rounded-full border border-speculative/30 bg-speculative/5 px-3 py-1 text-[10px] uppercase tracking-wide text-speculative">
            concept screening · not flight design
          </span>
        </div>

        <div className="mt-5 flex flex-wrap gap-2">
          {PRESETS.map((preset) => (
            <button
              key={preset.name}
              type="button"
              title={preset.detail}
              onClick={() => {
                setInputs(preset.inputs);
                setActivePreset(preset.name);
              }}
              className={`rounded-lg border px-3 py-2 text-left text-[11px] ${
                activePreset === preset.name
                  ? "border-speculative/50 bg-speculative/10 text-foreground"
                  : "border-edge bg-background/50 text-muted hover:border-speculative/30"
              }`}
            >
              <span className="block font-medium">{preset.name}</span>
              <span className="mt-0.5 hidden text-[9px] text-faint sm:block">{preset.detail}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="grid gap-0 lg:grid-cols-[0.78fr_1.22fr]">
        <div className="border-b border-edge p-5 sm:p-6 lg:border-b-0 lg:border-r">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-medium text-foreground">Architecture assumptions</h3>
            <span className="font-mono text-[10px] text-faint">{activePreset}</span>
          </div>
          <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2">
            <ConceptField
              label="Orbital radius"
              unit="au"
              value={inputs.orbitalRadiusAu}
              minimum={0.01}
              maximum={1000}
              step={0.01}
              onChange={(value) => update("orbitalRadiusAu", value)}
            />
            <ConceptField
              label="Collector area"
              unit="km²"
              value={inputs.collectorAreaKm2}
              minimum={0.001}
              maximum={1e18}
              step={1}
              onChange={(value) => update("collectorAreaKm2", value)}
            />
            <ConceptField
              label="Conversion efficiency"
              unit="%"
              value={inputs.conversionEfficiencyPercent}
              minimum={0.01}
              maximum={100}
              step={0.1}
              onChange={(value) => update("conversionEfficiencyPercent", value)}
            />
            <ConceptField
              label="Temperature limit"
              unit="K"
              value={inputs.maximumTemperatureK}
              minimum={10}
              maximum={10000}
              step={10}
              onChange={(value) => update("maximumTemperatureK", value)}
            />
            <ConceptField
              label="Absorptivity"
              unit="0–1"
              value={inputs.absorptivity}
              minimum={0.001}
              maximum={1}
              step={0.01}
              onChange={(value) => update("absorptivity", value)}
            />
            <ConceptField
              label="Emissivity"
              unit="0–1"
              value={inputs.emissivity}
              minimum={0.001}
              maximum={1}
              step={0.01}
              onChange={(value) => update("emissivity", value)}
            />
            <ConceptField
              label="Areal density"
              unit="kg/m²"
              value={inputs.arealDensityKgM2}
              minimum={0.000001}
              maximum={100000}
              step={0.1}
              onChange={(value) => update("arealDensityKgM2", value)}
            />
            <ConceptField
              label="Reflectivity"
              unit="0–1"
              value={inputs.reflectivity}
              minimum={0}
              maximum={1}
              step={0.01}
              onChange={(value) => update("reflectivity", value)}
            />
            <ConceptField
              label="Stellar luminosity"
              unit="L☉"
              value={inputs.stellarLuminositySolar}
              minimum={0.0001}
              maximum={1000000}
              step={0.1}
              onChange={(value) => update("stellarLuminositySolar", value)}
            />
            <ConceptField
              label="Stellar mass"
              unit="M☉"
              value={inputs.stellarMassSolar}
              minimum={0.01}
              maximum={1000}
              step={0.1}
              onChange={(value) => update("stellarMassSolar", value)}
            />
          </div>
        </div>

        <div className="p-5 sm:p-6">
          {!results ? (
            <div className="rounded-xl border border-danger/30 bg-danger/5 p-4 text-xs text-danger">
              One or more assumptions are outside the supported conceptual-design bounds.
            </div>
          ) : (
            <>
              <div
                className={`rounded-xl border p-4 ${
                  results.thermalStatus === "inside-limit"
                    ? "border-observed/30 bg-observed/5"
                    : "border-danger/30 bg-danger/5"
                }`}
              >
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <p className="text-[10px] uppercase tracking-[0.16em] text-faint">
                      Thermal decision
                    </p>
                    <p className="mt-1 text-sm font-medium text-foreground">
                      {results.thermalStatus === "inside-limit"
                        ? `Inside the ${inputs.maximumTemperatureK.toLocaleString()} K material limit`
                        : `Too hot by ${Math.abs(results.thermalMarginK).toFixed(1)} K`}
                    </p>
                  </div>
                  <div className="text-right">
                    <p className="font-mono text-xl text-foreground">
                      {results.minimumThermalRadiusAu.toFixed(3)} au
                    </p>
                    <p className="text-[9px] uppercase tracking-wide text-faint">
                      nearest thermal orbit
                    </p>
                  </div>
                </div>
                <p className="mt-3 text-[10px] leading-5 text-muted">
                  “Nearest” optimizes flux under this thermal model only. It does not include
                  structural dynamics, stellar activity, material degradation, navigation,
                  manufacturing or swarm-control constraints.
                </p>
              </div>

              <div className="mt-4 grid grid-cols-2 gap-3 xl:grid-cols-4">
                {[
                  ["Electrical power", engineering(results.electricalPowerW, "W")],
                  ["Collector temperature", engineering(results.equilibriumTemperatureK, "K", 4)],
                  ["Incident flux", engineering(results.incidentFluxWm2, "W/m²")],
                  ["Orbital period", engineering(results.orbitalPeriodDays, "days")],
                  ["One-way light time", engineering(results.oneWayLightTimeSeconds, "s")],
                  [
                    "Radiation acceleration",
                    engineering(results.radiationAccelerationMmS2, "mm/s²"),
                  ],
                  ["Collector mass", engineering(results.collectorMassKg, "kg")],
                  ["Starlight intercepted", engineering(results.interceptedLuminosityPercent, "%")],
                ].map(([label, value]) => (
                  <div key={label} className="rounded-xl border border-edge bg-background/50 p-3">
                    <p className="text-[9px] uppercase tracking-wide text-faint">{label}</p>
                    <p className="mt-2 break-words font-mono text-sm text-foreground">{value}</p>
                  </div>
                ))}
              </div>

              {results.coverageStatus === "overlapping-area" && (
                <p className="mt-3 rounded-xl border border-danger/30 bg-danger/5 p-3 text-[10px] leading-5 text-danger">
                  The requested collector area exceeds the surface area of a sphere at this
                  radius. A non-overlapping single-layer swarm cannot intercept that area; reduce
                  area, increase radius, or explicitly model multiple shadowing layers.
                </p>
              )}

              <div className="mt-5 overflow-hidden rounded-xl border border-edge">
                <div className="flex items-center justify-between border-b border-edge bg-surface-inset/50 px-4 py-3">
                  <h3 className="text-xs font-medium text-foreground">Position trade</h3>
                  <span className="text-[9px] uppercase tracking-wide text-faint">
                    same area and materials
                  </span>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[34rem] text-left text-[11px]">
                    <thead className="text-[9px] uppercase tracking-wide text-faint">
                      <tr className="border-b border-edge">
                        <th className="px-4 py-2 font-normal">Radius</th>
                        <th className="px-4 py-2 font-normal">Temperature</th>
                        <th className="px-4 py-2 font-normal">Power</th>
                        <th className="px-4 py-2 font-normal">Period</th>
                        <th className="px-4 py-2 font-normal">Thermal</th>
                      </tr>
                    </thead>
                    <tbody className="font-mono text-foreground">
                      {sensitivityRadii.map((radius) => {
                        const scenario = calculateStellarCollector({
                          ...inputs,
                          orbitalRadiusAu: radius,
                        });
                        return (
                          <tr key={radius} className="border-b border-edge last:border-0">
                            <td className="px-4 py-2.5">{radius.toFixed(3)} au</td>
                            <td className="px-4 py-2.5">
                              {scenario.equilibriumTemperatureK.toFixed(1)} K
                            </td>
                            <td className="px-4 py-2.5">
                              {engineering(scenario.electricalPowerW, "W")}
                            </td>
                            <td className="px-4 py-2.5">
                              {scenario.orbitalPeriodDays.toFixed(1)} d
                            </td>
                            <td
                              className={`px-4 py-2.5 ${
                                scenario.thermalStatus === "inside-limit"
                                  ? "text-observed"
                                  : "text-danger"
                              }`}
                            >
                              {scenario.thermalStatus === "inside-limit"
                                ? "inside limit"
                                : "too hot"}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>

              <div className="mt-5 flex flex-wrap items-center justify-between gap-3">
                <p className="max-w-2xl text-[10px] leading-5 text-faint">
                  Constants: IAU nominal solar irradiance and solar mass parameter; exact AU and
                  speed of light; NIST/CODATA Stefan–Boltzmann constant. Results are analytical
                  screening estimates and carry the speculative trust tier.
                </p>
                <button
                  type="button"
                  onClick={() => downloadReceipt(inputs)}
                  className="rounded-lg border border-edge-strong bg-background px-3 py-2 text-[11px] font-medium text-foreground hover:border-speculative/50"
                >
                  Export calculation receipt ↓
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </section>
  );
}

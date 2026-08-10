/** Trust tiers, mirroring `science.provenance.Tier` on the service. */
export type Tier = "observed" | "derived" | "predicted" | "speculative";

/** Everything needed to reproduce or audit a single value. */
export type Receipt = {
  tool: string;
  inputs: Record<string, unknown>;
  frame: string | null;
  time_scale: string | null;
  dataset: Record<string, unknown> | null;
  equation: string | null;
  uncertainty: Record<string, unknown> | null;
  notes: string | null;
};

/** A quantity with its unit, trust tier, and receipt. */
export type Value = {
  value: number | string;
  unit: string;
  tier: Tier;
  receipt: Receipt;
};

/** Response from `GET /api/satellite/{norad_id}`. */
export type SatelliteResponse = {
  norad_id: number;
  name: string;
  tle: { line1: string; line2: string };
  epoch: Value;
  element_set_age: Value;
  notice: string;
};

/** A single predicted access window. */
export type SatellitePass = {
  rise_utc: string;
  culmination_utc: string;
  set_utc: string;
  max_elevation_deg: number;
  duration_s: number;
};

/** Response from `POST /api/passes`. */
export type PassesResponse = {
  satellite: { norad_id: number; name: string };
  searched_from_utc: string;
  searched_days: number;
  min_elevation_deg: number;
  count: number;
  tier: Tier;
  receipt: Receipt;
  passes: SatellitePass[];
  notice: string;
};

/** One sample along a ground track. */
export type TrackSample = {
  t: string;
  lat: number;
  lon: number;
  alt_km: number;
};

/** Response from `POST /api/groundtrack`. */
export type GroundTrackResponse = {
  satellite: { norad_id: number; name: string };
  start_utc: string;
  minutes: number;
  current: { latitude: Value; longitude: Value; altitude: Value };
  samples: TrackSample[];
  notice: string;
};

/** Response from `POST /api/elements`. */
export type ElementsResponse = {
  satellite: { norad_id: number; name: string };
  evaluated_at_utc: string;
  at_epoch: boolean;
  state_vector: Record<string, Value>;
  classical_elements: Record<string, Value>;
  derived: Record<string, Value>;
  element_convention: string;
  notice: string;
};

/** A single shadow crossing. */
export type EclipseInterval = {
  entry_utc: string;
  exit_utc: string;
  duration_s: number;
  umbra_duration_s: number;
  orbit_fraction: number;
};

/** Response from `POST /api/eclipse`. */
export type EclipseResponse = {
  satellite: { norad_id: number; name: string };
  start_utc: string;
  days: number;
  beta_angle: Value;
  summary: Record<string, Value>;
  intervals: EclipseInterval[];
  notice: string;
};

/** Typed error body returned by the service. */
export type ApiError = { error: { code: string; message: string } };

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

/**
 * Response from `POST /api/decay`.
 *
 * The lifetime is three values rather than one on purpose: decay depends on solar activity,
 * which cannot be forecast years ahead, so the bracket is the result. Rendering only `nominal`
 * would misrepresent what the service computed.
 */
export type DecayResponse = {
  satellite: {
    norad_id: number;
    name: string;
    apogee_altitude_km: number;
    perigee_altitude_km: number;
  } | null;
  altitude_km: number;
  spacecraft: {
    mass_kg: number;
    cross_section_m2: number;
    drag_coefficient: number;
    ballistic_term_m2_per_kg: number;
  };
  lifetime: { shortest: Value; nominal: Value; longest: Value };
  spread_factor: number | null;
  disposal_guideline: {
    years: number;
    met_under_every_scenario: boolean;
    met_under_no_scenario: boolean;
    note: string;
  };
  assumptions: string;
  notice: string;
};

/** One arXiv record as returned by the science service. */
export type Paper = {
  arxiv_id: string;
  title: string;
  authors: string[];
  summary: string;
  published: string;
  updated: string;
  categories: string[];
  doi: string | null;
  journal_ref: string | null;
  url: string;
  peer_reviewed_signal: boolean;
};

/** Response from `POST /api/literature/search`. */
export type LiteratureSearchResponse = {
  query: string;
  count: number;
  papers: Paper[];
  citations: Value[];
  source: string;
  caveat: string;
  notice: string;
};

/**
 * Verdict on one claimed citation.
 *
 * `not_found` is the load-bearing case: well-formed, resolves to nothing, and therefore almost
 * certainly invented. `unchecked` is deliberately distinct — an unreachable service is not
 * evidence against a citation.
 */
export type CitationVerdict = {
  claimed: string;
  status: "verified" | "not_found" | "malformed" | "unchecked";
  paper: Paper | null;
  note: string | null;
};

/** Response from `POST /api/literature/verify`. */
export type CitationVerifyResponse = {
  results: CitationVerdict[];
  summary: { claimed: number; verified: number; unresolved: number; unchecked: number };
  interpretation: string;
  notice: string;
};

/** One body in a solar-system snapshot. */
export type SnapshotBody = {
  body: string;
  x_au: number;
  y_au: number;
  z_au: number;
  distance_from_barycentre_au: number;
  radius_m: number;
  colour: string;
  max_error_km: number;
};

/**
 * Response from `POST /api/ephemeris/snapshot`.
 *
 * Plain numbers rather than tiered values: a renderer consumes ten positions at once, and a
 * receipt per coordinate would be forty copies of identical provenance. The tier and accuracy
 * are stated once for the whole snapshot instead.
 */
export type SnapshotResponse = {
  at_utc: string;
  at_tdb: string;
  frame: string;
  tier: string;
  bodies: SnapshotBody[];
  accuracy: string;
  notice: string;
};

/** Typed error body returned by the service. */
export type ApiError = { error: { code: string; message: string } };

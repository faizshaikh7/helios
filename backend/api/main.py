"""HTTP surface for the science service.

One ASGI app owning every route, rather than a file per endpoint: routing is then explicit and
identical locally and in production, instead of being inferred from the filesystem.

Contract rules, per .agent/rules.md:

* Input is validated by pydantic; invalid input returns 422 with a typed body.
* Failures return ``{"error": {"code", "message"}}`` -- never a stack trace.
* Every returned quantity carries a unit, a frame where one applies, and a trust tier.
"""

from __future__ import annotations

import os
import platform
import sys
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from science import catalog, decay, eclipse, elements, literature, orbit
from science.provenance import Receipt, Tier, Value

app = FastAPI(
    title="Helios science service",
    description="Orbital mechanics with explicit frames, time scales, units, and provenance.",
)

# Shown alongside every prediction. The licence disclaims warranty; this states the operational
# limit in the place a user will actually read it.
OPERATIONAL_NOTICE = (
    "Research and educational use only. Do not use for mission operations, collision avoidance, "
    "or launch decisions. Verify independently before acting."
)

EXPECTED_TT_MINUS_UTC_SECONDS = 69.184
TIME_SCALE_TOLERANCE_SECONDS = 1e-6

# Evaluation only: pin the catalog to the same frozen element sets the ground truth was computed
# from. Without this the system is graded against a target it was never given, and the resulting
# error grows silently as element sets age. Never set in production.
_EVAL_FIXTURES = os.environ.get("EVAL_FIXTURES")
if _EVAL_FIXTURES:
    _pinned = catalog.load_fixtures(_EVAL_FIXTURES)
    print(f"[eval mode] catalog pinned to {_pinned} frozen element sets from {_EVAL_FIXTURES}")


def _parse_instant(value: str, field: str) -> datetime:
    """Parse an ISO-8601 UTC timestamp, defaulting a naive value to UTC.

    Args:
        value: The timestamp as supplied.
        field: Field name, for the error message.

    Returns:
        A timezone-aware datetime.

    Raises:
        RequestValidationError: If the value is not parseable.
    """
    try:
        # Python 3.11+ parses a trailing "Z" directly.
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise RequestValidationError(
            [
                {
                    "loc": ("body", field),
                    "msg": f"not a valid ISO-8601 timestamp: {value}",
                    "type": "value_error",
                }
            ]
        ) from exc

    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _error(code: str, message: str, status: int) -> JSONResponse:
    """Build a typed error response.

    Args:
        code: Stable machine-readable identifier.
        message: Human-readable explanation, safe to show a user.
        status: HTTP status code.

    Returns:
        A JSON response carrying only the typed error body.
    """
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


@app.exception_handler(catalog.CatalogNotFound)
async def _catalog_not_found_handler(_: Request, exc: catalog.CatalogNotFound) -> JSONResponse:
    """An unknown catalog number is the caller's mistake, so 404 rather than 502.

    Registered before the broader `CatalogError` handler because it is a subclass; the more
    specific handler must win.
    """
    return _error("satellite_not_found", str(exc), 404)


@app.exception_handler(catalog.CatalogError)
async def _catalog_error_handler(_: Request, exc: catalog.CatalogError) -> JSONResponse:
    """Turn catalog outages into a typed 502 rather than an unhandled 500."""
    return _error("catalog_unavailable", str(exc), 502)


@app.exception_handler(literature.LiteratureError)
async def _literature_error_handler(_: Request, exc: literature.LiteratureError) -> JSONResponse:
    """Report a literature-service failure as an upstream problem, not a client error."""
    return _error("literature_unavailable", str(exc), status=502)


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    """Return readable validation failures in the project's error shape.

    FastAPI's default body is a nested list of pydantic error dicts, which reaches a user as a
    bare "HTTP 422" with nothing actionable in it. Rejecting bad input is correct; failing to say
    which field and why is not.
    """
    problems = []
    for item in exc.errors():
        # loc is like ("body", "min_elevation_deg"); the leading source is noise to a user.
        field = ".".join(str(part) for part in item.get("loc", ()) if part != "body")
        problems.append(f"{field or 'request'}: {item.get('msg', 'invalid')}")

    return _error("invalid_input", "; ".join(problems), 422)


# --------------------------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------------------------


def _check_time_scales() -> dict[str, Any]:
    """Verify astropy converts UTC to TT correctly in this environment.

    Uses the two-part Julian date: a JD near 2.46e6 consumes most of a float64's significant
    digits, so differencing combined JDs introduces ~14 microseconds of error on its own. This
    is not `epoch.tt - epoch` -- those are the same instant in two scales and subtract to zero.

    Returns:
        Measured offset, expected offset, and whether they agree.
    """
    from astropy.time import Time

    epoch_utc = Time("2026-01-01T00:00:00", scale="utc")
    epoch_tt = epoch_utc.tt

    offset_seconds = float(
        ((epoch_tt.jd1 - epoch_utc.jd1) + (epoch_tt.jd2 - epoch_utc.jd2)) * 86400.0
    )

    return {
        "measured_tt_minus_utc_s": round(offset_seconds, 9),
        "expected_tt_minus_utc_s": EXPECTED_TT_MINUS_UTC_SECONDS,
        "within_tolerance": bool(
            abs(offset_seconds - EXPECTED_TT_MINUS_UTC_SECONDS) < TIME_SCALE_TOLERANCE_SECONDS
        ),
    }


def _library_versions() -> dict[str, str]:
    """Report installed versions of each scientific dependency.

    Results are only reproducible against known library versions, so the deployed versions are
    part of an answer's provenance.
    """
    versions: dict[str, str] = {}
    for name in ("astropy", "skyfield", "sgp4", "fastapi", "pydantic"):
        try:
            versions[name] = __import__(name).__version__
        except Exception as exc:  # noqa: BLE001 - surfacing any import failure is the point
            versions[name] = f"unavailable: {type(exc).__name__}"
    return versions


@app.get("/api/health")
def health() -> dict[str, Any]:
    """Report liveness, environment, and a time-scale self-check.

    Deliberately more than a ping: an endpoint returning only ``{"ok": true}`` would still pass
    while astropy was subtly misconfigured.
    """
    versions = _library_versions()

    try:
        time_check = _check_time_scales()
    except Exception as exc:  # noqa: BLE001 - a failed check is a reportable state, not a 500
        time_check = {"error": f"{type(exc).__name__}: {exc}", "within_tolerance": False}

    imports_ok = not any(v.startswith("unavailable") for v in versions.values())
    healthy = imports_ok and bool(time_check.get("within_tolerance"))

    return {
        "status": "ok" if healthy else "degraded",
        "service": "science",
        "python": platform.python_version(),
        "platform": sys.platform,
        "libraries": versions,
        "checks": {"time_scales": time_check},
    }


# --------------------------------------------------------------------------------------------
# Satellite lookup
# --------------------------------------------------------------------------------------------


@app.get("/api/satellite/{norad_id}")
def satellite(norad_id: int) -> dict[str, Any]:
    """Look up a satellite's current element set.

    Args:
        norad_id: NORAD catalog number, e.g. 25544 for the ISS.

    Returns:
        Identity and element-set metadata, with provenance.
    """
    tle = catalog.get_tle(norad_id)
    epoch = orbit.tle_epoch(tle)
    age_days = (datetime.now(UTC) - epoch).total_seconds() / 86400.0

    dataset = {
        "source": tle.source,
        "norad_id": tle.norad_id,
        "name": tle.name,
        "epoch_utc": epoch.isoformat(),
    }

    return {
        "norad_id": tle.norad_id,
        "name": tle.name,
        "tle": {"line1": tle.line1, "line2": tle.line2},
        "epoch": Value(
            value=epoch.isoformat(),
            unit="none",
            tier=Tier.OBSERVED,
            receipt=Receipt(
                tool="satellite_lookup",
                inputs={"norad_id": norad_id},
                time_scale="UTC",
                dataset=dataset,
                notes="Epoch as published in the element set; not computed.",
            ),
        ),
        "element_set_age": Value(
            value=round(age_days, 3),
            unit="days",
            tier=Tier.DERIVED,
            receipt=Receipt(
                tool="satellite_lookup",
                inputs={"norad_id": norad_id},
                time_scale="UTC",
                dataset=dataset,
                equation="now - epoch",
                notes=(
                    "SGP4 accuracy degrades with this age -- roughly a kilometre per day for "
                    "low Earth orbit."
                ),
            ),
        ),
        "notice": OPERATIONAL_NOTICE,
    }


# --------------------------------------------------------------------------------------------
# Passes
# --------------------------------------------------------------------------------------------


class PassRequest(BaseModel):
    """Parameters for a ground-station access-window query."""

    norad_id: int = Field(default=25544, ge=1, description="NORAD catalog number.")
    latitude_deg: float = Field(ge=-90, le=90, description="Station latitude, degrees north.")
    longitude_deg: float = Field(ge=-180, le=180, description="Station longitude, degrees east.")
    elevation_m: float = Field(default=0.0, ge=-500, le=9000, description="Station height.")
    station_name: str = Field(default="Ground station", max_length=120)
    min_elevation_deg: float = Field(
        default=10.0, ge=0, le=89, description="Elevation mask in degrees."
    )
    days: float = Field(default=1.0, gt=0, le=10, description="Search window length in days.")
    from_utc: str | None = Field(
        default=None,
        description=(
            "ISO-8601 UTC instant to start the search from, e.g. 2026-08-12T00:00:00Z. "
            "Omit to search from now."
        ),
    )


@app.post("/api/passes")
def passes(request: PassRequest) -> dict[str, Any]:
    """Predict ground-station access windows.

    Args:
        request: Satellite, station, elevation mask, and search window.

    Returns:
        Predicted passes with a shared receipt, plus the satellite's identity.
    """
    tle = catalog.get_tle(request.norad_id)
    station = orbit.GroundStation(
        name=request.station_name,
        latitude_deg=request.latitude_deg,
        longitude_deg=request.longitude_deg,
        elevation_m=request.elevation_m,
    )

    start = _parse_instant(request.from_utc, "from_utc") if request.from_utc else datetime.now(UTC)

    found = orbit.find_passes(
        tle,
        station,
        start,
        days=request.days,
        min_elevation_deg=request.min_elevation_deg,
    )
    receipt = orbit.pass_receipt(tle, station, request.min_elevation_deg, start)

    return {
        "satellite": {"norad_id": tle.norad_id, "name": tle.name},
        "searched_from_utc": start.isoformat(),
        "searched_days": request.days,
        "min_elevation_deg": request.min_elevation_deg,
        "count": len(found),
        "tier": Tier.PREDICTED,
        "receipt": receipt,
        "passes": [
            {
                "rise_utc": item.rise_utc.isoformat(),
                "culmination_utc": item.culmination_utc.isoformat(),
                "set_utc": item.set_utc.isoformat(),
                "max_elevation_deg": item.max_elevation_deg,
                "duration_s": item.duration_s,
            }
            for item in found
        ],
        "notice": OPERATIONAL_NOTICE,
    }


# --------------------------------------------------------------------------------------------
# Ground track
# --------------------------------------------------------------------------------------------


class GroundTrackRequest(BaseModel):
    """Parameters for a ground-track query."""

    norad_id: int = Field(default=25544, ge=1)
    minutes: int = Field(default=100, ge=1, le=1440, description="Span to cover.")
    step_seconds: int = Field(default=30, ge=5, le=600, description="Sample spacing.")


@app.post("/api/groundtrack")
def groundtrack(request: GroundTrackRequest) -> dict[str, Any]:
    """Sample the sub-satellite point over a span.

    Args:
        request: Satellite and sampling parameters.

    Returns:
        Track samples with a single shared receipt, plus the current sub-satellite point.
    """
    tle = catalog.get_tle(request.norad_id)
    start = datetime.now(UTC)

    samples = orbit.ground_track(
        tle, start, minutes=request.minutes, step_seconds=request.step_seconds
    )
    now_point = orbit.subpoint(tle, start)

    return {
        "satellite": {"norad_id": tle.norad_id, "name": tle.name},
        "start_utc": start.isoformat(),
        "minutes": request.minutes,
        "current": now_point,
        "samples": samples,
        "notice": OPERATIONAL_NOTICE,
    }


# --------------------------------------------------------------------------------------------
# State at an instant
# --------------------------------------------------------------------------------------------


class StateRequest(BaseModel):
    """Parameters for a state-at-an-instant query."""

    norad_id: int = Field(default=25544, ge=1)
    at_utc: str | None = Field(
        default=None,
        description=(
            "ISO-8601 UTC instant, e.g. 2026-08-12T00:00:00Z. Omit for now. Any instant is "
            "accepted; accuracy degrades with distance from the element set's epoch."
        ),
    )


@app.post("/api/state")
def state_at(request: StateRequest) -> dict[str, Any]:
    """Return where a satellite is, and how fast, at a specific instant.

    Distinct from `/api/groundtrack`, which samples forward from now. Questions are frequently
    about a stated moment -- "where was it at 03:00Z" -- and without this the only way to answer
    one is to sample a track and hope it covers the instant.

    Args:
        request: Satellite and instant.

    Returns:
        Sub-satellite point, altitude, and speed, each with provenance.

    Raises:
        HTTPException: If the timestamp cannot be parsed.
    """
    tle = catalog.get_tle(request.norad_id)

    when = _parse_instant(request.at_utc, "at_utc") if request.at_utc else datetime.now(UTC)

    point = orbit.subpoint(tle, when)
    state = elements.state_vector(tle, when)

    return {
        "satellite": {"norad_id": tle.norad_id, "name": tle.name},
        "at_utc": when.isoformat(),
        "latitude": point["latitude"],
        "longitude": point["longitude"],
        "altitude": point["altitude"],
        "speed": state["speed"],
        "notice": OPERATIONAL_NOTICE,
    }


# --------------------------------------------------------------------------------------------
# Orbital elements
# --------------------------------------------------------------------------------------------


class ElementsRequest(BaseModel):
    """Parameters for an orbital-element query."""

    norad_id: int = Field(default=25544, ge=1)
    at_epoch: bool = Field(
        default=True,
        description=(
            "Evaluate at the element set's own epoch, where results are derived rather than "
            "propagated. False evaluates now, which weakens the tier to predicted."
        ),
    )


@app.post("/api/elements")
def orbital_elements(request: ElementsRequest) -> dict[str, Any]:
    """Return the state vector, classical elements, and derived orbit properties.

    Elements are **osculating** -- computed from the instantaneous state -- not the Brouwer mean
    values a TLE prints. The two differ legitimately, by roughly a twentieth of a degree in
    inclination, so the distinction is stated rather than left for a reader to trip over.

    Args:
        request: Satellite and evaluation epoch.

    Returns:
        State vector, classical elements, and derived properties, each with provenance.
    """
    tle = catalog.get_tle(request.norad_id)
    when = orbit.tle_epoch(tle) if request.at_epoch else datetime.now(UTC)

    return {
        "satellite": {"norad_id": tle.norad_id, "name": tle.name},
        "evaluated_at_utc": when.isoformat(),
        "at_epoch": request.at_epoch,
        "state_vector": elements.state_vector(tle, when),
        "classical_elements": elements.classical_elements(tle, when),
        "derived": elements.derived_orbit_properties(tle, when),
        "element_convention": (
            "Osculating elements from the instantaneous state, not the Brouwer mean elements "
            "published in the two-line element set."
        ),
        "notice": OPERATIONAL_NOTICE,
    }


# --------------------------------------------------------------------------------------------
# Eclipse and beta angle
# --------------------------------------------------------------------------------------------


class EclipseRequest(BaseModel):
    """Parameters for an eclipse and beta-angle query."""

    norad_id: int = Field(default=25544, ge=1)
    days: float = Field(default=1.0, gt=0, le=10, description="Span to analyse, in days.")


@app.post("/api/eclipse")
def eclipse_analysis(request: EclipseRequest) -> dict[str, Any]:
    """Return beta angle and eclipse statistics over a span.

    These are the quantities that size a spacecraft's battery and drive its thermal design: beta
    angle sets how much of each orbit is spent in shadow, and the longest eclipse in a span is
    what the power system must survive.

    Args:
        request: Satellite and span.

    Returns:
        Beta angle now, eclipse summary statistics, and the individual intervals.
    """
    tle = catalog.get_tle(request.norad_id)
    start = datetime.now(UTC)

    intervals = eclipse.eclipse_intervals(tle, start, request.days)

    return {
        "satellite": {"norad_id": tle.norad_id, "name": tle.name},
        "start_utc": start.isoformat(),
        "days": request.days,
        "beta_angle": eclipse.beta_angle(tle, start),
        "summary": eclipse.eclipse_summary(tle, start, request.days),
        "intervals": [
            {
                "entry_utc": item.entry_utc.isoformat(),
                "exit_utc": item.exit_utc.isoformat(),
                "duration_s": round(item.duration_s, 1),
                "umbra_duration_s": round(item.umbra_duration_s, 1),
                "orbit_fraction": round(item.orbit_fraction, 5),
            }
            for item in intervals
        ],
        "notice": OPERATIONAL_NOTICE,
    }


# --------------------------------------------------------------------------------------------
# Orbital decay lifetime
# --------------------------------------------------------------------------------------------

# The 25-year post-mission disposal guideline, long the international norm (IADC, and the FCC's
# stricter 5-year rule for US-licensed LEO since 2024). Reported as context, never as a
# compliance verdict -- that is a regulatory judgement this tool has no business making.
DISPOSAL_GUIDELINE_YEARS = 25.0


class DecayRequest(BaseModel):
    """Parameters for an orbital decay lifetime estimate.

    Give either a satellite to read the altitude from, or an altitude directly. The second form
    is what mission planning actually needs: "how low can we fly and still clear 25 years?" is a
    question about an orbit that does not exist yet.
    """

    norad_id: int | None = Field(default=None, ge=1)
    altitude_km: float | None = Field(default=None, gt=100, le=2000)
    # Bounds sized for real hardware, not just cubesats: the ISS is ~450 t and presents roughly
    # 1500 m^2. Caps tight enough to exclude it would reject the most interesting question the
    # endpoint can answer.
    mass_kg: float = Field(default=3.3, gt=0, le=1_000_000)
    cross_section_m2: float = Field(default=0.03, gt=0, le=10_000)
    drag_coefficient: float = Field(
        default=2.2,
        gt=0,
        le=5,
        description="2.2 is the standard value for a compact body in free-molecular flow.",
    )


@app.post("/api/decay")
def decay_lifetime(request: DecayRequest) -> dict[str, Any]:
    """Estimate how long an orbit survives atmospheric drag, as a range.

    This is the endpoint where the uncertainty *is* the answer. Lifetime depends on atmospheric
    density, density depends on solar activity, and solar activity cannot be forecast years
    ahead. A single figure would read as a result while being a guess; the bracket across weak,
    average and strong solar activity is what the physics actually supports.

    Args:
        request: Satellite or altitude, plus the spacecraft's drag properties.

    Returns:
        Shortest, nominal and longest lifetimes with provenance, the spread between them, and
        where the estimate sits relative to the 25-year disposal guideline.

    Raises:
        ValueError: If neither or both of `norad_id` and `altitude_km` are given.
    """
    if (request.norad_id is None) == (request.altitude_km is None):
        return _error(
            "invalid_request",
            "Provide exactly one of norad_id or altitude_km.",
            status=422,
        )

    satellite_info: dict[str, Any] | None = None
    if request.norad_id is not None:
        tle = catalog.get_tle(request.norad_id)
        properties = elements.derived_orbit_properties(tle, orbit.tle_epoch(tle))

        # Mean of apogee and perigee: the simplified model is circular, so feeding it a single
        # representative altitude is the honest reduction. Using perigee alone would overstate
        # drag; apogee alone would understate it.
        apogee_km = properties["apogee_altitude"].value / 1000.0
        perigee_km = properties["perigee_altitude"].value / 1000.0
        altitude_km = (apogee_km + perigee_km) / 2.0

        satellite_info = {
            "norad_id": tle.norad_id,
            "name": tle.name,
            "apogee_altitude_km": round(apogee_km, 3),
            "perigee_altitude_km": round(perigee_km, 3),
        }
    else:
        altitude_km = float(request.altitude_km)  # type: ignore[arg-type]

    ballistic_term = request.drag_coefficient * request.cross_section_m2 / request.mass_kg
    estimates = decay.lifetime_range(altitude_km, ballistic_term)

    shortest = estimates["shortest"].value
    longest = estimates["longest"].value

    return {
        "satellite": satellite_info,
        "altitude_km": round(altitude_km, 3),
        "spacecraft": {
            "mass_kg": request.mass_kg,
            "cross_section_m2": request.cross_section_m2,
            "drag_coefficient": request.drag_coefficient,
            "ballistic_term_m2_per_kg": round(ballistic_term, 6),
        },
        "lifetime": estimates,
        "spread_factor": round(longest / shortest, 2) if shortest > 0 else None,
        "disposal_guideline": {
            "years": DISPOSAL_GUIDELINE_YEARS,
            "met_under_every_scenario": bool(longest <= DISPOSAL_GUIDELINE_YEARS),
            "met_under_no_scenario": bool(shortest > DISPOSAL_GUIDELINE_YEARS),
            "note": (
                "Context, not a compliance finding. Whether a mission complies depends on its "
                "licensing regime, its disposal plan, and hardware this tool knows nothing "
                "about. US-licensed LEO missions face a stricter 5-year rule."
            ),
        },
        "assumptions": (
            "Circular orbit at a single representative altitude, constant ballistic "
            "coefficient, orbit-averaged atmosphere. No manoeuvres, no attitude changes, no "
            "geomagnetic storms, no eccentricity decay."
        ),
        "notice": OPERATIONAL_NOTICE,
    }


# --------------------------------------------------------------------------------------------
# Literature and citation verification
# --------------------------------------------------------------------------------------------


class LiteratureSearchRequest(BaseModel):
    """Parameters for a literature search."""

    query: str = Field(min_length=3, max_length=400)
    max_results: int = Field(default=8, ge=1, le=literature.MAX_RESULTS_CAP)


@app.post("/api/literature/search")
def literature_search(request: LiteratureSearchRequest) -> dict[str, Any]:
    """Search the scientific literature and return records with provenance.

    This exists so the agent has a way to cite that does not involve remembering. Everything it
    returns was fetched; nothing was recalled.

    Args:
        request: Search terms and result count.

    Returns:
        Matching records, each also expressed as a tiered value carrying its receipt.
    """
    papers = literature.search(request.query, max_results=request.max_results)

    return {
        "query": request.query,
        "count": len(papers),
        "papers": [literature.as_dict(paper) for paper in papers],
        "citations": [literature.citation_value(paper) for paper in papers],
        "source": "arXiv",
        "caveat": (
            "arXiv is a preprint server. A record existing means the paper exists, not that it "
            "is peer-reviewed or correct. Records carrying a DOI or journal reference have a "
            "published version; the absence of one is not evidence of rejection."
        ),
        "notice": OPERATIONAL_NOTICE,
    }


class CitationVerifyRequest(BaseModel):
    """Identifiers to check against the source of record."""

    arxiv_ids: list[str] = Field(min_length=1, max_length=20)


@app.post("/api/literature/verify")
def literature_verify(request: CitationVerifyRequest) -> dict[str, Any]:
    """Check whether claimed citations actually exist.

    A fabricated reference is indistinguishable from a real one by inspection: the authors look
    right, the title sounds right, the identifier is well-formed. The only thing that separates
    them is whether it resolves. This route is that check, exposed so an answer's citations can
    be audited rather than trusted.

    Args:
        request: Claimed identifiers.

    Returns:
        A verdict per identifier, plus a summary count.
    """
    outcome = literature.verify_citations(request.arxiv_ids)

    return {
        **outcome,
        "interpretation": (
            "'verified' resolved to a real record. 'not_found' is well-formed but resolves to "
            "nothing - treat as fabricated unless shown otherwise. 'malformed' is not an arXiv "
            "identifier at all. 'unchecked' means the service could not be reached, which is "
            "not evidence either way."
        ),
        "notice": OPERATIONAL_NOTICE,
    }

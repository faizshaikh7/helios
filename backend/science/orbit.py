"""Orbit propagation, ground tracks, and ground-station access windows.

Built on skyfield/sgp4. Every quantity leaving this module carries a unit, a reference frame
where one applies, a time scale, and a trust tier -- see `science.provenance`.

Trust tiers used here:

* A TLE's own epoch and the station coordinates a user supplies are **observed**.
* A position at the element set's own epoch is **derived**.
* Any position or pass at a different time is **predicted**: SGP4 is a model being propagated,
  and its error grows with time from epoch (roughly a kilometre per day for LEO, and worse
  during high solar activity). Calling a future pass "derived" would overstate it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from skyfield.api import EarthSatellite, load, wgs84

from science.catalog import TLE
from science.provenance import Receipt, Tier, Value

# Built-in timescale data: skyfield would otherwise fetch leap-second and delta-T tables over
# the network, which would make results depend on when the process happened to start.
TIMESCALE = load.timescale()

# SGP4 error grows with age of the element set. Published rule of thumb for LEO, used to state
# an honest uncertainty rather than implying the model is exact.
SGP4_ERROR_AT_EPOCH_KM = 1.0
SGP4_ERROR_GROWTH_KM_PER_DAY = 1.5


@dataclass(frozen=True)
class GroundStation:
    """An observing site on the Earth's surface.

    Attributes:
        name: Human label for the site.
        latitude_deg: Geodetic latitude, degrees north.
        longitude_deg: Longitude, degrees east.
        elevation_m: Height above the WGS84 ellipsoid, metres.
    """

    name: str
    latitude_deg: float
    longitude_deg: float
    elevation_m: float = 0.0


def build_satellite(tle: TLE) -> EarthSatellite:
    """Build a skyfield satellite from an element set."""
    return EarthSatellite(tle.line1, tle.line2, tle.name, TIMESCALE)


def dataset_record(tle: TLE) -> dict[str, object]:
    """Describe the element set for a receipt."""
    return {
        "source": tle.source,
        "norad_id": tle.norad_id,
        "name": tle.name,
        "epoch_utc": tle_epoch(tle).isoformat(),
        "fetched_at_unix": round(tle.fetched_at_unix, 3),
    }


def tle_epoch(tle: TLE) -> datetime:
    """Return the element set's own epoch, in UTC.

    Every prediction's accuracy is a function of distance from this instant, so it is reported
    alongside results rather than buried.

    Args:
        tle: The element set.

    Returns:
        Epoch as a timezone-aware UTC datetime.
    """
    return build_satellite(tle).epoch.utc_datetime()


def position_uncertainty(tle: TLE, when: datetime) -> dict[str, object]:
    """Estimate SGP4 position error at a given time.

    Stated as an interval derived from age since epoch rather than a confidence percentage. It
    is a rule-of-thumb magnitude, not a rigorous covariance -- and says so.

    Args:
        tle: The element set being propagated.
        when: The instant being evaluated.

    Returns:
        An uncertainty record for a receipt.
    """
    age_days = abs((when - tle_epoch(tle)).total_seconds()) / 86400.0
    estimate_km = SGP4_ERROR_AT_EPOCH_KM + SGP4_ERROR_GROWTH_KM_PER_DAY * age_days

    return {
        "position_error_km": round(estimate_km, 2),
        "age_from_epoch_days": round(age_days, 3),
        "basis": (
            "Rule-of-thumb SGP4 growth (~1 km at epoch, ~1.5 km/day for LEO). Not a covariance; "
            "actual error varies with solar activity and orbit regime."
        ),
    }


def subpoint(tle: TLE, when: datetime) -> dict[str, Value]:
    """Compute the sub-satellite point and altitude at an instant.

    Args:
        tle: Element set to propagate.
        when: Instant, timezone-aware UTC.

    Returns:
        Mapping of ``latitude``, ``longitude``, ``altitude`` to values with provenance.
    """
    satellite = build_satellite(tle)
    time_point = TIMESCALE.from_datetime(when)
    geodetic = wgs84.subpoint(satellite.at(time_point))

    at_epoch = abs((when - tle_epoch(tle)).total_seconds()) < 1.0
    tier = Tier.DERIVED if at_epoch else Tier.PREDICTED

    def build(name: str, quantity: float, unit: str) -> Value:
        return Value(
            value=round(quantity, 6),
            unit=unit,
            tier=tier,
            receipt=Receipt(
                tool="subpoint",
                inputs={"norad_id": tle.norad_id, "when_utc": when.isoformat()},
                frame="ITRF (WGS84 geodetic)",
                time_scale="UTC",
                dataset=dataset_record(tle),
                equation="SGP4 propagation (TEME), rotated to ITRF, reduced to WGS84 geodetic",
                uncertainty=position_uncertainty(tle, when),
                notes=f"{name} of the sub-satellite point.",
            ),
        )

    return {
        "latitude": build("Latitude", geodetic.latitude.degrees, "deg"),
        "longitude": build("Longitude", geodetic.longitude.degrees, "deg"),
        "altitude": build("Altitude", geodetic.elevation.km, "km"),
    }


def ground_track(
    tle: TLE,
    start: datetime,
    *,
    minutes: int = 100,
    step_seconds: int = 30,
) -> list[dict[str, float]]:
    """Sample the sub-satellite point over a time span.

    Returned as plain numbers rather than `Value` objects: a track is hundreds of points that
    share one provenance, so the receipt is attached once by the caller instead of repeated per
    sample. Roughly 100 minutes covers one LEO revolution.

    Args:
        tle: Element set to propagate.
        start: First sample instant, timezone-aware UTC.
        minutes: Span to cover.
        step_seconds: Sample spacing.

    Returns:
        A list of ``{t, lat, lon, alt_km}`` samples.
    """
    satellite = build_satellite(tle)
    samples: list[dict[str, float]] = []

    for offset in range(0, minutes * 60 + 1, step_seconds):
        moment = start + timedelta(seconds=offset)
        geodetic = wgs84.subpoint(satellite.at(TIMESCALE.from_datetime(moment)))
        samples.append(
            {
                "t": moment.isoformat(),
                "lat": round(geodetic.latitude.degrees, 4),
                "lon": round(geodetic.longitude.degrees, 4),
                "alt_km": round(geodetic.elevation.km, 3),
            }
        )

    return samples


@dataclass(frozen=True)
class Pass:
    """A single access window between a satellite and a ground station.

    Attributes:
        rise_utc: When the satellite crosses the elevation mask on the way up.
        culmination_utc: When it reaches maximum elevation.
        set_utc: When it drops back below the mask.
        max_elevation_deg: Elevation at culmination.
        duration_s: Seconds between rise and set.
    """

    rise_utc: datetime
    culmination_utc: datetime
    set_utc: datetime
    max_elevation_deg: float
    duration_s: float


def find_passes(
    tle: TLE,
    station: GroundStation,
    start: datetime,
    *,
    days: float = 1.0,
    min_elevation_deg: float = 10.0,
) -> list[Pass]:
    """Find access windows between a satellite and a ground station.

    ``min_elevation_deg`` is the elevation mask: the angle above the horizon below which the
    station cannot usefully work the satellite, because of terrain, buildings, and the fact that
    signal path length through the atmosphere grows sharply near the horizon. Ten degrees is a
    common default for amateur and cubesat stations; a mask of zero would report passes that
    cannot actually be worked.

    Partial windows at the boundaries are discarded: skyfield reports whatever events fall in the
    span, so a pass already underway at ``start`` yields a set without a rise. Reporting that as
    a complete pass would misstate both its duration and its start.

    Args:
        tle: Element set to propagate.
        station: Observing site.
        start: Beginning of the search window, timezone-aware UTC.
        days: Length of the search window.
        min_elevation_deg: Elevation mask in degrees.

    Returns:
        Complete passes, in chronological order.
    """
    satellite = build_satellite(tle)
    site = wgs84.latlon(station.latitude_deg, station.longitude_deg, station.elevation_m)

    t0 = TIMESCALE.from_datetime(start)
    t1 = TIMESCALE.from_datetime(start + timedelta(days=days))

    times, codes = satellite.find_events(site, t0, t1, altitude_degrees=min_elevation_deg)

    passes: list[Pass] = []
    pending: dict[int, datetime] = {}

    for time_point, code in zip(times, codes, strict=True):
        moment = time_point.utc_datetime()

        if code == 0:  # rise
            pending = {0: moment}
        elif code == 1 and 0 in pending:  # culmination
            pending[1] = moment
        elif code == 2 and 0 in pending and 1 in pending:  # set
            culmination = pending[1]
            elevation = (
                (satellite - site).at(TIMESCALE.from_datetime(culmination)).altaz()[0].degrees
            )
            passes.append(
                Pass(
                    rise_utc=pending[0],
                    culmination_utc=culmination,
                    set_utc=moment,
                    max_elevation_deg=round(float(elevation), 3),
                    duration_s=round((moment - pending[0]).total_seconds(), 1),
                )
            )
            pending = {}

    return passes


def pass_receipt(
    tle: TLE, station: GroundStation, min_elevation_deg: float, when: datetime
) -> Receipt:
    """Build the provenance record shared by a set of predicted passes.

    Args:
        tle: Element set used.
        station: Observing site used.
        min_elevation_deg: Elevation mask applied.
        when: Start of the prediction window, used to age the uncertainty estimate.

    Returns:
        A receipt describing how the passes were produced.
    """
    return Receipt(
        tool="find_passes",
        inputs={
            "norad_id": tle.norad_id,
            "station": {
                "name": station.name,
                "latitude_deg": station.latitude_deg,
                "longitude_deg": station.longitude_deg,
                "elevation_m": station.elevation_m,
            },
            "min_elevation_deg": min_elevation_deg,
            "from_utc": when.isoformat(),
        },
        frame="ITRF (topocentric alt/az from WGS84 site)",
        time_scale="UTC",
        dataset=dataset_record(tle),
        equation="SGP4 propagation; geometric elevation above the WGS84 ellipsoid",
        uncertainty=position_uncertainty(tle, when),
        notes=(
            "Geometric visibility only. Ignores terrain, local obstructions, atmospheric "
            "refraction, and link budget -- a geometrically visible pass is not necessarily a "
            "workable one."
        ),
    )

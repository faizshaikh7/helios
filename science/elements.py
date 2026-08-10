"""State vectors, classical orbital elements, and the orbit properties derived from them.

Built on skyfield (which delegates the propagation itself to `sgp4`). Every quantity leaving this
module carries a unit, a reference frame where one applies, a time scale, and a trust tier -- see
`science.provenance`.

**Frame.** Everything here is expressed in **TEME**, the frame SGP4 natively produces. Elements
computed from a TEME state are TEME-referenced elements, which is the convention a TLE's own
inclination and right ascension follow. Converting to GCRF first would move the inclination and
RAAN by the precession-nutation angle -- roughly 0.3 degrees at present -- so the frame is stated
on every value rather than assumed. Frame conversion itself is graded by `tests/test_frames.py`.

**Osculating, not mean.** These are *osculating* elements: the instantaneous two-body ellipse that
matches the state vector at that moment. A TLE's own printed elements are *mean* elements in the
Brouwer-Lyddane sense, with short-period terms removed, so the two differ slightly and legitimately
-- for the ISS, about 0.02 degrees of inclination and 0.02 rev/day of mean motion. Reporting an
osculating value as if it were the TLE's mean value, or vice versa, is a real error; reporting a
small difference between them is not.

**Trust tiers.** At the element set's own epoch a state is *derived*: accepted physics applied to
published data. At any other instant SGP4 is being propagated and every value is *predicted*, since
model error grows with time from epoch. Provenance is monotonic, so everything computed downstream
of a propagated state stays predicted no matter how exact the arithmetic in between.
"""

from __future__ import annotations

import math
from datetime import datetime

import numpy as np
from sgp4.earth_gravity import wgs72
from skyfield.api import wgs84
from skyfield.elementslib import OsculatingElements, osculating_elements_of
from skyfield.framelib import itrs
from skyfield.positionlib import ICRF
from skyfield.sgp4lib import TEME
from skyfield.timelib import Time
from skyfield.units import Distance, Velocity

from science.catalog import TLE

# Reused rather than duplicated: the element-set descriptor, the SGP4 uncertainty model, and the
# skyfield handles are one concept each and belong in one place. Duplicating them here would let
# the two modules drift into disagreeing about the same TLE.
from science.orbit import (
    TIMESCALE,
    build_satellite,
    dataset_record,
    position_uncertainty,
    tle_epoch,
)
from science.provenance import Receipt, Tier, Value

# SGP4 is defined against the WGS72 gravity model, so the state vectors converted below were
# produced under WGS72's gravitational parameter. Re-deriving elements with WGS84's mu would mix
# two models -- shifting the semi-major axis by several metres for no reason -- so the propagator's
# own constant is used. Read from the sgp4 package rather than typed in, per rules.md: never invent
# a physical constant.
EARTH_MU_KM3_S2 = wgs72.mu
EARTH_MU_M3_S2 = wgs72.mu * 1e9

# Geocentric identifier skyfield uses for "centre of the Earth". Required before the WGS84 geoid
# will accept a position for reduction to geodetic coordinates.
EARTH_CENTER = 399

# How close to the element set's own epoch still counts as "at epoch" rather than propagated.
# Matches science.orbit so the two modules cannot disagree about when a value stops being derived.
EPOCH_TOLERANCE_S = 1.0

FRAME_TEME = "TEME (true equator, mean equinox -- SGP4's native frame)"
FRAME_TEME_WGS84 = "TEME positions, heights above the WGS84 ellipsoid"

_MODEL_NOTE = (
    "Osculating (instantaneous two-body) elements from an SGP4 state, under the WGS72 "
    f"gravitational parameter mu = {EARTH_MU_M3_S2:.6e} m^3/s^2. These are not the TLE's own "
    "mean elements; short-period terms make the two differ slightly."
)


def _tier(tle: TLE, when: datetime) -> Tier:
    """Classify how much weight a value computed at ``when`` can bear.

    At the element set's own epoch nothing has been propagated, so the result is derived from
    published data by accepted physics. Anywhere else, SGP4 is extrapolating and the honest label
    is predicted.

    Args:
        tle: The element set being used.
        when: The instant being evaluated.

    Returns:
        ``DERIVED`` at epoch, otherwise ``PREDICTED``.
    """
    at_epoch = abs((when - tle_epoch(tle)).total_seconds()) < EPOCH_TOLERANCE_S
    return Tier.DERIVED if at_epoch else Tier.PREDICTED


def _receipt(
    tool: str, tle: TLE, when: datetime, *, frame: str, equation: str, notes: str
) -> Receipt:
    """Build the provenance record shared by the values one call produces.

    Args:
        tool: Public function that produced the value.
        tle: Element set used.
        when: Instant evaluated.
        frame: Reference frame the value is expressed in.
        equation: Relation or model applied.
        notes: Caveats a reader needs in order to judge the number.

    Returns:
        A receipt describing how the value was produced.
    """
    return Receipt(
        tool=tool,
        inputs={"norad_id": tle.norad_id, "when_utc": when.isoformat()},
        frame=frame,
        time_scale="UTC",
        dataset=dataset_record(tle),
        equation=equation,
        uncertainty=position_uncertainty(tle, when),
        notes=notes,
    )


def _teme_state(tle: TLE, when: datetime) -> tuple[np.ndarray, np.ndarray, Time]:
    """Propagate an element set and return its TEME state.

    ``frame_xyz_and_velocity(TEME)`` is used rather than skyfield's default geocentric output
    because SGP4 emits TEME and anything else would fold a frame rotation into a propagation
    result. Agreement with Orekit's TEME state is measured in `tests/test_propagation.py`.

    Args:
        tle: Element set to propagate.
        when: Instant, timezone-aware UTC.

    Returns:
        Position in metres, velocity in metres per second, and the skyfield time used.
    """
    time_point = TIMESCALE.from_datetime(when)
    distance, velocity = build_satellite(tle).at(time_point).frame_xyz_and_velocity(TEME)
    return distance.km * 1e3, velocity.km_per_s * 1e3, time_point


def _osculating(tle: TLE, when: datetime) -> tuple[OsculatingElements, Time]:
    """Convert a propagated TEME state into osculating orbital elements.

    The state-vector-to-elements conversion is skyfield's, not this module's: it is exactly the
    kind of standard reduction rules.md forbids reimplementing. ``gm_km3_s2`` is passed explicitly
    so the conversion uses SGP4's own WGS72 constant instead of skyfield's default Earth GM, which
    comes from a different model.

    Args:
        tle: Element set to propagate.
        when: Instant, timezone-aware UTC.

    Returns:
        The osculating elements and the skyfield time used.
    """
    time_point = TIMESCALE.from_datetime(when)
    distance, velocity = build_satellite(tle).at(time_point).frame_xyz_and_velocity(TEME)

    state = ICRF(distance.au, velocity.au_per_d, t=time_point, center=EARTH_CENTER)
    return osculating_elements_of(state, gm_km3_s2=EARTH_MU_KM3_S2), time_point


def _apsis_latitude_deg(inclination_deg: float, argument_of_latitude_deg: float) -> float:
    """Geocentric latitude at which a point on the orbit sits.

    For any Keplerian orbit ``sin(declination) = sin(inclination) * sin(argument of latitude)``,
    where the argument of latitude is measured from the ascending node. This is needed because the
    WGS84 ellipsoid's radius varies by 21 km between equator and pole, so an apsis altitude cannot
    be computed without knowing where the apsis actually occurs.

    Args:
        inclination_deg: Orbit inclination, degrees.
        argument_of_latitude_deg: Angle from ascending node to the point, degrees.

    Returns:
        Geocentric latitude, degrees.
    """
    sine = math.sin(math.radians(inclination_deg)) * math.sin(math.radians(argument_of_latitude_deg))
    return math.degrees(math.asin(max(-1.0, min(1.0, sine))))


def _geodetic_height_m(radius_m: float, geocentric_latitude_deg: float, time_point: Time) -> float:
    """Height of a point above the WGS84 ellipsoid surface.

    The ellipsoid is a surface of revolution, so height depends only on radius and latitude --
    longitude is therefore fixed at zero and the answer is unaffected. The point is constructed
    directly in ITRS and handed to skyfield's geoid so that skyfield performs the geodetic
    reduction (which is iterative, and not the same as ``radius - ellipsoid radius`` at that
    latitude). Building the point in Earth-fixed coordinates also keeps the result independent of
    the epoch, which is what makes fixing the longitude legitimate.

    Args:
        radius_m: Distance from Earth's centre, metres.
        geocentric_latitude_deg: Geocentric latitude of the point, degrees.
        time_point: Any epoch; the frame round-trip is exact, so it does not affect the height.

    Returns:
        Height above the WGS84 ellipsoid, metres.
    """
    latitude = math.radians(geocentric_latitude_deg)
    xyz_km = np.array([math.cos(latitude), 0.0, math.sin(latitude)]) * radius_m / 1e3

    point = ICRF.from_time_and_frame_vectors(
        time_point, itrs, Distance(km=xyz_km), Velocity(km_per_s=np.zeros(3))
    )
    point.center = EARTH_CENTER

    return float(wgs84.geographic_position_of(point).elevation.m)


def state_vector(tle: TLE, when: datetime) -> dict[str, Value]:
    """Compute the satellite's position and velocity at an instant.

    Both are expressed in **TEME**, in metres and metres per second. TEME is SGP4's native output
    frame; reporting it as though it were GCRF or ITRF displaces a low-Earth-orbit satellite by
    tens of kilometres, which is why the frame travels with every value.

    ``radius`` and ``speed`` are the magnitudes of the two vectors. They are frame-independent
    (a frame change is a rotation) and are included because they are what most sanity checks
    actually want.

    Args:
        tle: Element set to propagate.
        when: Instant, timezone-aware UTC.

    Returns:
        Mapping of ``position_x``/``position_y``/``position_z`` (m),
        ``velocity_x``/``velocity_y``/``velocity_z`` (m/s), ``radius`` (m) and ``speed`` (m/s)
        to values with provenance.
    """
    position_m, velocity_m_s, _ = _teme_state(tle, when)
    tier = _tier(tle, when)

    receipt = _receipt(
        "state_vector",
        tle,
        when,
        frame=FRAME_TEME,
        equation="SGP4 propagation of the element set; state read in TEME",
        notes=(
            "Position and velocity are TEME, not GCRF and not Earth-fixed. Treating TEME as GCRF "
            "displaces a LEO satellite by roughly 44 km."
        ),
    )

    def build(quantity: float, unit: str) -> Value:
        return Value(value=round(float(quantity), 6), unit=unit, tier=tier, receipt=receipt)

    return {
        "position_x": build(position_m[0], "m"),
        "position_y": build(position_m[1], "m"),
        "position_z": build(position_m[2], "m"),
        "velocity_x": build(velocity_m_s[0], "m/s"),
        "velocity_y": build(velocity_m_s[1], "m/s"),
        "velocity_z": build(velocity_m_s[2], "m/s"),
        "radius": build(float(np.linalg.norm(position_m)), "m"),
        "speed": build(float(np.linalg.norm(velocity_m_s)), "m/s"),
    }


def classical_elements(tle: TLE, when: datetime) -> dict[str, Value]:
    """Compute the classical (Keplerian) orbital elements at an instant.

    The six elements are the osculating two-body ellipse matching the TEME state vector at
    ``when`` -- see the module docstring on osculating versus mean elements, which is the
    difference most often reported wrongly.

    ``semi_major_axis`` is measured **from Earth's centre**, not from the surface: for the ISS it
    is about 6790 km, not about 420 km. Angles are in degrees and referred to TEME, so the
    inclination and right ascension are directly comparable with the TLE's own fields and are
    *not* comparable with J2000 or GCRF values without a frame rotation.

    Args:
        tle: Element set to propagate.
        when: Instant, timezone-aware UTC.

    Returns:
        Mapping of ``semi_major_axis`` (m), ``eccentricity`` (dimensionless), ``inclination``
        (deg), ``raan`` (deg), ``argument_of_perigee`` (deg) and ``true_anomaly`` (deg) to values
        with provenance.
    """
    elements, _ = _osculating(tle, when)
    tier = _tier(tle, when)

    receipt = _receipt(
        "classical_elements",
        tle,
        when,
        frame=FRAME_TEME,
        equation="SGP4 propagation to a TEME state, reduced to osculating Keplerian elements",
        notes=_MODEL_NOTE,
    )

    def build(quantity: float, unit: str, digits: int) -> Value:
        return Value(value=round(float(quantity), digits), unit=unit, tier=tier, receipt=receipt)

    return {
        "semi_major_axis": build(elements.semi_major_axis.m, "m", 6),
        "eccentricity": build(elements.eccentricity, "none", 12),
        "inclination": build(elements.inclination.degrees, "deg", 9),
        "raan": build(elements.longitude_of_ascending_node.degrees, "deg", 9),
        "argument_of_perigee": build(elements.argument_of_periapsis.degrees, "deg", 9),
        "true_anomaly": build(elements.true_anomaly.degrees, "deg", 9),
    }


def derived_orbit_properties(tle: TLE, when: datetime) -> dict[str, Value]:
    """Compute orbital period, apsis altitudes, and mean motion at an instant.

    **Datum.** ``apogee_altitude`` and ``perigee_altitude`` are heights above the **WGS84
    ellipsoid surface** -- not radii from Earth's centre, and not heights above a spherical mean
    radius. This is the classic silent error in orbital software: for the ISS the correct perigee
    altitude is about 417 km, while the apsis *radius* is about 6784 km and "radius minus a
    spherical 6371 km" gives about 413 km. All three look like plausible numbers.

    Each apsis is reduced at the latitude where it actually occurs, recovered from the orbit
    geometry (see `_apsis_latitude_deg`). The ellipsoid is 21 km smaller in radius at the poles
    than at the equator, so using the equatorial radius everywhere would understate the ISS
    perigee altitude by roughly 11 km.

    ``mean_motion`` is in revolutions per day, matching the TLE's own field, and is the osculating
    mean motion rather than the TLE's Brouwer mean value.

    Args:
        tle: Element set to propagate.
        when: Instant, timezone-aware UTC.

    Returns:
        Mapping of ``orbital_period`` (s), ``apogee_altitude`` (m), ``perigee_altitude`` (m) and
        ``mean_motion`` (rev/day) to values with provenance.
    """
    elements, time_point = _osculating(tle, when)
    tier = _tier(tle, when)

    inclination_deg = float(elements.inclination.degrees)
    argument_of_perigee_deg = float(elements.argument_of_periapsis.degrees)

    perigee_altitude_m = _geodetic_height_m(
        float(elements.periapsis_distance.m),
        _apsis_latitude_deg(inclination_deg, argument_of_perigee_deg),
        time_point,
    )
    apogee_altitude_m = _geodetic_height_m(
        float(elements.apoapsis_distance.m),
        _apsis_latitude_deg(inclination_deg, argument_of_perigee_deg + 180.0),
        time_point,
    )

    period_receipt = _receipt(
        "derived_orbit_properties",
        tle,
        when,
        frame=FRAME_TEME,
        equation="Keplerian period and mean motion of the osculating orbit",
        notes=_MODEL_NOTE,
    )
    altitude_receipt = _receipt(
        "derived_orbit_properties",
        tle,
        when,
        frame=FRAME_TEME_WGS84,
        equation=(
            "Apsis radius a(1 +/- e), reduced to a geodetic height above the WGS84 ellipsoid at "
            "the apsis latitude sin(dec) = sin(i) * sin(argument of latitude)"
        ),
        notes=(
            _MODEL_NOTE
            + " Altitudes are heights above the WGS84 ellipsoid surface, not radii from Earth's "
            "centre and not heights above a mean spherical radius."
        ),
    )

    return {
        "orbital_period": Value(
            value=round(float(elements.period_in_days) * 86400.0, 6),
            unit="s",
            tier=tier,
            receipt=period_receipt,
        ),
        "mean_motion": Value(
            value=round(float(elements.mean_motion_per_day.degrees) / 360.0, 9),
            unit="rev/day",
            tier=tier,
            receipt=period_receipt,
        ),
        "apogee_altitude": Value(
            value=round(apogee_altitude_m, 6), unit="m", tier=tier, receipt=altitude_receipt
        ),
        "perigee_altitude": Value(
            value=round(perigee_altitude_m, 6), unit="m", tier=tier, receipt=altitude_receipt
        ),
    }

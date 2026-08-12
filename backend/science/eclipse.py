"""Eclipse geometry and beta angle: when a satellite loses the Sun, and for how long.

These two quantities size the power system. A spacecraft in sunlight charges; a spacecraft in
Earth's shadow discharges. The **longest** eclipse over a mission season sets the battery
capacity and the depth of discharge the cells must survive tens of thousands of times, and the
eclipse fraction sets how much array area is needed to recharge in the sunlit remainder of each
orbit. Get the maximum eclipse duration wrong by two minutes and the battery is undersized for
the life of the mission.

**Beta angle** is the angle between the orbit plane and the Earth-Sun vector. It is the single
parameter that governs all of this: at low beta the orbit plane contains the Sun line, the
satellite passes squarely behind the Earth, and eclipses are longest. As |beta| rises the chord
through the shadow shortens, and past a critical value that depends only on orbit altitude the
orbit clears the shadow entirely and the satellite is in continuous sunlight -- a *full-sun*
season, which is a thermal problem rather than a power one. Beta also sets the Sun incidence
angle on the arrays and on the radiators, so it drives the thermal design as much as the
electrical one.

Frames, time scales, and tiers
------------------------------
Positions are compared in **GCRS** (geocentric ICRF). skyfield propagates the TLE with SGP4,
whose native frame is TEME, and rotates the result into the ICRF; the Sun is obtained in GCRS.
Both sides of every angle in this module are therefore expressed in the same inertial frame,
which is the point -- a TEME-vs-GCRS mix-up would tilt the orbit plane by around a degree at a
2024 epoch and quietly corrupt every beta angle.

Trust tiers follow `science.orbit`: beta angle **at the element set's own epoch** is DERIVED,
because it is accepted geometry applied to published elements. Anything propagated away from
epoch -- which every eclipse interval is, since a search span necessarily leaves the epoch --
is PREDICTED.

Sun ephemeris
-------------
`skyfield`'s planetary ephemerides (`load('de421.bsp')` and friends) are **not** used here: they
are not bundled with the package and `load` would fetch a 17 MB kernel over the network on first
use. `*.bsp` is gitignored in this repository, so the file could not be committed either, and CI
must stay offline and deterministic (see `.agent/test.md`). Instead the Sun's geocentric position
comes from `astropy.coordinates.get_sun`, which evaluates the ERFA implementation of a simplified
VSOP2000 with **no download at all** -- astropy states it as good to about 4 km in the Sun-Earth
vector over 1900-2100. Four kilometres at one astronomical unit is an angular error near 0.006
arcseconds; Earth subtends roughly 70 degrees from low Earth orbit, so this contributes nothing
measurable to an eclipse boundary time.

Shadow model
------------
A **conical** shadow model is used, not a cylindrical one, so umbra and penumbra are
distinguished. Skyfield's own `is_sunlit` is not used for two reasons: it requires the planetary
ephemeris kernel above, and it reports a single boolean, which would merge the penumbra into
either full sun or full shadow. That distinction is exactly what a power budget needs -- in the
penumbra the array still produces, tapering rather than dropping to zero, so treating penumbra as
umbra overstates the discharge and treating it as sunlight understates it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
from astropy import units as u
from astropy.constants import R_sun
from astropy.coordinates import get_sun
from astropy.time import Time
from astropy.utils import iers
from skyfield.api import EarthSatellite
from skyfield.constants import ERAD
from skyfield.searchlib import find_discrete
from skyfield.timelib import Time as SkyfieldTime

from science import orbit
from science.catalog import TLE
from science.provenance import Receipt, Tier, Value

# Never reach for the network: astropy would otherwise fetch IERS-A mid-computation, making
# results depend on when the process happened to run. Its bundled IERS-B table is sufficient --
# eclipse geometry is inertial, so Earth-orientation data does not enter it at all.
iers.conf.auto_download = False

# Earth's equatorial radius as skyfield defines it, in km. A sphere of the *equatorial* radius is
# the conservative choice: it is the largest the shadow can be, so eclipse durations are never
# understated. Oblateness would shrink the shadow slightly at high latitudes -- an effect of
# order a second on entry and exit times, well inside the SGP4 uncertainty reported alongside.
EARTH_RADIUS_KM = ERAD / 1e3

# IAU 2015 Resolution B3 nominal solar radius, in km. The Sun is an extended source, which is the
# only reason a penumbra exists at all.
SUN_RADIUS_KM = float(R_sun.to_value(u.km))

# Coarse sampling interval for the shadow search, in minutes. It must be short enough that no
# eclipse can hide entirely between two samples: the shortest real eclipse of interest is a
# geostationary one at the edge of an equinox season, a few minutes long, and low-Earth-orbit
# eclipses run 20-40 minutes. Four minutes brackets both with margin; skyfield then bisects each
# bracket to millisecond precision.
SEARCH_STEP_MINUTES = 4.0

# Upper bound on a search span. Eclipse seasons are what callers actually want, and a month
# covers one; the bound exists so an API caller cannot ask for a year of second-precision
# root-finding and stall the service.
MAX_SEARCH_DAYS = 31.0


@dataclass(frozen=True)
class Eclipse:
    """One passage through the Earth's shadow.

    Entry and exit bracket the *penumbra*, the first and last instants at which any part of the
    Sun is occulted, because that is where the array output starts and stops changing. The umbra
    fields are the fully-shadowed core, and are ``None`` for a grazing eclipse that never reaches
    totality -- which happens at the start and end of an eclipse season and is a real case, not a
    degenerate one.

    Attributes:
        entry_utc: First loss of full sunlight (penumbra entry).
        exit_utc: Return to full sunlight (penumbra exit).
        umbra_entry_utc: Start of total shadow, or None if the eclipse never reaches totality.
        umbra_exit_utc: End of total shadow, or None.
        duration_s: Seconds between ``entry_utc`` and ``exit_utc`` -- total time not in full Sun.
        umbra_duration_s: Seconds in total shadow; zero for a grazing eclipse.
        penumbra_duration_s: Seconds in partial shadow, summed over both edges.
        orbit_fraction: ``duration_s`` as a fraction of the orbital period. This is the number a
            power budget consumes: it is the share of each revolution spent discharging.
    """

    entry_utc: datetime
    exit_utc: datetime
    umbra_entry_utc: datetime | None
    umbra_exit_utc: datetime | None
    duration_s: float
    umbra_duration_s: float
    penumbra_duration_s: float
    orbit_fraction: float


def _sun_gcrs_km(when: SkyfieldTime) -> np.ndarray:
    """Return the Sun's geocentric position in GCRS, in km.

    See the module docstring for why this uses astropy's built-in ERFA ephemeris rather than a
    downloaded JPL kernel, and why its accuracy is irrelevant at this scale.

    Args:
        when: Instant or array of instants, as a skyfield time.

    Returns:
        A ``(3, n)`` array of GCRS components in km, with one column per instant.
    """
    epoch = Time(when.tt, format="jd", scale="tt")
    return np.reshape(get_sun(epoch).cartesian.xyz.to_value(u.km), (3, -1))


def _shadow_angles(
    satellite: EarthSatellite, when: SkyfieldTime
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute the three angles that decide whether a satellite is in shadow.

    The conical shadow test is done in the satellite's sky rather than in Cartesian space, which
    is what makes umbra and penumbra fall out of the same expression. Seen from the satellite,
    the Earth is a disc of angular radius ``theta_earth`` and the Sun a disc of angular radius
    ``theta_sun``, separated by ``separation``:

    * ``separation >= theta_earth + theta_sun`` -- the discs are apart, full sunlight.
    * ``theta_earth - theta_sun < separation < theta_earth + theta_sun`` -- partial overlap,
      penumbra.
    * ``separation <= theta_earth - theta_sun`` -- the Sun is wholly hidden, umbra.

    Args:
        satellite: A skyfield ``EarthSatellite``.
        when: Instant or array of instants.

    Returns:
        ``(separation, theta_earth, theta_sun)``, each an array of radians.
    """
    position_km = np.reshape(satellite.at(when).position.km, (3, -1))
    sun_km = _sun_gcrs_km(when)

    radius_km = np.linalg.norm(position_km, axis=0)
    to_sun_km = sun_km - position_km
    distance_to_sun_km = np.linalg.norm(to_sun_km, axis=0)

    theta_earth = np.arcsin(np.clip(EARTH_RADIUS_KM / radius_km, -1.0, 1.0))
    theta_sun = np.arcsin(np.clip(SUN_RADIUS_KM / distance_to_sun_km, -1.0, 1.0))

    # Angle at the satellite between the direction to the Earth's centre and the direction to the
    # Sun's centre.
    cosine = np.sum(-position_km * to_sun_km, axis=0) / (radius_km * distance_to_sun_km)
    separation = np.arccos(np.clip(cosine, -1.0, 1.0))

    return separation, theta_earth, theta_sun


def _orbital_period_s(satellite: EarthSatellite) -> float:
    """Return the orbital period implied by the element set, in seconds.

    Taken from the TLE's Kozai mean motion rather than from the propagated state, because that is
    the quantity the element set actually publishes; deriving a period from one instantaneous
    radius would confuse an osculating orbit for a mean one.

    Args:
        satellite: A skyfield ``EarthSatellite``.

    Returns:
        The mean orbital period in seconds.
    """
    revolutions_per_minute = satellite.model.no_kozai / (2.0 * np.pi)
    return 60.0 / revolutions_per_minute


def _validate_span(start: datetime, days: float) -> None:
    """Reject search spans that are empty, negative, or unboundedly long.

    Args:
        start: Beginning of the span.
        days: Length of the span.

    Raises:
        ValueError: If ``start`` is not timezone-aware, or ``days`` is outside (0, 31].
    """
    if start.tzinfo is None:
        raise ValueError("start must be timezone-aware; a naive datetime has no time scale")
    if not 0.0 < days <= MAX_SEARCH_DAYS:
        raise ValueError(f"days must be in (0, {MAX_SEARCH_DAYS}]; got {days}")


def _complete_intervals(
    satellite: EarthSatellite, start: datetime, days: float, *, umbra: bool
) -> list[tuple[datetime, datetime]]:
    """Find complete shadow intervals of one kind over a span.

    Two separate boolean searches are run rather than one three-valued search. A boolean changes
    exactly once inside a bracket that contains a crossing, so skyfield's bisection is guaranteed
    to converge on it; a three-valued function can step from sunlight straight to umbra within a
    single bracket and lose the penumbra edge entirely, because the penumbra at low Earth orbit
    lasts only about ten seconds.

    Intervals still open at either end of the span are discarded, matching
    `science.orbit.find_passes`: an eclipse already underway at ``start`` has no entry time, and
    reporting it as complete would misstate both its duration and its beginning.

    Args:
        satellite: A skyfield ``EarthSatellite``.
        start: Beginning of the span, timezone-aware UTC.
        days: Length of the span.
        umbra: Search for total shadow if True, for any shadow (penumbra included) if False.

    Returns:
        Complete ``(entry, exit)`` pairs as UTC datetimes, in chronological order.
    """

    def in_shadow(when: SkyfieldTime) -> np.ndarray:
        """Whether the satellite is inside the requested shadow region."""
        separation, theta_earth, theta_sun = _shadow_angles(satellite, when)
        if umbra:
            return separation < theta_earth - theta_sun
        return separation < theta_earth + theta_sun

    in_shadow.step_days = SEARCH_STEP_MINUTES / 1440.0

    timescale = orbit.TIMESCALE
    t0 = timescale.from_datetime(start)
    t1 = timescale.from_datetime(start + timedelta(days=days))

    times, flags = find_discrete(t0, t1, in_shadow)

    intervals: list[tuple[datetime, datetime]] = []
    entry: datetime | None = None

    for time_point, entering in zip(times, flags, strict=True):
        moment = time_point.utc_datetime()
        if entering:
            entry = moment
        elif entry is not None:
            intervals.append((entry, moment))
            entry = None

    return intervals


def beta_angle(tle: TLE, when: datetime) -> Value:
    """Compute the beta angle -- the angle between the orbit plane and the Sun.

    Defined as ``arcsin(n . s)`` where ``n`` is the unit orbit normal ``r x v`` and ``s`` the unit
    vector to the Sun, both in GCRS. The sign follows the orbit normal: beta is positive when the
    Sun lies on the side the angular momentum vector points to, which for a prograde orbit is the
    northern side. The sign is not cosmetic -- a power analyst uses it to tell which face of the
    spacecraft is being illuminated and which radiator is looking at deep space.

    Why it matters: beta sets eclipse duration, and eclipse duration sets battery capacity and
    depth of discharge. At beta near zero the satellite passes through the deepest part of the
    shadow and spends the largest share of each orbit discharging. Above a critical beta that
    depends only on altitude, the orbit misses the shadow completely and the satellite is in
    continuous sunlight, which removes the power problem and replaces it with a thermal one.
    Because a low-Earth orbit's node regresses several degrees a day, beta sweeps through its
    whole range over weeks, so a single value is a snapshot of a season, not a constant.

    Args:
        tle: Element set to propagate.
        when: Instant, timezone-aware UTC.

    Returns:
        Beta angle in degrees, in [-90, 90], with provenance. DERIVED at the element set's own
        epoch, PREDICTED anywhere else.

    Raises:
        ValueError: If ``when`` is not timezone-aware.
    """
    if when.tzinfo is None:
        raise ValueError("when must be timezone-aware; a naive datetime has no time scale")

    satellite = orbit.build_satellite(tle)
    time_point = orbit.TIMESCALE.from_datetime(when)
    state = satellite.at(time_point)

    normal = np.cross(state.position.km, state.velocity.km_per_s)
    normal = normal / np.linalg.norm(normal)

    sun = _sun_gcrs_km(time_point)[:, 0]
    sun = sun / np.linalg.norm(sun)

    beta_deg = float(np.degrees(np.arcsin(np.clip(np.dot(normal, sun), -1.0, 1.0))))

    at_epoch = abs((when - orbit.tle_epoch(tle)).total_seconds()) < 1.0
    tier = Tier.DERIVED if at_epoch else Tier.PREDICTED

    return Value(
        value=round(beta_deg, 6),
        unit="deg",
        tier=tier,
        receipt=Receipt(
            tool="beta_angle",
            inputs={"norad_id": tle.norad_id, "when_utc": when.isoformat()},
            frame="GCRS (geocentric ICRF); SGP4 TEME state rotated by skyfield",
            time_scale="UTC",
            dataset=dataset_record(tle),
            equation="beta = arcsin((r x v) . s), unit vectors, r and v from SGP4, s to the Sun",
            uncertainty=_timing_uncertainty(satellite, tle, when),
            notes=(
                "Positive when the Sun lies on the orbit-normal side of the plane. Sun position "
                "from astropy's built-in ERFA ephemeris (no download); its ~4 km error is "
                "negligible against a beta angle. Beta changes by several degrees per day at low "
                "Earth orbit because the node regresses, so treat this as a snapshot."
            ),
        ),
    )


def eclipse_intervals(tle: TLE, start: datetime, days: float = 1.0) -> list[Eclipse]:
    """Find every complete eclipse over a span, with umbra and penumbra boundaries.

    Returned as plain dataclasses rather than `Value` objects for the same reason a ground track
    is: a span of eclipses shares one provenance, so the receipt is attached once by the caller
    via `eclipse_receipt` instead of being repeated on every field. Every interval here is
    PREDICTED -- a search span necessarily leaves the element set's epoch.

    Args:
        tle: Element set to propagate.
        start: Beginning of the search span, timezone-aware UTC.
        days: Length of the span, in (0, 31].

    Returns:
        Complete eclipses in chronological order. Eclipses already underway at ``start`` or still
        underway at the end of the span are omitted.

    Raises:
        ValueError: If ``start`` is naive or ``days`` is outside (0, 31].
    """
    _validate_span(start, days)

    satellite = orbit.build_satellite(tle)
    period_s = _orbital_period_s(satellite)

    shadow_intervals = _complete_intervals(satellite, start, days, umbra=False)
    umbra_intervals = _complete_intervals(satellite, start, days, umbra=True)

    eclipses: list[Eclipse] = []
    for entry, exit_ in shadow_intervals:
        # The umbra is a sub-interval of the penumbra, so at most one umbra pair can start inside
        # this shadow interval. A grazing eclipse has none.
        nested = [pair for pair in umbra_intervals if entry <= pair[0] <= exit_]
        umbra_entry, umbra_exit = nested[0] if nested else (None, None)

        duration_s = (exit_ - entry).total_seconds()
        umbra_duration_s = (
            (umbra_exit - umbra_entry).total_seconds()
            if umbra_entry is not None and umbra_exit is not None
            else 0.0
        )

        eclipses.append(
            Eclipse(
                entry_utc=entry,
                exit_utc=exit_,
                umbra_entry_utc=umbra_entry,
                umbra_exit_utc=umbra_exit,
                duration_s=round(duration_s, 3),
                umbra_duration_s=round(umbra_duration_s, 3),
                penumbra_duration_s=round(duration_s - umbra_duration_s, 3),
                orbit_fraction=round(duration_s / period_s, 6),
            )
        )

    return eclipses


def eclipse_summary(tle: TLE, start: datetime, days: float = 1.0) -> dict[str, Value]:
    """Summarise eclipse exposure over a span, in the terms a power budget is written in.

    The number that sizes a battery is the **longest** eclipse in the span, not the average one:
    the battery has to carry the worst orbit, and every orbit in the mission is a charge/discharge
    cycle the cells must survive. The shortest eclipse and the count are reported alongside so a
    reader can see whether the span sits inside an eclipse season or is crossing into full sun,
    and the largest orbit fraction gives the share of a revolution spent discharging, which is
    what sets the array size needed to recover the charge in the sunlit remainder.

    Beta at both ends of the span is included because a summary without it is unreadable: two
    identical eclipse durations mean entirely different things depending on whether beta is
    heading towards zero (durations about to grow) or towards the critical angle (a full-sun
    season approaching).

    A span with no eclipses is a valid, useful answer -- it means full sun -- and is reported as a
    zero count with zero durations rather than an error.

    Args:
        tle: Element set to propagate.
        start: Beginning of the span, timezone-aware UTC.
        days: Length of the span, in (0, 31].

    Returns:
        Mapping of ``eclipse_count``, ``max_eclipse_duration``, ``min_eclipse_duration``,
        ``mean_eclipse_duration``, ``max_umbra_duration``, ``max_orbit_fraction``,
        ``beta_angle_at_start`` and ``beta_angle_at_end`` to values with provenance.

    Raises:
        ValueError: If ``start`` is naive or ``days`` is outside (0, 31].
    """
    _validate_span(start, days)

    eclipses = eclipse_intervals(tle, start, days)
    end = start + timedelta(days=days)

    durations = [item.duration_s for item in eclipses]
    umbra_durations = [item.umbra_duration_s for item in eclipses]
    fractions = [item.orbit_fraction for item in eclipses]

    satellite = orbit.build_satellite(tle)
    receipt = _summary_receipt(satellite, tle, start, days, len(eclipses))

    def build(quantity: float, unit: str) -> Value:
        """Wrap a summary statistic as a predicted value sharing the summary receipt."""
        return Value(
            value=round(float(quantity), 3),
            unit=unit,
            tier=Tier.PREDICTED,
            receipt=receipt,
        )

    mean_duration_s = sum(durations) / len(durations) if durations else 0.0

    return {
        "eclipse_count": build(len(eclipses), "count"),
        "max_eclipse_duration": build(max(durations, default=0.0), "s"),
        "min_eclipse_duration": build(min(durations, default=0.0), "s"),
        "mean_eclipse_duration": build(mean_duration_s, "s"),
        "max_umbra_duration": build(max(umbra_durations, default=0.0), "s"),
        "max_orbit_fraction": build(max(fractions, default=0.0), "none"),
        "beta_angle_at_start": beta_angle(tle, start),
        "beta_angle_at_end": beta_angle(tle, end),
    }


def eclipse_receipt(tle: TLE, start: datetime, days: float) -> Receipt:
    """Build the provenance record shared by a list of predicted eclipses.

    `eclipse_intervals` returns bare dataclasses so that one receipt covers the whole span rather
    than being duplicated onto every boundary time; this is that receipt.

    Args:
        tle: Element set used.
        start: Beginning of the span, used to age the uncertainty estimate.
        days: Length of the span.

    Returns:
        A receipt describing how the eclipses were produced.
    """
    return _summary_receipt(orbit.build_satellite(tle), tle, start, days, None)


# --------------------------------------------------------------------------------------------
# Provenance helpers
# --------------------------------------------------------------------------------------------


def dataset_record(tle: TLE) -> dict[str, object]:
    """Describe the element set and the Sun ephemeris backing a value.

    `science.orbit` already knows how to describe an element set; this adds the second dataset
    every eclipse answer depends on, which would otherwise go unrecorded and leave a reader
    unable to tell which solar ephemeris produced the number.

    Args:
        tle: The element set.

    Returns:
        A dataset record for a receipt.
    """
    return {
        **orbit.dataset_record(tle),
        "sun_ephemeris": "astropy get_sun (ERFA simplified VSOP2000, built in, no download)",
        "sun_ephemeris_accuracy": "~4 km in the Sun-Earth vector, 1900-2100",
    }


def _timing_uncertainty(satellite: EarthSatellite, tle: TLE, when: datetime) -> dict[str, object]:
    """Translate the SGP4 position uncertainty into an eclipse-boundary timing uncertainty.

    A shadow boundary is a place, and the satellite crosses it at orbital speed, so a position
    error of a few kilometres along track is a timing error of a fraction of a second. Stating it
    in seconds is what lets a reader judge whether an entry time is good enough for their purpose;
    stating it in kilometres leaves them to do the division and guess the speed.

    The speed used is the propagated speed at ``when``, not a nominal figure, so the estimate
    stays honest across orbit regimes -- a geostationary satellite moves at 3 km/s, not 7.7.

    Args:
        satellite: A skyfield ``EarthSatellite``.
        tle: The element set being propagated.
        when: The instant being evaluated.

    Returns:
        An uncertainty record for a receipt.
    """
    base = orbit.position_uncertainty(tle, when)
    state = satellite.at(orbit.TIMESCALE.from_datetime(when))
    speed_km_s = float(np.linalg.norm(state.velocity.km_per_s))

    return {
        **base,
        "orbital_speed_km_s": round(speed_km_s, 3),
        "boundary_timing_error_s": round(base["position_error_km"] / speed_km_s, 2),
        "basis": (
            f"{base['basis']} Boundary timing error is that position error divided by the "
            f"propagated orbital speed, and assumes the error is along track, which is the "
            f"worst case for a crossing time."
        ),
    }


def _summary_receipt(
    satellite: EarthSatellite, tle: TLE, start: datetime, days: float, count: int | None
) -> Receipt:
    """Build the receipt shared by every eclipse quantity over one span.

    Args:
        satellite: A skyfield ``EarthSatellite``, reused so the span is not propagated twice.
        tle: Element set used.
        start: Beginning of the span.
        days: Length of the span.
        count: Number of complete eclipses found, or None when the count is not yet known.

    Returns:
        A receipt describing how the span was searched.
    """
    notes = (
        "Conical shadow model: umbra and penumbra are separated using the apparent angular radii "
        "of the Earth and the Sun as seen from the satellite. The Earth is modelled as a sphere "
        "of its equatorial radius, which never understates the shadow, and atmospheric refraction "
        "is ignored -- the real atmosphere bends and reddens sunlight into the geometric umbra, "
        "so a physical eclipse is slightly shallower at its edges than this reports. Eclipses "
        "already underway at the start of the span, or still underway at its end, are omitted "
        "rather than reported with a missing boundary."
    )
    if count == 0:
        notes = (
            "No complete eclipse in this span -- either the orbit is in full sun, or the span is "
            f"shorter than the time between one eclipse entry and the next. {notes}"
        )

    return Receipt(
        tool="eclipse",
        inputs={
            "norad_id": tle.norad_id,
            "from_utc": start.isoformat(),
            "days": days,
            "search_step_minutes": SEARCH_STEP_MINUTES,
        },
        frame="GCRS (geocentric ICRF); SGP4 TEME state rotated by skyfield",
        time_scale="UTC",
        dataset=dataset_record(tle),
        equation=(
            "Sunlit when the angular separation of the Earth and Sun discs seen from the "
            "satellite exceeds the sum of their angular radii; umbra when it is below their "
            "difference; penumbra in between"
        ),
        uncertainty=_timing_uncertainty(satellite, tle, start),
        notes=notes,
    )

"""Positions of the Sun, Moon and planets.

The default source is JPL DE421, packaged with the deployment through ``skyfield-data``.  It is a
numerically integrated SPK kernel rather than an analytic approximation, and it never downloads
at runtime.  The previous ERFA analytic series remains selectable and is the automatic fallback
if the packaged asset cannot open, so improving precision does not create a new boot dependency.

Both modes are independently measured against Orekit reading a JPL ephemeris.  The JPL mode
reduces the largest measured outer-planet disagreement from 756,788 km to 3,296 km.  It still is
not spacecraft navigation: different DE releases legitimately differ, apparent positions omit
annual aberration, and topocentric parallax needs an observer location.  Every receipt identifies
the effective model, kernel, frame, time scale, and measured bound.

**Light time is corrected.** For apparent sky positions the geometric direction is wrong by up
to tens of arcseconds -- larger than the ephemeris error itself for the inner planets -- so the
position is iterated back along the light path. Aberration from the observer's own motion is
*not* applied, and that omission is stated on the value.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from typing import Literal

from science.provenance import Receipt, Tier, Value

# Metres per astronomical unit (IAU 2012 definition, exact).
AU_M = 1.495978707e11

# Speed of light, m/s (exact by definition).
C_M_PER_S = 299_792_458.0

BODIES = (
    "sun",
    "mercury",
    "venus",
    "earth",
    "moon",
    "mars",
    "jupiter",
    "saturn",
    "uranus",
    "neptune",
)

EphemerisModel = Literal["precision", "analytic"]
DEFAULT_MODEL: EphemerisModel = "precision"
KERNEL_NAME = "de421.bsp"
KERNEL_COVERAGE = "1899-07-28 through 2053-10-08"
KERNEL_SHA256 = "a20a7139da04cbc462454634918e9a9ca69127044e2cc9d4f9c16e238d2deedc"
KERNEL_BYTES = 16_788_480

KERNEL_BODY_NAMES = {
    "sun": "sun",
    "mercury": "mercury",
    "venus": "venus",
    "earth": "earth",
    "moon": "moon",
    "mars": "mars",
    "jupiter": "jupiter barycenter",
    "saturn": "saturn barycenter",
    "uranus": "uranus barycenter",
    "neptune": "neptune barycenter",
}

# Measured departure from Orekit's JPL DE ephemeris, over epochs spanning 2000-2040.
#
# These are not estimates or vendor claims: they are the worst observed disagreement, in
# kilometres and as a fraction of the body's distance, produced by
# tools/reference/generate_ephemeris.py. They are reported to the user because the digits in a
# position otherwise imply a precision the analytic series does not have.
#
# The Sun's relative figure is large only because it sits near the barycentre, so its distance is
# small; its absolute error is the smallest of any body. Absolute is the meaningful figure there.
ANALYTIC_ACCURACY: dict[str, dict[str, float]] = {
    "sun": {"max_error_km": 119.0, "relative": 1.03e-4},
    "mercury": {"max_error_km": 535.0, "relative": 7.99e-6},
    "venus": {"max_error_km": 2162.0, "relative": 2.00e-5},
    "earth": {"max_error_km": 122.0, "relative": 8.31e-7},
    "moon": {"max_error_km": 126.0, "relative": 8.53e-7},
    "mars": {"max_error_km": 7189.0, "relative": 3.37e-5},
    "jupiter": {"max_error_km": 118_096.0, "relative": 1.47e-4},
    "saturn": {"max_error_km": 405_000.0, "relative": 2.97e-4},
    "uranus": {"max_error_km": 756_788.0, "relative": 2.73e-4},
    "neptune": {"max_error_km": 131_629.0, "relative": 2.94e-5},
}

# Measured against the same committed Orekit/JPL reference as the analytic table, across ten
# bodies and five epochs spanning 2000-2040. Rounded upward so the published limit is never more
# flattering than the measurement. The common ~118 km offset reflects a difference between the
# JPL ephemeris release in Orekit's data bundle and DE421, not numerical noise.
PRECISION_ACCURACY: dict[str, dict[str, float]] = {
    "sun": {"max_error_km": 120.0, "relative": 1.16e-3},
    "mercury": {"max_error_km": 120.0, "relative": 2.52e-6},
    "venus": {"max_error_km": 120.0, "relative": 1.09e-6},
    "earth": {"max_error_km": 120.0, "relative": 8.05e-7},
    "moon": {"max_error_km": 120.0, "relative": 8.07e-7},
    "mars": {"max_error_km": 120.0, "relative": 5.71e-7},
    "jupiter": {"max_error_km": 145.0, "relative": 1.94e-7},
    "saturn": {"max_error_km": 125.0, "relative": 8.73e-8},
    "uranus": {"max_error_km": 3000.0, "relative": 1.06e-6},
    "neptune": {"max_error_km": 3400.0, "relative": 7.40e-7},
}

# Backward-compatible name used by consumers that mean the default mode.
ACCURACY = PRECISION_ACCURACY

# Equatorial radius in metres and a display colour, for rendering. Radii are IAU 2015 nominal
# values; they are constants of the body, not computed, so they carry no uncertainty of their own.
BODY_FACTS: dict[str, dict[str, object]] = {
    "sun": {"radius_m": 6.957e8, "colour": "#ffd27d"},
    "mercury": {"radius_m": 2.4397e6, "colour": "#9c8f84"},
    "venus": {"radius_m": 6.0518e6, "colour": "#d9b27c"},
    "earth": {"radius_m": 6.3781e6, "colour": "#6b93d6"},
    "moon": {"radius_m": 1.7374e6, "colour": "#b0aca6"},
    "mars": {"radius_m": 3.3962e6, "colour": "#c1502e"},
    "jupiter": {"radius_m": 7.1492e7, "colour": "#d8ca9d"},
    "saturn": {"radius_m": 6.0268e7, "colour": "#e3d9a5"},
    "uranus": {"radius_m": 2.5559e7, "colour": "#a6d8e0"},
    "neptune": {"radius_m": 2.4764e7, "colour": "#5b7fd4"},
}


class EphemerisError(ValueError):
    """Raised when a body is not one this module can compute."""


class PrecisionRangeError(EphemerisError):
    """Raised when a requested instant lies outside the packaged kernel coverage."""


@dataclass(frozen=True)
class Vector:
    """A barycentric position in metres, ICRF.

    Attributes:
        x: ICRF x, metres.
        y: ICRF y, metres.
        z: ICRF z, metres.
    """

    x: float
    y: float
    z: float

    @property
    def norm(self) -> float:
        """Length of the vector, metres."""
        return math.sqrt(self.x * self.x + self.y * self.y + self.z * self.z)

    def minus(self, other: Vector) -> Vector:
        """Return the vector from `other` to this one."""
        return Vector(self.x - other.x, self.y - other.y, self.z - other.z)


def _check_body(body: str) -> str:
    """Normalise and validate a body name.

    Args:
        body: Body name, any case.

    Returns:
        The lowercase name.

    Raises:
        EphemerisError: If the body is not supported.
    """
    name = body.strip().lower()
    if name not in BODIES:
        raise EphemerisError(
            f"unknown body {body!r}. Supported: {', '.join(BODIES)}. "
            "Asteroids, comets and exoplanets are not available from this ephemeris."
        )
    return name


def _kernel_path() -> Path:
    """Return the installed DE421 path without triggering a network request.

    Returns:
        Path inside the ``skyfield-data`` wheel.
    """
    import skyfield_data

    return Path(skyfield_data.__file__).resolve().parent / "data" / KERNEL_NAME


@lru_cache(maxsize=1)
def _kernel():
    """Open and cache the packaged JPL DE421 SPK kernel.

    Returns:
        Skyfield ``SpiceKernel`` instance.

    Raises:
        OSError: If the packaged kernel is missing or unreadable.
    """
    from skyfield.api import load_file

    path = _kernel_path()
    if not path.is_file():
        raise OSError(f"packaged JPL kernel is missing: {path}")
    return load_file(str(path))


def _file_sha256(path: Path) -> str:
    """Hash a file incrementally so health checks do not duplicate the kernel in memory.

    Args:
        path: File whose bytes identify the deployed dataset.

    Returns:
        Lowercase hexadecimal SHA-256 digest.
    """
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


@lru_cache(maxsize=1)
def kernel_metadata() -> dict[str, object]:
    """Return auditable identity and availability for the packaged precision asset.

    Returns:
        Kernel filename, SHA-256 digest, coverage, byte size, and availability.
    """
    try:
        path = _kernel_path()
        _kernel()
        digest = _file_sha256(path)
        size = path.stat().st_size
        if digest != KERNEL_SHA256 or size != KERNEL_BYTES:
            raise ValueError(
                f"packaged JPL kernel identity mismatch: sha256={digest}, bytes={size}"
            )
        return {
            "available": True,
            "identity_verified": True,
            "kernel": KERNEL_NAME,
            "sha256": digest,
            "bytes": size,
            "coverage": KERNEL_COVERAGE,
            "package": "skyfield-data==7.0.0",
        }
    except (ImportError, OSError, ValueError) as exc:
        return {
            "available": False,
            "identity_verified": False,
            "kernel": KERNEL_NAME,
            "error": f"{type(exc).__name__}: {exc}",
            "coverage": KERNEL_COVERAGE,
            "package": "skyfield-data==7.0.0",
        }


def effective_model(requested: EphemerisModel) -> EphemerisModel:
    """Resolve precision to analytic when the packaged kernel cannot be verified and opened.

    Args:
        requested: Caller-selected model.

    Returns:
        Effective model. Analytic requests are unchanged; precision falls back visibly.
    """
    if requested == "precision" and not bool(kernel_metadata()["available"]):
        return "analytic"
    return requested


def accuracy_for(model: EphemerisModel, body: str) -> dict[str, float]:
    """Return the independently measured error bound for one model and body.

    Args:
        model: Effective ephemeris model.
        body: Validated lowercase body name.

    Returns:
        Maximum measured absolute and relative errors.
    """
    table = PRECISION_ACCURACY if model == "precision" else ANALYTIC_ACCURACY
    return table[body]


@lru_cache(maxsize=512)
def _barycentric(body: str, iso_tdb: str, model: EphemerisModel = DEFAULT_MODEL) -> Vector:
    """Barycentric ICRF position of a body, metres.

    Cached: an apparent-position solve evaluates the same body two or three times while
    iterating light time, and a snapshot evaluates ten bodies at one instant.

    Args:
        body: Validated lowercase body name.
        iso_tdb: Instant as an ISO string in the TDB scale.
        model: Effective precision or analytic model.

    Returns:
        Position in metres, barycentric ICRF.
    """
    if model == "precision":
        from astropy.time import Time
        from skyfield.errors import EphemerisRangeError

        from science.orbit import TIMESCALE

        jd_tdb = float(Time(iso_tdb, scale="tdb").jd)
        try:
            body_position = (
                _kernel()[KERNEL_BODY_NAMES[body]].at(TIMESCALE.tdb_jd(jd_tdb)).position
            )
        except EphemerisRangeError as exc:
            raise PrecisionRangeError(
                f"precision ephemeris covers {KERNEL_COVERAGE}; use model='analytic' outside "
                "that interval"
            ) from exc
        return Vector(
            float(body_position.m[0]),
            float(body_position.m[1]),
            float(body_position.m[2]),
        )

    # astropy warns when it falls back for bodies the builtin series treats approximately; the
    # resulting error is quantified in ANALYTIC_ACCURACY and reported, so the warning adds nothing.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")

        import astropy.units as u
        from astropy.coordinates import get_body_barycentric, solar_system_ephemeris
        from astropy.time import Time

        with solar_system_ephemeris.set("builtin"):
            position = get_body_barycentric(body, Time(iso_tdb, scale="tdb"))

    return Vector(
        float(position.x.to(u.m).value),
        float(position.y.to(u.m).value),
        float(position.z.to(u.m).value),
    )


def _to_tdb_iso(when: datetime) -> str:
    """Convert a UTC instant to a TDB ISO string.

    Planetary ephemerides are parameterised by TDB, not UTC. The two differ by around 69
    seconds, which moves the Moon by roughly 900 km -- far larger than this ephemeris's own
    error, so treating UTC as TDB would dominate every other source of inaccuracy.

    Args:
        when: A timezone-aware instant.

    Returns:
        The same instant expressed in TDB, ISO-8601.
    """
    # astropy's ISO parsers reject a "+00:00" offset, so the instant is normalised to UTC and
    # the offset dropped. A naive input is taken as UTC, matching the API boundary: every
    # timestamp in this service is UTC, and silently applying the server's local zone would
    # shift results by hours depending on where the process happens to run.
    normalised = when.astimezone(UTC) if when.tzinfo else when.replace(tzinfo=UTC)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from astropy.time import Time

        return str(Time(normalised.replace(tzinfo=None).isoformat(), scale="utc").tdb.isot)


def _receipt(
    body: str,
    when: datetime,
    *,
    model: EphemerisModel,
    requested_model: EphemerisModel,
    quantity: str,
    extra_notes: str = "",
) -> Receipt:
    """Build the provenance record shared by every value this module returns.

    Args:
        body: Body name.
        when: Instant of evaluation.
        model: Effective model used for the value.
        requested_model: Model the caller requested, before any safe fallback.
        quantity: What was computed, for the equation field.
        extra_notes: Caveats specific to the calling function.

    Returns:
        The receipt.
    """
    accuracy = accuracy_for(model, body)
    if model == "precision":
        dataset = {
            "source": "JPL DE421 numerical ephemeris",
            **kernel_metadata(),
            "graded_against": "Orekit reading a JPL DE ephemeris, 2000-2040",
        }
        fit_for = (
            "High-precision planetary geometry, visualization, and research calculations within "
            "the kernel coverage."
        )
        not_fit_for = (
            "Navigation or spacecraft targeting without mission-specific kernels and an "
            "independent operational pipeline."
        )
    else:
        dataset = {
            "source": "astropy builtin ephemeris (ERFA analytic series)",
            "role": "selectable fallback requiring no binary kernel",
            "graded_against": "Orekit reading a JPL DE ephemeris, 2000-2040",
        }
        fit_for = "Orientation, rendering, relative geometry, and approximate sky position."
        not_fit_for = (
            "Navigation, spacecraft targeting, occultation or transit timing, or anything "
            "requiring arcsecond accuracy. Use the packaged JPL precision mode."
        )

    fallback_note = (
        " Requested precision mode was unavailable, so the response used the labelled analytic "
        "fallback."
        if requested_model != model
        else ""
    )

    return Receipt(
        tool="ephemeris",
        inputs={
            "body": body,
            "at_utc": when.isoformat(),
            "requested_model": requested_model,
            "effective_model": model,
        },
        frame="ICRF (barycentric)",
        time_scale="TDB, converted from UTC",
        dataset=dataset,
        equation=quantity,
        uncertainty={
            "max_error_km": accuracy["max_error_km"],
            "relative": accuracy["relative"],
            "basis": (
                f"Worst measured disagreement with a JPL numerical ephemeris for {body}: "
                f"{accuracy['max_error_km']:,.0f} km, or {accuracy['relative']:.1e} of its "
                "distance. This is a measured figure, not a specification."
            ),
            "fit_for": fit_for,
            "not_fit_for": not_fit_for,
        },
        notes=(
            f"Position of {body} at {when.isoformat()}. " + extra_notes + fallback_note
        ).strip(),
    )


def barycentric_position(
    body: str, when: datetime, model: EphemerisModel = DEFAULT_MODEL
) -> dict[str, Value]:
    """Position of a body relative to the solar-system barycentre.

    Args:
        body: One of `BODIES`.
        when: A timezone-aware instant.
        model: Requested precision or analytic model.

    Returns:
        ICRF x, y, z and distance from the barycentre, each with provenance.

    Raises:
        EphemerisError: If the body is unsupported.
    """
    name = _check_body(body)
    selected = effective_model(model)
    position = _barycentric(name, _to_tdb_iso(when), selected)

    def build(value: float, quantity: str) -> Value:
        return Value(
            value=round(value / AU_M, 9),
            unit="AU",
            # Derived, not observed: this is computed from a fitted model of accepted physics,
            # not measured. Nobody observed Uranus at this coordinate.
            tier=Tier.DERIVED,
            receipt=_receipt(
                name,
                when,
                model=selected,
                requested_model=model,
                quantity=quantity,
            ),
        )

    return {
        "x": build(position.x, "barycentric ICRF x"),
        "y": build(position.y, "barycentric ICRF y"),
        "z": build(position.z, "barycentric ICRF z"),
        "distance_from_barycentre": build(position.norm, "|r| from solar-system barycentre"),
    }


def apparent_from_earth(
    body: str, when: datetime, model: EphemerisModel = DEFAULT_MODEL
) -> dict[str, Value]:
    """Where a body appears in Earth's sky, corrected for light travel time.

    Light time is not a nicety here. Sunlight takes eight minutes to arrive and Jupiter's up to
    fifty; over that interval the body moves, so the geometric direction is wrong by tens of
    arcseconds -- larger than this ephemeris's own error for the inner planets. The position is
    therefore iterated: solve for the emission time such that the light arrives now.

    Aberration from the observer's own motion is **not** applied, which displaces a body by up
    to about 20 arcseconds. That is stated on the value rather than left for a reader to
    discover.

    Args:
        body: One of `BODIES`, other than earth.
        when: A timezone-aware instant of observation.
        model: Requested precision or analytic model.

    Returns:
        Right ascension, declination, distance, and light travel time, with provenance.

    Raises:
        EphemerisError: If the body is unsupported, or is the Earth itself.
    """
    name = _check_body(body)
    if name == "earth":
        raise EphemerisError("the Earth has no apparent position in Earth's own sky")

    selected = effective_model(model)
    iso_tdb = _to_tdb_iso(when)
    observer = _barycentric("earth", iso_tdb, selected)

    # Iterate the light-time solution. Two passes converge to well under a metre for every body
    # here; a third is cheap insurance and still costs nothing measurable.
    emission_iso = iso_tdb
    separation = _barycentric(name, iso_tdb, selected).minus(observer)

    for _ in range(3):
        light_time_s = separation.norm / C_M_PER_S
        emission_iso = _shift_iso(iso_tdb, -light_time_s)
        separation = _barycentric(name, emission_iso, selected).minus(observer)

    distance = separation.norm
    light_time_s = distance / C_M_PER_S

    right_ascension = math.degrees(math.atan2(separation.y, separation.x)) % 360.0
    declination = math.degrees(math.asin(separation.z / distance))

    notes = (
        "Corrected for light travel time. Not corrected for annual aberration (up to ~20 "
        "arcsec) or atmospheric refraction. Geocentric, not topocentric: no observer location "
        "is applied, which moves the Moon by up to about 1 degree."
    )

    def build(value: float, unit: str, quantity: str, *, places: int = 6) -> Value:
        return Value(
            value=round(value, places),
            unit=unit,
            tier=Tier.DERIVED,
            receipt=_receipt(
                name,
                when,
                model=selected,
                requested_model=model,
                quantity=quantity,
                extra_notes=notes,
            ),
        )

    return {
        # Six decimal places on an angle in degrees is 3.6 milliarcseconds -- far finer than the
        # ephemeris itself, so nothing is lost.
        "right_ascension": build(right_ascension, "degrees", "atan2(y, x) of the geocentric vector"),
        "declination": build(declination, "degrees", "asin(z / |r|) of the geocentric vector"),
        # Distance needs nine, not six. Six decimal places of an AU is 150 km, which is coarser
        # than the ephemeris error for the Moon (126 km) -- rounding would then be throwing away
        # more accuracy than the model itself lacks.
        "distance": build(distance / AU_M, "AU", "|r| from Earth, light-time corrected", places=9),
        "light_travel_time": build(light_time_s / 60.0, "minutes", "|r| / c", places=9),
    }


def _shift_iso(iso_tdb: str, seconds: float) -> str:
    """Shift a TDB ISO timestamp by a number of seconds.

    Args:
        iso_tdb: Instant in TDB.
        seconds: Offset to apply; negative moves earlier.

    Returns:
        The shifted instant, ISO-8601 in TDB.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from astropy.time import Time, TimeDelta

        return str((Time(iso_tdb, scale="tdb") + TimeDelta(seconds, format="sec")).isot)


def snapshot(
    when: datetime, model: EphemerisModel = DEFAULT_MODEL
) -> dict[str, object]:
    """Positions of every supported body at one instant, for rendering.

    Returns plain numbers rather than tiered values: a renderer consumes ten positions at once
    and a receipt per coordinate would be forty receipts of identical provenance. The tier and
    accuracy are stated once for the whole snapshot instead, which is the same claim without the
    repetition.

    Args:
        when: A timezone-aware instant.
        model: Requested precision or analytic model.

    Returns:
        One entry per body with barycentric AU coordinates, radius, and colour.
    """
    selected = effective_model(model)
    iso_tdb = _to_tdb_iso(when)

    bodies = []
    for name in BODIES:
        position = _barycentric(name, iso_tdb, selected)
        facts = BODY_FACTS[name]

        # Heliocentric ecliptic is what a renderer needs: orbits lie in this plane, so bodies
        # drawn from these coordinates sit on their own orbit paths. The barycentric ICRF values
        # are kept alongside because that is the frame the positions were computed in, and
        # silently replacing them would hide a frame conversion.
        ecliptic = to_ecliptic(position.minus(_barycentric("sun", iso_tdb, selected)))

        bodies.append(
            {
                "body": name,
                "x_au": round(position.x / AU_M, 9),
                "y_au": round(position.y / AU_M, 9),
                "z_au": round(position.z / AU_M, 9),
                "ecliptic_x_au": round(ecliptic.x / AU_M, 9),
                "ecliptic_y_au": round(ecliptic.y / AU_M, 9),
                "ecliptic_z_au": round(ecliptic.z / AU_M, 9),
                "distance_from_sun_au": round(ecliptic.norm / AU_M, 9),
                "distance_from_barycentre_au": round(position.norm / AU_M, 9),
                "radius_m": facts["radius_m"],
                "colour": facts["colour"],
                "max_error_km": accuracy_for(selected, name)["max_error_km"],
            }
        )

    return {
        "at_utc": when.isoformat(),
        "at_tdb": iso_tdb,
        "frame": "ICRF barycentric; ecliptic heliocentric coordinates supplied alongside",
        "tier": Tier.DERIVED.value,
        "requested_model": model,
        "model": selected,
        "dataset": (
            {"source": "JPL DE421 numerical ephemeris", **kernel_metadata()}
            if selected == "precision"
            else {
                "source": "astropy builtin ephemeris (ERFA analytic series)",
                "fallback": model != selected,
            }
        ),
        "bodies": bodies,
        "accuracy": (
            "Positions come from packaged JPL DE421. Worst measured disagreement with the "
            "independent Orekit/JPL reference over 2000-2040 is 3,400 km (Neptune), versus "
            "756,788 km for the analytic fallback. Not a spacecraft-navigation product."
            if selected == "precision"
            else (
                "Positions use the ERFA analytic fallback. Worst measured disagreement with a "
                "JPL ephemeris over 2000-2040 is 3e-4 of a body's distance; use precision mode "
                "for research geometry."
            )
        ),
    }


# --------------------------------------------------------------------------------------------
# Ecliptic frame, and orbit paths
# --------------------------------------------------------------------------------------------

# Obliquity of the ecliptic at J2000 (IAU 2006), degrees.
#
# ICRF is an *equatorial* frame: its xy-plane is Earth's equator. The planets orbit in the
# *ecliptic*, tilted from it by this angle. Rendering ICRF coordinates against a flat orbital
# plane therefore throws every body up to 23 degrees off its own orbit -- which is exactly what
# the first version of the solar-system view did.
OBLIQUITY_DEG = 23.4392911

# Sidereal orbital periods in Julian years, used to sample one full revolution.
ORBITAL_PERIOD_YEARS: dict[str, float] = {
    "mercury": 0.2408467,
    "venus": 0.6151973,
    "earth": 1.0000174,
    "mars": 1.8808476,
    "jupiter": 11.862615,
    "saturn": 29.447498,
    "uranus": 84.016846,
    "neptune": 164.79132,
}


def to_ecliptic(vector: Vector) -> Vector:
    """Rotate an ICRF equatorial vector into ecliptic coordinates.

    A rotation about the x-axis by the obliquity. The x-axis is shared by both frames -- it
    points at the March equinox, which is precisely where the two planes intersect -- so only y
    and z change.

    Args:
        vector: Position in ICRF equatorial coordinates.

    Returns:
        The same position expressed in ecliptic coordinates.
    """
    angle = math.radians(OBLIQUITY_DEG)
    cos_e, sin_e = math.cos(angle), math.sin(angle)

    return Vector(
        vector.x,
        vector.y * cos_e + vector.z * sin_e,
        -vector.y * sin_e + vector.z * cos_e,
    )


@lru_cache(maxsize=16)
def orbit_path(
    body: str, samples: int = 180, model: EphemerisModel = "analytic"
) -> tuple[tuple[float, float, float], ...]:
    """Trace a body's actual orbit by sampling the ephemeris over one full period.

    The first version of the renderer drew a circle at the body's current distance. That asserts
    a shape the data does not contain: real orbits are ellipses, offset from the Sun, and a
    circle through one sampled point is a guess wearing the costume of a measurement. Sampling
    the ephemeris over a whole revolution produces the real path, and the body then sits exactly
    on it -- because it is the same computation.

    Cached: an orbit does not change, and Neptune costs 180 ephemeris evaluations.

    Args:
        body: A planet name. The Sun and Moon have no heliocentric orbit to draw.
        samples: Points around the path.
        model: Ephemeris source. Analytic is the default because DE421 ends in 2053, before one
            full outer-planet revolution can be sampled.

    Returns:
        Ecliptic (x, y, z) points in AU, heliocentric, closed by the caller.

    Raises:
        EphemerisError: If the body has no tabulated orbital period.
    """
    name = _check_body(body)
    if name not in ORBITAL_PERIOD_YEARS:
        raise EphemerisError(f"{name} has no heliocentric orbit path to draw")

    period_days = ORBITAL_PERIOD_YEARS[name] * 365.25
    start = datetime(2026, 1, 1, tzinfo=UTC)

    points: list[tuple[float, float, float]] = []
    for index in range(samples):
        when = start + timedelta(days=period_days * index / samples)
        iso = _to_tdb_iso(when)

        # Heliocentric, not barycentric: an orbit is drawn about the Sun, and the barycentre
        # wanders by up to a solar radius as Jupiter moves. Using it would make the inner
        # planets' paths visibly wobble for no physical reason.
        relative = to_ecliptic(
            _barycentric(name, iso, model).minus(_barycentric("sun", iso, model))
        )
        points.append(
            (relative.x / AU_M, relative.y / AU_M, relative.z / AU_M)
        )

    return tuple(points)

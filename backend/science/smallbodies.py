"""Moons and asteroids, propagated from committed orbital elements.

astropy's builtin ephemeris covers the Sun, the planets and Earth's Moon. Everything else here --
nineteen more moons and several hundred asteroids -- comes from published element sets fetched
from JPL (see `tools/reference/fetch_moons.py` and `fetch_asteroids.py`) and propagated as a
**two-body orbit**.

**What two-body ignores, and why it is still worth doing.** A real moon is pulled by its parent's
equatorial bulge, by the Sun, and by its sibling moons. A real asteroid is pulled by Jupiter.
None of that is modelled here, so the orbit plane precesses in reality while this holds it fixed.
The result is that a body's *position along* its orbit stays good for a long time, while the
orientation of the orbit slowly goes stale.

That is a real limitation and it is measured rather than guessed: `tests/test_smallbodies.py`
grades these positions against JPL Horizons vectors at later dates, and the measured departure is
reported on every value. For the thing this feeds -- seeing where the moons are and how the belt
is structured -- it is entirely adequate; for anything that has to hit a target, it is not, and
the receipts say so.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from science.ephemeris import AU_M, _barycentric, _to_tdb_iso, to_ecliptic

DATA_DIR = Path(__file__).parent / "data"

# Heliocentric gravitational constant, m^3/s^2 (IAU 2015 nominal).
MU_SUN = 1.32712440018e20

# Julian date of J2000.0, for converting element epochs.
J2000_JD = 2451545.0

# Kepler's equation is solved by Newton iteration. Six is generous for the eccentricities here
# (the most eccentric asteroid in the set is well under 0.5); the loop exits early on convergence.
KEPLER_MAX_ITERATIONS = 24
KEPLER_TOLERANCE = 1e-12


@dataclass(frozen=True)
class Elements:
    """A Keplerian element set and the epoch it applies at.

    Attributes:
        semi_major_axis_m: Semi-major axis, metres.
        eccentricity: Orbital eccentricity.
        inclination_rad: Inclination to the reference plane.
        node_rad: Longitude of the ascending node.
        periapsis_rad: Argument of periapsis.
        mean_anomaly_rad: Mean anomaly at the epoch.
        mean_motion_rad_per_s: Mean motion.
        epoch_jd: Epoch as a Julian date, TDB.
        node_rate_rad_per_s: Secular drift of the node. Zero for a pure two-body orbit.
        periapsis_rate_rad_per_s: Secular drift of the periapsis. Zero for a pure two-body orbit.
    """

    semi_major_axis_m: float
    eccentricity: float
    inclination_rad: float
    node_rad: float
    periapsis_rad: float
    mean_anomaly_rad: float
    mean_motion_rad_per_s: float
    epoch_jd: float
    node_rate_rad_per_s: float = 0.0
    periapsis_rate_rad_per_s: float = 0.0


def solve_kepler(mean_anomaly: float, eccentricity: float) -> float:
    """Solve Kepler's equation ``M = E - e sin E`` for the eccentric anomaly.

    Newton's method, started from a guess that is good across the whole elliptical range. The
    naive start ``E = M`` converges slowly at high eccentricity, and this costs nothing.

    Args:
        mean_anomaly: Mean anomaly, radians.
        eccentricity: Eccentricity, below 1.

    Returns:
        Eccentric anomaly, radians.

    Raises:
        ValueError: If the orbit is not elliptical.
    """
    if not 0.0 <= eccentricity < 1.0:
        raise ValueError(f"eccentricity {eccentricity} is not elliptical")

    normalised = math.remainder(mean_anomaly, math.tau)

    eccentric = normalised + eccentricity * math.sin(normalised)

    for _ in range(KEPLER_MAX_ITERATIONS):
        error = eccentric - eccentricity * math.sin(eccentric) - normalised
        derivative = 1.0 - eccentricity * math.cos(eccentric)
        step = error / derivative
        eccentric -= step
        if abs(step) < KEPLER_TOLERANCE:
            break

    return eccentric


def position_at(elements: Elements, when_jd: float) -> tuple[float, float, float]:
    """Propagate an element set to a time and return the position.

    Args:
        elements: The orbit.
        when_jd: Julian date, TDB.

    Returns:
        Position in metres, in the frame the elements are referred to.
    """
    seconds = (when_jd - elements.epoch_jd) * 86400.0
    mean_anomaly = elements.mean_anomaly_rad + elements.mean_motion_rad_per_s * seconds

    # Secular precession of the orbit plane and the line of apsides. Without these a close-in
    # moon of an oblate planet goes badly wrong within months -- Phobos by twice its own orbit
    # radius in half a year. The rates are fitted from a Horizons series, not modelled from a J2.
    node = elements.node_rad + elements.node_rate_rad_per_s * seconds
    periapsis = elements.periapsis_rad + elements.periapsis_rate_rad_per_s * seconds

    eccentric = solve_kepler(mean_anomaly, elements.eccentricity)

    # Position in the orbital plane, with the x-axis toward periapsis.
    cos_e = math.cos(eccentric)
    sin_e = math.sin(eccentric)
    factor = math.sqrt(1.0 - elements.eccentricity * elements.eccentricity)

    x_orbital = elements.semi_major_axis_m * (cos_e - elements.eccentricity)
    y_orbital = elements.semi_major_axis_m * factor * sin_e

    # Rotate perifocal -> reference frame: periapsis, then inclination, then node.
    cos_w, sin_w = math.cos(periapsis), math.sin(periapsis)
    cos_i, sin_i = math.cos(elements.inclination_rad), math.sin(elements.inclination_rad)
    cos_o, sin_o = math.cos(node), math.sin(node)

    x_plane = x_orbital * cos_w - y_orbital * sin_w
    y_plane = x_orbital * sin_w + y_orbital * cos_w

    return (
        x_plane * cos_o - y_plane * cos_i * sin_o,
        x_plane * sin_o + y_plane * cos_i * cos_o,
        y_plane * sin_i,
    )


@lru_cache(maxsize=1)
def _moon_data() -> dict:
    """Load the committed moon element sets."""
    path = DATA_DIR / "moons.json"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} missing - run `uv run python tools/reference/fetch_moons.py`"
        )
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _asteroid_data() -> dict:
    """Load the committed asteroid element sets."""
    path = DATA_DIR / "asteroids.json"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} missing - run `uv run python tools/reference/fetch_asteroids.py`"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _to_julian_date(when: datetime) -> float:
    """Convert an instant to a Julian date in TDB."""
    from astropy.time import Time

    return float(Time(_to_tdb_iso(when), scale="tdb").jd)


def moon_elements() -> list[tuple[str, str, Elements, float | None]]:
    """Every committed moon as (name, parent planet, elements, radius in metres)."""
    epoch_jd = _to_julian_date(datetime.fromisoformat("2026-01-01T00:00:00+00:00"))

    records = []
    for moon in _moon_data()["moons"]:
        records.append(
            (
                moon["name"],
                moon["planet"],
                Elements(
                    semi_major_axis_m=moon["semi_major_axis_km"] * 1000.0,
                    eccentricity=moon["eccentricity"],
                    inclination_rad=math.radians(moon["inclination_deg"]),
                    node_rad=math.radians(moon["node_deg"]),
                    periapsis_rad=math.radians(moon["argument_of_periapsis_deg"]),
                    mean_anomaly_rad=math.radians(moon["mean_anomaly_deg"]),
                    # The mean anomaly advances at the fitted mean-longitude rate minus the two
                    # precession rates, so that the body's actual angular progression matches the
                    # measured one. Using the osculating mean motion here instead double-counts
                    # the precession.
                    mean_motion_rad_per_s=math.radians(
                        moon["mean_longitude_rate_deg_per_day"]
                        - moon["node_rate_deg_per_day"]
                        - moon["periapsis_rate_deg_per_day"]
                    )
                    / 86400.0,
                    epoch_jd=epoch_jd,
                    node_rate_rad_per_s=math.radians(moon["node_rate_deg_per_day"]) / 86400.0,
                    periapsis_rate_rad_per_s=(
                        math.radians(moon["periapsis_rate_deg_per_day"]) / 86400.0
                    ),
                ),
                (moon["radius_km"] * 1000.0) if moon.get("radius_km") else None,
            )
        )
    return records


def moon_positions(when: datetime) -> list[dict[str, object]]:
    """Positions of every committed moon, heliocentric and ecliptic.

    A moon's elements are referred to its parent planet, so the parent's own position is added
    to place it in the same frame as everything else.

    Args:
        when: A timezone-aware instant.

    Returns:
        One entry per moon, in AU.
    """
    iso_tdb = _to_tdb_iso(when)
    when_jd = _to_julian_date(when)

    sun = _barycentric("sun", iso_tdb)
    parents: dict[str, tuple[float, float, float]] = {}

    results = []
    for name, planet, elements, radius_m in moon_elements():
        if planet not in parents:
            offset = to_ecliptic(_barycentric(planet, iso_tdb).minus(sun))
            parents[planet] = (offset.x, offset.y, offset.z)

        parent_x, parent_y, parent_z = parents[planet]
        x, y, z = position_at(elements, when_jd)

        results.append(
            {
                "name": name,
                "planet": planet,
                # Relative to the parent, which is what a close-up view needs.
                "relative_x_au": round(x / AU_M, 12),
                "relative_y_au": round(y / AU_M, 12),
                "relative_z_au": round(z / AU_M, 12),
                "distance_from_planet_km": round(math.sqrt(x * x + y * y + z * z) / 1000.0, 1),
                # And heliocentric, for the system view.
                "x_au": round((parent_x + x) / AU_M, 9),
                "y_au": round((parent_y + y) / AU_M, 9),
                "z_au": round((parent_z + z) / AU_M, 9),
                "radius_m": radius_m,
            }
        )

    return results


def asteroid_positions(when: datetime, *, limit: int = 800) -> list[dict[str, object]]:
    """Positions of the committed asteroids, heliocentric and ecliptic.

    Args:
        when: A timezone-aware instant.
        limit: How many to return, largest first.

    Returns:
        One entry per asteroid, in AU.
    """
    when_jd = _to_julian_date(when)

    results = []
    for record in _asteroid_data()["asteroids"][:limit]:
        semi_major_axis_m = record["semi_major_axis_au"] * AU_M

        elements = Elements(
            semi_major_axis_m=semi_major_axis_m,
            eccentricity=record["eccentricity"],
            inclination_rad=math.radians(record["inclination_deg"]),
            node_rad=math.radians(record["node_deg"]),
            periapsis_rad=math.radians(record["argument_of_periapsis_deg"]),
            mean_anomaly_rad=math.radians(record["mean_anomaly_deg"]),
            # SBDB does not return mean motion, so it comes from the semi-major axis and the
            # solar GM -- which is the definition, not an approximation.
            mean_motion_rad_per_s=math.sqrt(MU_SUN / semi_major_axis_m**3),
            epoch_jd=record["epoch_jd"],
        )

        x, y, z = position_at(elements, when_jd)

        results.append(
            {
                "name": record["name"].strip(),
                "class": record["class"],
                "x_au": round(x / AU_M, 8),
                "y_au": round(y / AU_M, 8),
                "z_au": round(z / AU_M, 8),
                "semi_major_axis_au": record["semi_major_axis_au"],
                "eccentricity": record["eccentricity"],
                "diameter_km": record["diameter_km"],
            }
        )

    return results


@lru_cache(maxsize=1)
def stars() -> dict:
    """Load the committed bright-star catalogue.

    Returned as-is: a star's position does not depend on when you ask, and the renderer wants
    the whole catalogue once rather than a query per frame.
    """
    path = DATA_DIR / "bright_stars.json"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} missing - run `uv run python tools/reference/fetch_stars.py`"
        )
    return json.loads(path.read_text(encoding="utf-8"))

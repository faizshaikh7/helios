"""Tests for moons, asteroids and the star catalogue.

The tier-3 test here graded four successive models, and the numbers are worth recording because
each step looked reasonable and only measurement told them apart:

| Model | Worst error, as a fraction of orbit radius |
|---|---|
| Two-body from one element set | 194% (Phobos at six months) |
| Plus fitted node and periapsis precession | 200% — *worse* |
| Plus fitted mean-longitude rate | 190% (Io, from a miscounted revolution) |
| Plus incremental cycle resolution | **1.3%** |

The second row is the instructive one. Adding a real physical effect made the answer worse,
because precessing the periapsis while still advancing the mean anomaly at the osculating mean
motion double-counts the precession — it changes the body's orbital rate. A model can be more
physical and less correct at the same time, and only a measurement against an independent source
shows it.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import app
from science import smallbodies

REFERENCE_PATH = Path(__file__).parent / "reference" / "moon_positions.json"

# Measured worst is 1.29%, for the Moon; see the module docstring.
MAX_POSITION_ERROR_FRACTION = 0.03

client = TestClient(app)


def _reference() -> list[dict[str, float]]:
    """True moon positions from JPL Horizons."""
    if not REFERENCE_PATH.exists():
        return []
    return json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))["positions"]


# --------------------------------------------------------------------------------------------
# Kepler's equation
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("eccentricity", [0.0, 0.001, 0.05, 0.3, 0.7, 0.95])
@pytest.mark.parametrize("mean_anomaly", [0.0, 0.7, 2.0, 3.14159, 4.5, 6.0, -2.5, 100.0])
def test_kepler_solution_satisfies_the_equation(eccentricity: float, mean_anomaly: float) -> None:
    """The returned eccentric anomaly actually solves ``M = E - e sin E``.

    Checked against the equation itself rather than against a table, so it cannot be circular,
    and swept up to e=0.95 where Newton's method is slowest to converge.
    """
    eccentric = smallbodies.solve_kepler(mean_anomaly, eccentricity)

    residual = eccentric - eccentricity * math.sin(eccentric)
    expected = math.remainder(mean_anomaly, math.tau)

    assert residual == pytest.approx(expected, abs=1e-9)


def test_a_hyperbolic_eccentricity_is_refused() -> None:
    """An eccentricity of 1 or more is not an ellipse and is rejected rather than diverging."""
    for bad in (1.0, 1.5, -0.1):
        with pytest.raises(ValueError):
            smallbodies.solve_kepler(1.0, bad)


# --------------------------------------------------------------------------------------------
# Moons
# --------------------------------------------------------------------------------------------


def test_moon_distances_match_their_known_orbits() -> None:
    """Each moon sits at roughly its published distance from its planet.

    A sanity check on the whole chain — elements, units, propagation — against figures anyone
    can look up. A metres-for-kilometres slip anywhere would show here as a factor of 1000.
    """
    expected_km = {
        "Moon": 384_400,
        "Phobos": 9_376,
        "Io": 421_800,
        "Europa": 671_100,
        "Ganymede": 1_070_400,
        "Callisto": 1_882_700,
        "Titan": 1_221_870,
        "Triton": 354_759,
    }

    positions = {item["name"]: item for item in smallbodies.moon_positions(datetime.now(UTC))}

    for name, distance in expected_km.items():
        measured = positions[name]["distance_from_planet_km"]
        # Generous, because these orbits are eccentric and the figure quoted is usually the mean.
        assert measured == pytest.approx(distance, rel=0.12), name


def test_moons_orbit_the_planet_they_belong_to() -> None:
    """A moon's heliocentric position is near its parent, not somewhere else in the system.

    Guards the frame arithmetic: the elements are planet-centred and the parent's own position
    is added to place them. Getting that wrong would leave Titan orbiting the Sun directly, at a
    perfectly plausible-looking distance.
    """
    when = datetime.now(UTC)
    response = client.post("/api/ephemeris/snapshot", json={"at_utc": when.isoformat()})
    planets = {item["body"]: item for item in response.json()["bodies"]}

    for moon in smallbodies.moon_positions(when):
        planet = planets[moon["planet"]]

        separation = math.dist(
            (moon["x_au"], moon["y_au"], moon["z_au"]),
            (planet["ecliptic_x_au"], planet["ecliptic_y_au"], planet["ecliptic_z_au"]),
        )

        # Every moon here orbits well inside 0.03 AU of its planet.
        assert separation < 0.03, f"{moon['name']} is {separation:.4f} AU from {moon['planet']}"


def test_moons_move():
    """Positions change with time, so the propagation is actually running."""
    now = datetime.now(UTC)
    later = now + timedelta(days=3)

    first = {item["name"]: item for item in smallbodies.moon_positions(now)}
    second = {item["name"]: item for item in smallbodies.moon_positions(later)}

    for name, item in first.items():
        moved = math.dist(
            (item["relative_x_au"], item["relative_y_au"], item["relative_z_au"]),
            (
                second[name]["relative_x_au"],
                second[name]["relative_y_au"],
                second[name]["relative_z_au"],
            ),
        )
        assert moved > 0, f"{name} did not move in three days"


@pytest.mark.parametrize(
    "case", _reference(), ids=lambda c: f"{c['name']}@{c['date_tdb']}"
)
def test_moon_positions_match_jpl_horizons(case: dict[str, float]) -> None:
    """Propagated moon positions agree with JPL Horizons within the measured bound.

    The bound is a fraction of each moon's own orbit radius, because that is what the error
    means: 1% of Phobos's orbit and 1% of Iapetus's are four hundred times apart in kilometres,
    and both are equally good.
    """
    elements = {name: element for name, _, element, _ in smallbodies.moon_elements()}

    when = datetime.fromisoformat(f"{case['date_tdb']}T00:00:00+00:00")
    x, y, z = smallbodies.position_at(
        elements[case["name"]], smallbodies._to_julian_date(when)
    )

    error_km = math.dist(
        (x / 1000.0, y / 1000.0, z / 1000.0), (case["x_km"], case["y_km"], case["z_km"])
    )
    orbit_radius_km = elements[case["name"]].semi_major_axis_m / 1000.0
    fraction = error_km / orbit_radius_km

    assert fraction < MAX_POSITION_ERROR_FRACTION, (
        f"{case['name']} at {case['date_tdb']}: off by {error_km:,.0f} km, "
        f"{fraction:.1%} of its orbit radius"
    )


# --------------------------------------------------------------------------------------------
# Asteroids
# --------------------------------------------------------------------------------------------


def test_the_belt_is_where_the_belt_is() -> None:
    """Most catalogued asteroids fall between Mars and Jupiter.

    Not a tautology: the elements are propagated through Kepler's equation and a frame rotation,
    and an error in either would scatter them anywhere.
    """
    positions = smallbodies.asteroid_positions(datetime.now(UTC), limit=400)

    radii = [
        math.sqrt(item["x_au"] ** 2 + item["y_au"] ** 2 + item["z_au"] ** 2)
        for item in positions
    ]
    in_belt = [radius for radius in radii if 2.0 < radius < 3.5]

    assert len(in_belt) / len(radii) > 0.7, "most large asteroids should be in the main belt"


def test_asteroid_distance_tracks_its_semi_major_axis() -> None:
    """Each asteroid's distance is consistent with its own orbit, not with the belt average.

    A propagation that ignored the elements and scattered objects across a plausible band would
    pass the previous test and fail this one.
    """
    for item in smallbodies.asteroid_positions(datetime.now(UTC), limit=200):
        radius = math.sqrt(item["x_au"] ** 2 + item["y_au"] ** 2 + item["z_au"] ** 2)
        semi_major_axis = item["semi_major_axis_au"]
        eccentricity = item["eccentricity"]

        # The bound comes from each object's own eccentricity rather than a blanket margin: an
        # orbit runs exactly between a(1-e) and a(1+e), and the selection includes Centaurs
        # eccentric enough that any fixed percentage either fails on them or is too loose to
        # catch anything on the rest.
        perihelion = semi_major_axis * (1.0 - eccentricity)
        aphelion = semi_major_axis * (1.0 + eccentricity)

        assert perihelion * 0.999 <= radius <= aphelion * 1.001, (
            f"{item['name']}: {radius:.3f} AU is outside its own orbit "
            f"[{perihelion:.3f}, {aphelion:.3f}]"
        )


def test_ceres_is_the_largest_and_is_in_the_belt() -> None:
    """A named check that the catalogue is what it claims to be."""
    first = smallbodies.asteroid_positions(datetime.now(UTC), limit=1)[0]

    assert "Ceres" in first["name"]
    assert 2.5 < first["semi_major_axis_au"] < 3.0


# --------------------------------------------------------------------------------------------
# Stars
# --------------------------------------------------------------------------------------------


def test_the_star_catalogue_is_real_and_complete_enough() -> None:
    """The catalogue holds thousands of stars with valid coordinates and magnitudes."""
    data = smallbodies.stars()

    assert data["count"] > 5000

    for star in data["stars"][:500]:
        assert 0.0 <= star["ra_deg"] < 360.0
        assert -90.0 <= star["dec_deg"] <= 90.0
        assert -2.0 < star["vmag"] < 7.0


def test_sirius_is_the_brightest_star_and_is_where_it_should_be() -> None:
    """The brightest entry is Sirius, at its real position.

    Checked against the sky rather than against the file: Sirius is the brightest star as seen
    from Earth, at right ascension 6h45m and declination -16.7 degrees. If the coordinate parser
    mishandled sexagesimal input, this is where it shows.
    """
    brightest = smallbodies.stars()["stars"][0]

    assert brightest["vmag"] < -1.4
    assert brightest["ra_deg"] == pytest.approx(101.287, abs=0.05)
    assert brightest["dec_deg"] == pytest.approx(-16.716, abs=0.05)


def test_negative_declinations_survive_the_parser() -> None:
    """Stars exist in both hemispheres, in roughly equal numbers.

    A sign dropped while parsing "-00 30 00" would fold the southern sky into the northern one,
    and every constellation below the equator would be mirrored.
    """
    declinations = [star["dec_deg"] for star in smallbodies.stars()["stars"]]

    southern = sum(1 for value in declinations if value < 0)
    fraction = southern / len(declinations)

    assert 0.35 < fraction < 0.65, f"{fraction:.0%} of stars are southern, which is lopsided"


# --------------------------------------------------------------------------------------------
# API contract
# --------------------------------------------------------------------------------------------


def test_endpoints_state_their_model_and_selection() -> None:
    """Moons and asteroids both declare what the model ignores and what was left out.

    Both routes return a *selection* propagated by a *simplified* model. Serving either without
    saying so would invite a reader to take the picture as complete and exact.
    """
    moons = client.post("/api/moons", json={}).json()
    assert "two-body" in moons["model"].lower()
    assert "major moons only" in moons["selection"].lower()

    asteroids = client.post("/api/asteroids", json={"limit": 10}).json()
    assert "epoch" in asteroids["model"].lower()
    assert "not the whole belt" in asteroids["selection"].lower()

    stars = client.get("/api/stars").json()
    assert stars["tier"] == "observed"
    assert "Yale" in stars["provenance"]["source"]

"""Tests for planetary positions.

Two layers:

* **Tier 1** -- properties that hold whatever ephemeris is underneath: the ordering of the
  planets, the Sun's position at the equinoxes (checkable against the definition of the equinox
  rather than against another library), light-time actually being applied, and the time scale
  being converted rather than assumed.
* **Tier 3** -- every body at every epoch against Orekit reading the JPL DE ephemeris.

The tier-3 bound is **relative**, at 1e-3. Measured worst departure across all ten bodies over
2000-2040 is 3.0e-4, at Uranus. A relative bound is the right shape here because the analytic
series' error scales with distance: an absolute bound tight enough to be meaningful for the Moon
would reject Uranus, and one loose enough for Uranus would let a gross Moon error through.

The Sun is graded on an absolute bound instead. The barycentre lies inside it, so its distance
from the origin runs close to zero and a relative figure there measures the choice of origin
rather than the ephemeris. Its absolute error is in fact the smallest of any body.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import pytest

from science import ephemeris
from science.provenance import Tier

REFERENCE_PATH = Path(__file__).parent / "reference" / "ephemeris.json"

# Measured worst is 3.0e-4, at Uranus; see module docstring.
MAX_RELATIVE_ERROR = 1e-3

# Below this barycentric distance a relative bound is meaningless, because the denominator is
# near zero. Only the Sun is affected: the solar-system barycentre lies inside it, so its
# distance from the origin passes close to nothing while its absolute error stays the smallest
# of any body (113 km measured). Grading it on the fraction of a near-zero distance would be
# measuring the choice of origin, not the ephemeris.
RELATIVE_BOUND_MIN_DISTANCE_AU = 0.01

# Absolute bound for such bodies. Measured worst for the Sun is 119 km.
MAX_ABSOLUTE_ERROR_KM = 1000.0

AU_M = ephemeris.AU_M


def _load() -> list[dict[str, float]]:
    """Reference positions, for parametrizing."""
    if not REFERENCE_PATH.exists():
        return []
    return json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))["positions"]


# --------------------------------------------------------------------------------------------
# Tier 1: properties
# --------------------------------------------------------------------------------------------


def test_planets_are_ordered_outward_from_the_sun() -> None:
    """Heliocentric distances increase in the expected planetary order.

    Cheap, but it catches the failure that matters most: a body name silently resolving to the
    wrong body. Every position would still look like a perfectly plausible position.
    """
    when = datetime(2026, 8, 18, tzinfo=UTC)
    sun = ephemeris._barycentric("sun", ephemeris._to_tdb_iso(when))

    order = ("mercury", "venus", "earth", "mars", "jupiter", "saturn", "uranus", "neptune")
    distances = [
        ephemeris._barycentric(name, ephemeris._to_tdb_iso(when)).minus(sun).norm
        for name in order
    ]

    for (inner, outer), (inner_name, outer_name) in zip(
        pairwise(distances), pairwise(order), strict=True
    ):
        assert inner < outer, f"{inner_name} is not inside {outer_name}"


def test_the_sun_sits_at_the_equinox_when_it_should() -> None:
    """At the March equinox the Sun's right ascension is ~0h; at September, ~12h.

    This is graded against the *definition* of an equinox rather than against another library,
    so it is a genuinely independent check: the equinox is the instant the Sun crosses the
    celestial equator northward, where RA is 0 and declination is 0 by construction.
    """
    march = datetime(2026, 3, 20, 14, 46, tzinfo=UTC)
    september = datetime(2026, 9, 23, 0, 5, tzinfo=UTC)

    march_position = ephemeris.apparent_from_earth("sun", march)
    september_position = ephemeris.apparent_from_earth("sun", september)

    # Within a degree: the equinox instants above are rounded to the minute, and the Sun moves
    # about a degree a day, so a tighter bound would be testing the timestamps not the ephemeris.
    assert min(
        march_position["right_ascension"].value, 360 - march_position["right_ascension"].value
    ) < 1.0
    assert abs(march_position["declination"].value) < 1.0

    assert abs(september_position["right_ascension"].value - 180.0) < 1.0
    assert abs(september_position["declination"].value) < 1.0


def test_the_suns_declination_swings_between_the_tropics() -> None:
    """Solstice declinations reach +/-23.4 degrees, the obliquity of the ecliptic.

    Another check against a physical constant rather than a library: the tropics are *defined*
    by this angle.
    """
    june = ephemeris.apparent_from_earth("sun", datetime(2026, 6, 21, 8, 24, tzinfo=UTC))
    december = ephemeris.apparent_from_earth("sun", datetime(2026, 12, 21, 20, 50, tzinfo=UTC))

    assert 23.0 < june["declination"].value < 23.6
    assert -23.6 < december["declination"].value < -23.0


def test_earth_stays_about_one_au_from_the_sun_all_year() -> None:
    """Earth's distance from the Sun varies between roughly 0.983 and 1.017 AU.

    The bounds are perihelion and aphelion. A frame or unit error would leave the mean intact
    while destroying this range, so the range is what is asserted.
    """
    start = datetime(2026, 1, 1, tzinfo=UTC)
    distances = []

    for day in range(0, 365, 5):
        when = start + timedelta(days=day)
        iso = ephemeris._to_tdb_iso(when)
        separation = ephemeris._barycentric("earth", iso).minus(ephemeris._barycentric("sun", iso))
        distances.append(separation.norm / AU_M)

    assert 0.980 < min(distances) < 0.988, f"perihelion out of range: {min(distances):.4f} AU"
    assert 1.014 < max(distances) < 1.020, f"aphelion out of range: {max(distances):.4f} AU"


def test_light_travel_times_match_the_distances() -> None:
    """Reported light time equals distance over c.

    Guards against the light-time field being computed from a different quantity than the
    distance beside it -- the two would still each look reasonable on their own.
    """
    when = datetime(2026, 8, 18, tzinfo=UTC)

    for body in ("sun", "moon", "mars", "jupiter", "neptune"):
        result = ephemeris.apparent_from_earth(body, when)

        distance_m = result["distance"].value * AU_M
        expected_minutes = distance_m / ephemeris.C_M_PER_S / 60.0

        assert result["light_travel_time"].value == pytest.approx(expected_minutes, rel=1e-6)


def test_light_time_correction_actually_moves_the_body() -> None:
    """Correcting for light travel changes the answer measurably.

    Without this, a light-time solver that quietly did nothing would pass every other test here:
    the positions would still be plausible, just wrong by tens of arcseconds. Jupiter's light
    time is around 50 minutes, over which it moves far more than the ephemeris error.
    """
    when = datetime(2026, 8, 18, tzinfo=UTC)
    iso = ephemeris._to_tdb_iso(when)

    observer = ephemeris._barycentric("earth", iso)
    geometric = ephemeris._barycentric("jupiter", iso).minus(observer)

    geometric_ra = math.degrees(math.atan2(geometric.y, geometric.x)) % 360.0
    corrected_ra = ephemeris.apparent_from_earth("jupiter", when)["right_ascension"].value

    separation_arcsec = abs(geometric_ra - corrected_ra) * 3600.0

    assert separation_arcsec > 5.0, (
        f"light-time correction moved Jupiter by only {separation_arcsec:.1f} arcsec; "
        "it appears not to be applied"
    )


def test_utc_is_converted_to_tdb_rather_than_assumed() -> None:
    """The TDB conversion is applied, and it is the ~69 s offset it should be.

    Treating UTC as TDB would displace the Moon by roughly 900 km -- seven times this
    ephemeris's own error for the Moon, so the shortcut would dominate every other inaccuracy
    while leaving every position looking entirely reasonable.
    """
    when = datetime(2026, 8, 18, tzinfo=UTC)
    tdb_iso = ephemeris._to_tdb_iso(when)

    offset_seconds = (
        datetime.fromisoformat(tdb_iso).replace(tzinfo=UTC) - when
    ).total_seconds()

    # TT - UTC is 69.184 s in this era; TDB differs from TT by under 2 ms.
    assert 69.0 < offset_seconds < 69.4


def test_a_naive_timestamp_is_treated_as_utc() -> None:
    """A timestamp with no offset is interpreted as UTC, not as server-local time."""
    aware = datetime(2026, 8, 18, tzinfo=UTC)
    naive = datetime(2026, 8, 18)  # noqa: DTZ001 - the point of the test

    assert ephemeris._to_tdb_iso(aware) == ephemeris._to_tdb_iso(naive)


# --------------------------------------------------------------------------------------------
# Tier 1: provenance
# --------------------------------------------------------------------------------------------


def test_positions_are_derived_and_state_their_measured_accuracy() -> None:
    """Every position is tiered derived and carries the measured departure from JPL.

    Derived, never observed: nobody measured Uranus at this coordinate. It is computed from a
    fitted model, and the receipt says how far that model was found to be off.
    """
    result = ephemeris.barycentric_position("uranus", datetime(2026, 8, 18, tzinfo=UTC))

    for value in result.values():
        assert value.tier is Tier.DERIVED
        assert value.unit == "AU"

        uncertainty = value.receipt.uncertainty
        assert uncertainty["max_error_km"] == ephemeris.ACCURACY["uranus"]["max_error_km"]
        assert "not fit" in " ".join(uncertainty).lower() or "not_fit_for" in uncertainty
        assert "Navigation" in uncertainty["not_fit_for"]

        assert value.receipt.frame == "ICRF (barycentric)"
        assert "TDB" in value.receipt.time_scale


def test_apparent_positions_disclose_what_was_not_corrected() -> None:
    """Aberration and topocentric parallax are named as omissions.

    Both are larger than the ephemeris error they sit beside -- parallax moves the Moon by up to
    a degree -- so leaving them unstated would make the reported precision misleading.
    """
    notes = ephemeris.apparent_from_earth("moon", datetime(2026, 8, 18, tzinfo=UTC))[
        "declination"
    ].receipt.notes

    assert "aberration" in notes.lower()
    assert "topocentric" in notes.lower()
    assert "light travel time" in notes.lower()


def test_unsupported_bodies_are_refused_with_a_useful_message() -> None:
    """An unknown body names what is available rather than failing opaquely."""
    when = datetime(2026, 8, 18, tzinfo=UTC)

    for name in ("pluto", "ceres", "kepler-186f", ""):
        with pytest.raises(ephemeris.EphemerisError) as caught:
            ephemeris.barycentric_position(name, when)

        assert "mercury" in str(caught.value), "the error should list what is supported"


def test_the_earth_has_no_apparent_position_in_its_own_sky() -> None:
    """Asking where Earth appears from Earth is refused rather than answered with noise."""
    with pytest.raises(ephemeris.EphemerisError):
        ephemeris.apparent_from_earth("earth", datetime(2026, 8, 18, tzinfo=UTC))


def test_snapshot_covers_every_body_and_states_its_limits() -> None:
    """The rendering snapshot is complete and carries one accuracy statement for the set."""
    result = ephemeris.snapshot(datetime(2026, 8, 18, tzinfo=UTC))

    assert {item["body"] for item in result["bodies"]} == set(ephemeris.BODIES)
    assert result["tier"] == "derived"
    assert "navigation" in result["accuracy"].lower()

    for item in result["bodies"]:
        assert item["radius_m"] > 0
        assert item["colour"].startswith("#")


# --------------------------------------------------------------------------------------------
# Tier 3: against Orekit's JPL ephemeris
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case", _load(), ids=lambda c: f"{c['body']}@{c['epoch_tdb'][:10]}"
)
def test_matches_the_jpl_ephemeris_within_the_measured_bound(case: dict[str, float]) -> None:
    """Every body at every epoch agrees with Orekit's JPL ephemeris relative to its distance."""
    position = ephemeris._barycentric(case["body"], case["epoch_tdb"])

    dx = position.x - case["x_m"]
    dy = position.y - case["y_m"]
    dz = position.z - case["z_m"]

    error_m = math.sqrt(dx * dx + dy * dy + dz * dz)
    distance_m = math.sqrt(case["x_m"] ** 2 + case["y_m"] ** 2 + case["z_m"] ** 2)

    if distance_m / AU_M < RELATIVE_BOUND_MIN_DISTANCE_AU:
        assert error_m / 1000.0 < MAX_ABSOLUTE_ERROR_KM, (
            f"{case['body']} at {case['epoch_tdb']}: off by {error_m / 1000:,.0f} km, over the "
            f"{MAX_ABSOLUTE_ERROR_KM:,.0f} km bound applied near the barycentre"
        )
        return

    relative = error_m / distance_m

    assert relative < MAX_RELATIVE_ERROR, (
        f"{case['body']} at {case['epoch_tdb']}: off by {error_m / 1000:,.0f} km "
        f"({relative:.2e} of {distance_m / AU_M:.3f} AU), over the {MAX_RELATIVE_ERROR:.0e} bound"
    )


def test_the_published_accuracy_table_is_not_optimistic() -> None:
    """The `ACCURACY` figures shown to users are at least as large as the real disagreement.

    This is the test that keeps the module honest. The table is what a reader sees on every
    value; if the real error ever exceeded it, the tool would be understating its own
    uncertainty -- which is worse than having no figure at all, because it invites trust.
    """
    if not _load():
        pytest.skip("ephemeris.json not generated")

    worst: dict[str, float] = {}
    for case in _load():
        position = ephemeris._barycentric(case["body"], case["epoch_tdb"])
        error_km = (
            math.sqrt(
                (position.x - case["x_m"]) ** 2
                + (position.y - case["y_m"]) ** 2
                + (position.z - case["z_m"]) ** 2
            )
            / 1000.0
        )
        worst[case["body"]] = max(worst.get(case["body"], 0.0), error_km)

    for body, measured_km in worst.items():
        published_km = ephemeris.ACCURACY[body]["max_error_km"]

        # A small tolerance so rounding the published figure cannot fail the test, while a real
        # regression still does.
        assert measured_km <= published_km * 1.01, (
            f"{body}: real error {measured_km:,.0f} km exceeds the published "
            f"{published_km:,.0f} km. The stated accuracy is optimistic."
        )


def test_reference_records_its_method() -> None:
    """The reference states how it was produced."""
    if not REFERENCE_PATH.exists():
        pytest.skip("ephemeris.json not generated")

    provenance = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))["_provenance"]

    assert provenance["source"] == "orekit"
    assert "JPL" in provenance["method"]
    assert provenance["time_scale"] == "TDB"

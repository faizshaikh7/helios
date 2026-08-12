"""Tests for orbital elements, state vectors, and derived orbit properties.

Two tiers run here, and .agent/test.md is explicit that they carry unequal authority.

**Tier 1 -- physical and analytic sanity.** Closed-form identities and known ISS geometry, run
against a **frozen element set** so they never touch the network. These catch the two failure
classes that are worth more than everything else in this file: a unit error, and a wrong altitude
datum. Both produce numbers that look entirely reasonable. An ISS "altitude" of 6784 km is an apsis
radius mislabelled; an altitude of 406 km is a spherical-Earth approximation; the answer is about
417 km, a height above the WGS84 ellipsoid at the latitude the perigee actually occurs.

**Tier 3 -- differential against Orekit.** The headline truth source, comparing against values
committed in `tests/reference/elements.json` and regenerated deliberately by
`tools/reference/generate_elements.py`. When that file is absent these tests skip rather than fail,
so a clone without generated references still runs green on tier 1.

The frozen TLE below is the same one `tests/reference/tle_propagation.json` uses, so both reference
sets describe the same satellite at the same instants.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from skyfield.api import wgs84

from science import catalog, elements, orbit
from science.provenance import Tier, Value

REFERENCE_PATH = Path(__file__).parent / "reference" / "elements.json"

# ISS (ZARYA), frozen so results are reproducible. Identical to the propagation and element
# references -- see module docstring.
FIXTURE_TLE = catalog.TLE(
    name="ISS (ZARYA)",
    line1="1 25544U 98067A   24001.50000000  .00016717  00000-0  30150-3 0  9004",
    line2="2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.49815310 10000",
    norad_id=25544,
    source="test-fixture",
    fetched_at_unix=time.time(),
)

# Fields printed on the element set itself, for comparison against the osculating values computed
# from a state vector. They are *mean* elements in the Brouwer-Lyddane sense, so exact agreement is
# not expected -- and would in fact indicate the short-period terms had been dropped somewhere.
TLE_INCLINATION_DEG = 51.6416
TLE_ECCENTRICITY = 0.0006703
TLE_MEAN_MOTION_REV_PER_DAY = 15.49815310

# Tolerances for the tier-3 comparison.
#
# The skyfield and Orekit TEME states agree to under 0.1 mm (measured in tests/test_propagation.py
# against the same frozen TLE), and both sides reduce that state to elements under the same WGS72
# mu, which the reference records and a guard below checks. So the element tolerances are set by
# what that state agreement propagates to, with several orders of magnitude of headroom, while
# staying far under any real defect:
#
#   * 1 m on the semi-major axis, against a mu mix-up (several metres) or a km/m error (millions).
#   * 1e-6 deg on angles -- 3.6 mas, about 0.1 m of along-track position at this altitude --
#     against a quadrant error in the element reduction, which is degrees.
#   * 5 m on apsis altitudes. The two implementations place the apsis in Earth-fixed coordinates
#     differently: this module fixes longitude and relies on the ellipsoid being a surface of
#     revolution, while Orekit applies the full TEME->ITRF rotation including polar motion (~0.3
#     arcsec, worth roughly 0.03 m of height here). A wrong datum is 6370 km out, and a spherical
#     Earth about 11 km, so 5 m separates them unambiguously.
POSITION_TOLERANCE_M = 1.0
VELOCITY_TOLERANCE_M_S = 0.01
SEMI_MAJOR_AXIS_TOLERANCE_M = 1.0
ECCENTRICITY_TOLERANCE = 1e-9
ANGLE_TOLERANCE_DEG = 1e-6
PERIOD_TOLERANCE_S = 1e-3
MEAN_MOTION_TOLERANCE_REV_PER_DAY = 1e-6
ALTITUDE_TOLERANCE_M = 5.0


def _epoch() -> datetime:
    """Return the frozen element set's own epoch, as a timezone-aware UTC datetime."""
    return orbit.tle_epoch(FIXTURE_TLE)


def _reference() -> dict[str, Any]:
    """Load the committed Orekit element reference."""
    return json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))


def _states() -> list[dict[str, Any]]:
    """Return reference states, for parametrizing tests.

    Returns an empty list when the reference has not been generated, which leaves the
    parametrized tests uncollected rather than failing a fresh clone.
    """
    if not REFERENCE_PATH.exists():
        return []
    return _reference()["states"]


def _require_reference() -> dict[str, Any]:
    """Load the reference document or skip the calling test."""
    if not REFERENCE_PATH.exists():
        pytest.skip(
            f"{REFERENCE_PATH.name} not generated - run "
            "`uv run python tools/reference/generate_elements.py`"
        )
    return _reference()


def _angle_difference_deg(measured: float, expected: float) -> float:
    """Smallest absolute difference between two angles, in degrees.

    Angles wrap, so a plain subtraction reports 359.9999 degrees of disagreement for two values
    that are a ten-thousandth of a degree apart across zero.

    Args:
        measured: Angle produced by this project, degrees.
        expected: Reference angle, degrees.

    Returns:
        Absolute separation in degrees, in [0, 180].
    """
    return abs((measured - expected + 180.0) % 360.0 - 180.0)


def _at(when: datetime) -> dict[str, dict[str, Value]]:
    """Compute all three element products at one instant, for tests that need several."""
    return {
        "state": elements.state_vector(FIXTURE_TLE, when),
        "elements": elements.classical_elements(FIXTURE_TLE, when),
        "derived": elements.derived_orbit_properties(FIXTURE_TLE, when),
    }


# --------------------------------------------------------------------------------------------
# Tier 1 -- physical sanity: units, datum, and known ISS geometry
# --------------------------------------------------------------------------------------------


def test_semi_major_axis_is_measured_from_earths_centre() -> None:
    """The ISS semi-major axis is about 6790 km, not about 420 km.

    A semi-major axis is a distance from the focus -- Earth's centre -- so a value in the hundreds
    of kilometres means an altitude was returned in its place. That confusion changes the period by
    a factor of about sixty and would still print a plausible-looking number.
    """
    semi_major_axis = elements.classical_elements(FIXTURE_TLE, _epoch())["semi_major_axis"]

    assert semi_major_axis.unit == "m"
    axis_km = semi_major_axis.value / 1e3
    assert 6700 < axis_km < 6900, (
        f"semi-major axis {axis_km:.1f} km; the ISS sits near 6790 km from Earth's centre. "
        "A few hundred km would mean an altitude was returned instead."
    )


def test_eccentricity_and_inclination_track_the_element_set() -> None:
    """Osculating eccentricity and inclination stay close to the TLE's own fields.

    Close, not equal: the TLE prints mean elements and these are osculating, so short-period terms
    make them differ by hundredths of a degree. A disagreement of degrees would mean a mis-parsed
    TLE field; a factor of 57.3 would mean radians were reported as degrees.
    """
    computed = elements.classical_elements(FIXTURE_TLE, _epoch())

    assert computed["eccentricity"].unit == "none"
    assert 0.0 < computed["eccentricity"].value < 0.01, "the ISS orbit is very nearly circular"
    assert computed["eccentricity"].value == pytest.approx(TLE_ECCENTRICITY, abs=1e-3)

    assert computed["inclination"].unit == "deg"
    assert computed["inclination"].value == pytest.approx(TLE_INCLINATION_DEG, abs=0.1), (
        "inclination should sit within a tenth of a degree of the element set's 51.6416 deg"
    )


def test_all_angles_are_reported_in_degrees_within_range() -> None:
    """Every angular element is a degree value inside its natural range.

    A radian value leaking out where degrees are declared is the quietest unit bug available: 0.9
    rad and 0.9 deg are both perfectly plausible-looking inclinations.
    """
    computed = elements.classical_elements(FIXTURE_TLE, _epoch())

    assert 0.0 <= computed["inclination"].value <= 180.0
    for name in ("raan", "argument_of_perigee", "true_anomaly"):
        assert computed[name].unit == "deg"
        assert 0.0 <= computed[name].value < 360.0, f"{name} out of range"

    # At least one angle must exceed 2*pi, or the whole set could be radians and still pass above.
    assert max(computed[n].value for n in ("raan", "argument_of_perigee", "true_anomaly")) > 7.0


def test_orbital_period_is_about_ninety_three_minutes() -> None:
    """The ISS period is 92-93 minutes.

    The period scales as a^(3/2), so it is the most sensitive check on the semi-major axis being
    both correct and in metres: a kilometre/metre error moves it by a factor of about 31600.
    """
    period = elements.derived_orbit_properties(FIXTURE_TLE, _epoch())["orbital_period"]

    assert period.unit == "s"
    minutes = period.value / 60.0
    assert 92.0 < minutes < 93.0, f"orbital period {minutes:.3f} min; the ISS orbits in about 92.8"


def test_mean_motion_matches_the_element_set() -> None:
    """Osculating mean motion stays within a tenth of a rev/day of the TLE's printed value."""
    mean_motion = elements.derived_orbit_properties(FIXTURE_TLE, _epoch())["mean_motion"]

    assert mean_motion.unit == "rev/day"
    assert mean_motion.value == pytest.approx(TLE_MEAN_MOTION_REV_PER_DAY, abs=0.1)


def test_period_and_mean_motion_are_reciprocal() -> None:
    """Mean motion times period is one day, exactly, by definition.

    A closed-form identity between two values this project reports separately. It cannot be
    satisfied by accident if either carries the wrong unit -- rad/s instead of rev/day, or minutes
    instead of seconds.
    """
    derived = elements.derived_orbit_properties(FIXTURE_TLE, _epoch())
    product = derived["mean_motion"].value * derived["orbital_period"].value

    assert product == pytest.approx(86400.0, abs=1e-2)


def test_vis_viva_holds_between_the_state_vector_and_the_elements() -> None:
    """The state vector and the elements satisfy vis-viva: v^2 = mu (2/r - 1/a).

    A textbook closed-form identity linking the two public functions. It is a regression and
    units check rather than an accuracy claim -- both sides come from the same propagation -- but
    it is exactly what breaks if one function starts reporting kilometres while the other reports
    metres.
    """
    epoch = _epoch()
    state = elements.state_vector(FIXTURE_TLE, epoch)
    semi_major_axis_m = elements.classical_elements(FIXTURE_TLE, epoch)["semi_major_axis"].value

    radius_m = state["radius"].value
    speed_m_s = state["speed"].value

    expected = elements.EARTH_MU_M3_S2 * (2.0 / radius_m - 1.0 / semi_major_axis_m)

    assert speed_m_s**2 == pytest.approx(expected, rel=1e-9)


def test_apsis_altitudes_are_heights_above_the_ellipsoid_not_radii() -> None:
    """Apogee and perigee altitudes are a few hundred km, not a few thousand.

    The classic silent error: returning the apsis *radius* (about 6784 km for the ISS perigee)
    where an altitude was asked for. Both are metres, both are positive, and only the magnitude
    gives it away.
    """
    derived = elements.derived_orbit_properties(FIXTURE_TLE, _epoch())

    for name in ("perigee_altitude", "apogee_altitude"):
        assert derived[name].unit == "m"
        altitude_km = derived[name].value / 1e3
        assert 300 < altitude_km < 500, (
            f"{name} is {altitude_km:.1f} km; the ISS flies a few hundred km up. A value near "
            "6800 km would be an apsis radius measured from Earth's centre."
        )

    assert derived["apogee_altitude"].value > derived["perigee_altitude"].value


def test_apsis_altitude_separation_equals_twice_a_times_e() -> None:
    """Apogee minus perigee altitude is 2ae, tying the altitudes back to the element set.

    Both apsides sit at the same magnitude of latitude, so the ellipsoid radius subtracted from
    each is the same and the difference must reduce to the ellipse's own geometry. This catches an
    apogee and perigee computed from different orbits, or an apsis radius formed with the wrong
    sign on the eccentricity.
    """
    epoch = _epoch()
    computed = elements.classical_elements(FIXTURE_TLE, epoch)
    derived = elements.derived_orbit_properties(FIXTURE_TLE, epoch)

    separation_m = derived["apogee_altitude"].value - derived["perigee_altitude"].value
    expected_m = 2.0 * computed["semi_major_axis"].value * computed["eccentricity"].value

    assert separation_m == pytest.approx(expected_m, abs=1.0)


def test_altitude_uses_the_ellipsoid_and_not_a_sphere() -> None:
    """The altitude datum is the WGS84 ellipsoid, not a sphere of any radius.

    The strongest available check on the datum without an independent implementation. The ISS
    perigee occurs near 46 degrees latitude, where the ellipsoid is about 9 km smaller in radius
    than at the equator, so measured against a sphere of equatorial radius the correct answer sits
    about 11.2 km high. The two spherical approximations people actually reach for land elsewhere:
    the equatorial radius itself gives 0 km of excess, and the popular 6371 km mean radius gives
    about 7.1 km. Bracketing between 9 km and the ellipsoid's own 21 km of equator-to-pole
    flattening excludes both while leaving room for the orbit to precess.
    """
    epoch = _epoch()
    computed = elements.classical_elements(FIXTURE_TLE, epoch)
    derived = elements.derived_orbit_properties(FIXTURE_TLE, epoch)

    perigee_radius_m = computed["semi_major_axis"].value * (1.0 - computed["eccentricity"].value)
    spherical_equatorial_m = perigee_radius_m - wgs84.radius.m
    flattening_span_m = wgs84.radius.m - wgs84.polar_radius.m

    excess_m = derived["perigee_altitude"].value - spherical_equatorial_m

    assert 9_000 < excess_m < flattening_span_m, (
        f"perigee altitude sits {excess_m:.1f} m above the equatorial-sphere answer; expected "
        f"about 11.2 km, and never more than the {flattening_span_m:.0f} m the ellipsoid "
        "flattens by. Roughly 7 km would mean a 6371 km mean sphere; zero, the equatorial radius."
    )


def test_state_vector_is_physically_plausible_and_self_consistent() -> None:
    """Position and velocity magnitudes match low Earth orbit and match their own components."""
    state = elements.state_vector(FIXTURE_TLE, _epoch())

    components_m = np.array([state[f"position_{axis}"].value for axis in "xyz"])
    speeds_m_s = np.array([state[f"velocity_{axis}"].value for axis in "xyz"])

    assert state["radius"].value == pytest.approx(float(np.linalg.norm(components_m)), abs=1e-3)
    assert state["speed"].value == pytest.approx(float(np.linalg.norm(speeds_m_s)), abs=1e-3)

    assert 6.5e6 < state["radius"].value < 7.2e6, "ISS radius is about 6785 km from Earth's centre"
    assert 7.4e3 < state["speed"].value < 7.9e3, "LEO orbital speed is about 7.7 km/s"


def test_state_radius_lies_between_the_apsis_radii() -> None:
    """The instantaneous radius sits inside the osculating ellipse it was reduced to.

    Trivially true if everything is consistent, and immediately false if the elements were built
    from a different epoch's state than the one reported.
    """
    epoch = _epoch()
    state = elements.state_vector(FIXTURE_TLE, epoch)
    computed = elements.classical_elements(FIXTURE_TLE, epoch)

    axis_m = computed["semi_major_axis"].value
    eccentricity = computed["eccentricity"].value

    assert axis_m * (1.0 - eccentricity) - 1e-3 <= state["radius"].value
    assert state["radius"].value <= axis_m * (1.0 + eccentricity) + 1e-3


# --------------------------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------------------------


def test_every_value_states_unit_frame_time_scale_and_tier() -> None:
    """No bare float leaves this module.

    rules.md requires frame, time scale, and unit on every value that crosses a boundary. This
    walks all three products rather than sampling one, because the field that gets forgotten is
    always on the value nobody wrote a test for.
    """
    for group in _at(_epoch()).values():
        for name, value in group.items():
            assert value.unit, f"{name} has no unit"
            assert value.tier in {Tier.DERIVED, Tier.PREDICTED}, name
            assert value.receipt.frame, f"{name} does not state a reference frame"
            assert value.receipt.time_scale == "UTC", name
            assert value.receipt.dataset, f"{name} does not state which element set produced it"
            assert value.receipt.uncertainty, f"{name} carries no uncertainty statement"


def test_state_and_elements_declare_teme() -> None:
    """Positions, velocities, and elements say they are TEME.

    SGP4 emits TEME. Labelling it GCRF displaces a LEO satellite by roughly 44 km while every
    number still looks correct, so the frame string is asserted rather than trusted.
    """
    products = _at(_epoch())

    for group in ("state", "elements"):
        for name, value in products[group].items():
            assert "TEME" in value.receipt.frame, f"{name} does not declare TEME"

    for name in ("apogee_altitude", "perigee_altitude"):
        frame = products["derived"][name].receipt.frame
        assert "WGS84" in frame, f"{name} must state the ellipsoid its height is measured from"


def test_altitude_receipt_names_the_datum() -> None:
    """The altitude receipt says the height is above the ellipsoid, not from Earth's centre.

    A reader cannot tell 417 km from 6784 km apart by inspection of a single number. The receipt
    is where the distinction has to be recorded, so its absence is a defect in its own right.
    """
    receipt = elements.derived_orbit_properties(FIXTURE_TLE, _epoch())["perigee_altitude"].receipt

    assert "WGS84 ellipsoid" in receipt.notes
    assert "centre" in receipt.notes


def test_tier_weakens_away_from_epoch() -> None:
    """At the element set's own epoch values are derived; propagated forward they are predicted.

    Provenance is monotonic, so a propagated state may never be reported as derived no matter how
    exact the element reduction applied to it.
    """
    epoch = _epoch()

    for group in _at(epoch).values():
        for name, value in group.items():
            assert value.tier is Tier.DERIVED, f"{name} at epoch should be derived"

    for group in _at(epoch + timedelta(days=1)).values():
        for name, value in group.items():
            assert value.tier is Tier.PREDICTED, f"{name} a day from epoch should be predicted"


def test_uncertainty_grows_with_distance_from_epoch() -> None:
    """The stated SGP4 error is larger a day out than at epoch."""
    epoch = _epoch()

    at_epoch = elements.state_vector(FIXTURE_TLE, epoch)["radius"].receipt.uncertainty
    later = elements.state_vector(FIXTURE_TLE, epoch + timedelta(days=1))["radius"].receipt

    assert later.uncertainty["position_error_km"] > at_epoch["position_error_km"]
    assert later.uncertainty["age_from_epoch_days"] == pytest.approx(1.0, abs=1e-3)


# --------------------------------------------------------------------------------------------
# Tier 3 -- differential against Orekit
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("state", _states(), ids=lambda s: f"+{s['offset_minutes']}min")
def test_state_vector_matches_orekit(state: dict[str, Any]) -> None:
    """TEME position and velocity agree with Orekit at each offset from epoch.

    Checked here as well as in `test_propagation.py` because this module reads the state through
    skyfield's frame machinery rather than calling `sgp4` directly, and a frame mix-up in that
    step would otherwise only surface as a subtly wrong RAAN.
    """
    when = _epoch() + timedelta(minutes=state["offset_minutes"])
    measured = elements.state_vector(FIXTURE_TLE, when)

    position = np.array([measured[f"position_{axis}"].value for axis in "xyz"])
    velocity = np.array([measured[f"velocity_{axis}"].value for axis in "xyz"])

    position_error = float(np.linalg.norm(position - np.array(state["position_teme_m"])))
    velocity_error = float(np.linalg.norm(velocity - np.array(state["velocity_teme_m_s"])))

    assert position_error <= POSITION_TOLERANCE_M, (
        f"TEME position disagreement at +{state['offset_minutes']} min: {position_error:.6f} m"
    )
    assert velocity_error <= VELOCITY_TOLERANCE_M_S, (
        f"TEME velocity disagreement at +{state['offset_minutes']} min: {velocity_error:.6f} m/s"
    )


@pytest.mark.parametrize("state", _states(), ids=lambda s: f"+{s['offset_minutes']}min")
def test_classical_elements_match_orekit(state: dict[str, Any]) -> None:
    """The six classical elements agree with Orekit at each offset from epoch.

    Angles are compared with wraparound. A disagreement of degrees in RAAN, argument of perigee,
    or true anomaly points at a quadrant error in the state-to-elements reduction -- the failure
    this file exists for, since it leaves the state vector itself untouched.
    """
    when = _epoch() + timedelta(minutes=state["offset_minutes"])
    computed = elements.classical_elements(FIXTURE_TLE, when)

    axis_error = abs(computed["semi_major_axis"].value - state["semi_major_axis_m"])
    assert axis_error <= SEMI_MAJOR_AXIS_TOLERANCE_M, (
        f"semi-major axis disagreement at +{state['offset_minutes']} min: {axis_error:.6f} m"
    )

    assert computed["eccentricity"].value == pytest.approx(
        state["eccentricity"], abs=ECCENTRICITY_TOLERANCE
    )

    for name, reference_key in (
        ("inclination", "inclination_deg"),
        ("raan", "raan_deg"),
        ("argument_of_perigee", "argument_of_perigee_deg"),
        ("true_anomaly", "true_anomaly_deg"),
    ):
        difference = _angle_difference_deg(computed[name].value, state[reference_key])
        assert difference <= ANGLE_TOLERANCE_DEG, (
            f"{name} disagreement at +{state['offset_minutes']} min: {difference:.3e} deg "
            f"(skyfield={computed[name].value}, orekit={state[reference_key]})"
        )


@pytest.mark.parametrize("state", _states(), ids=lambda s: f"+{s['offset_minutes']}min")
def test_derived_orbit_properties_match_orekit(state: dict[str, Any]) -> None:
    """Period, mean motion, and both apsis altitudes agree with Orekit.

    The altitudes are the point of this case. Orekit reduces the apsis position onto its own
    WGS84 ellipsoid in ITRF, entirely independently, so agreement to metres is real evidence that
    the datum is right rather than merely self-consistent.
    """
    when = _epoch() + timedelta(minutes=state["offset_minutes"])
    derived = elements.derived_orbit_properties(FIXTURE_TLE, when)

    assert derived["orbital_period"].value == pytest.approx(
        state["orbital_period_s"], abs=PERIOD_TOLERANCE_S
    )
    assert derived["mean_motion"].value == pytest.approx(
        state["mean_motion_rev_per_day"], abs=MEAN_MOTION_TOLERANCE_REV_PER_DAY
    )

    for name, reference_key in (
        ("perigee_altitude", "perigee_altitude_m"),
        ("apogee_altitude", "apogee_altitude_m"),
    ):
        difference = abs(derived[name].value - state[reference_key])
        assert difference <= ALTITUDE_TOLERANCE_M, (
            f"{name} disagreement at +{state['offset_minutes']} min: {difference:.4f} m "
            f"(skyfield={derived[name].value:.4f}, orekit={state[reference_key]:.4f})"
        )


def test_reference_uses_the_same_element_set() -> None:
    """The reference describes the satellite these tests propagate.

    Guards the suite itself. If the generator's frozen TLE drifted away from this fixture, every
    differential test above would compare two different orbits and fail for a reason that looks
    like a physics bug.
    """
    reference = _require_reference()

    assert reference["tle"]["line1"] == FIXTURE_TLE.line1
    assert reference["tle"]["line2"] == FIXTURE_TLE.line2


def test_reference_uses_the_same_gravitational_parameter() -> None:
    """Both sides reduced the state to elements under the same mu.

    Elements are a function of mu, so comparing values derived under WGS72 against values derived
    under WGS84 would be comparing two different physical models -- a several-metre offset in the
    semi-major axis that no tolerance should be widened to absorb.
    """
    reference = _require_reference()
    reference_mu = reference["_provenance"]["mu_m3_s2"]

    relative = abs(reference_mu - elements.EARTH_MU_M3_S2) / elements.EARTH_MU_M3_S2
    assert relative < 1e-9, (
        f"reference mu {reference_mu:.9e} differs from {elements.EARTH_MU_M3_S2:.9e} m^3/s^2"
    )


def test_reference_altitudes_are_not_radii() -> None:
    """The reference itself uses the ellipsoid datum, not distance from Earth's centre.

    The vacuity guard .agent/test.md requires of every differential-test family. If the generator
    had recorded apsis radii under the altitude keys, the tier-3 tests above would still pass --
    both sides would simply be wrong together -- and the exact bug this file was written to catch
    would sail through. Altitudes must sit roughly one Earth radius below the radii.
    """
    states = _states()
    if not states:
        pytest.skip("elements.json not generated")

    for state in states:
        for altitude_key, radius_key in (
            ("perigee_altitude_m", "perigee_radius_m"),
            ("apogee_altitude_m", "apogee_radius_m"),
        ):
            gap_m = state[radius_key] - state[altitude_key]
            assert wgs84.polar_radius.m <= gap_m <= wgs84.radius.m, (
                f"{altitude_key} at +{state['offset_minutes']} min sits {gap_m:.0f} m below its "
                "radius; an ellipsoid height must sit between one polar and one equatorial "
                "radius below it"
            )
            assert 200e3 < state[altitude_key] < 600e3


def test_reference_states_are_actually_distinct() -> None:
    """The reference propagated to five different instants, not five copies of one.

    The second half of the vacuity guard. A generator that shifted the epoch incorrectly would
    emit identical states, and every parametrized case above would pass while testing a single
    instant five times.
    """
    states = _states()
    if not states:
        pytest.skip("elements.json not generated")

    anomalies = {round(state["true_anomaly_deg"], 3) for state in states}
    assert len(anomalies) == len(states), "reference states repeat the same true anomaly"

    first = np.array(states[0]["position_teme_m"])
    for state in states[1:]:
        separation_km = float(np.linalg.norm(np.array(state["position_teme_m"]) - first)) / 1e3
        assert separation_km > 100.0, (
            f"+{state['offset_minutes']} min sits only {separation_km:.1f} km from epoch"
        )


def test_reference_records_its_provenance() -> None:
    """The element reference states where it came from, in which frame, and on which datum."""
    provenance = _require_reference()["_provenance"]

    assert provenance["source"] == "orekit"
    assert provenance["orekit_jpype_version"] != "unknown"
    assert provenance["frame"] == "TEME"
    assert provenance["element_type"] == "osculating"
    assert "WGS84 ellipsoid" in provenance["altitude_datum"]

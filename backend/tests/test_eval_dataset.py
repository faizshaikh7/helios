"""Guards on the evaluation dataset.

The dataset in `eval/questions.json` is the ground truth behind the project's headline accuracy
chart. If it is wrong, every number in that chart is wrong in a way no amount of downstream care
can detect -- so the dataset itself gets tested.

Two distinct risks are covered:

1. **Physically wrong ground truth.** Caught by checking known orbital regimes against values
   independent of how the dataset was generated: a geostationary period is a sidereal day, GPS
   is 11h58m, low Earth orbit is ~7.7 km/s. If the generator used the wrong units, datum, or
   frame, these fail.
2. **A dataset that cannot discriminate.** If the questions are ones a language model can answer
   from memory, a grounded system and a bare model score alike and the chart shows nothing. The
   set must stay dominated by quantities that are unmemorizable by construction.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

# eval/ sits at the repo root: it is research data shared by the tools, not part of the
# deployable backend service.
DATASET_PATH = Path(__file__).resolve().parents[2] / "eval" / "questions.json"

# Independent expectations, from published orbital facts rather than from this project's code.
EXPECTED_PERIOD_MINUTES: dict[str, tuple[float, float]] = {
    # Geostationary period is one sidereal day, 1436.07 min, by definition.
    "geostationary": (1430.0, 1442.0),
    # GPS satellites sit in a half-sidereal-day orbit: 11 h 58 m.
    "medium Earth orbit": (700.0, 730.0),
    # Low Earth orbit is roughly 90-100 minutes.
    "LEO": (85.0, 100.0),
    "LEO low-inclination": (85.0, 100.0),
    "sun-synchronous": (95.0, 110.0),
}


def _dataset() -> dict[str, Any]:
    """Load the evaluation dataset, skipping if it has not been generated."""
    if not DATASET_PATH.exists():
        pytest.skip(
            "eval/questions.json not generated - run "
            "`uv run python tools/eval/generate_questions.py`"
        )
    return json.loads(DATASET_PATH.read_text(encoding="utf-8"))


def _by_category(category: str) -> list[dict[str, Any]]:
    """Return every question in a category."""
    return [q for q in _dataset()["questions"] if q["category"] == category]


def test_orbital_periods_match_known_regimes() -> None:
    """Periods agree with published values for each orbital regime.

    This is the strongest available check on the generator: a geostationary period is a sidereal
    day by definition, so a value outside that window means the propagation, units, or element
    parsing is wrong -- independently of anything in this codebase.
    """
    for question in _by_category("orbital_period"):
        regime = question["context"]["regime"]
        if regime not in EXPECTED_PERIOD_MINUTES:
            continue

        low, high = EXPECTED_PERIOD_MINUTES[regime]
        period = question["answer"]["value"]

        assert low <= period <= high, (
            f"{regime} satellite #{question['context']['norad_id']} has period {period:.2f} min, "
            f"outside the expected {low}-{high} min for that regime"
        )


def test_speeds_are_physically_plausible() -> None:
    """Orbital speeds fall in a physically possible range, and scale correctly with altitude.

    Anything above ~11.2 km/s is escape velocity; anything below ~1 km/s is not in a bound orbit
    around Earth at these radii. A km/m unit error would put every value out by 1000.
    """
    for question in _by_category("speed"):
        speed = question["answer"]["value"]
        assert 1.0 < speed < 11.2, (
            f"implausible orbital speed {speed} km/s for #{question['context']['norad_id']}"
        )


def test_low_earth_orbit_is_faster_than_geostationary() -> None:
    """Speed decreases with orbital radius, as gravity requires.

    A generator that mixed up satellites, epochs, or frames could still produce individually
    plausible speeds. This relationship has to hold across the whole set.
    """
    speeds: dict[str, list[float]] = {}
    for question in _by_category("speed"):
        speeds.setdefault(question["context"]["regime"], []).append(question["answer"]["value"])

    if "LEO" not in speeds or "geostationary" not in speeds:
        pytest.skip("dataset lacks both regimes")

    assert min(speeds["LEO"]) > max(speeds["geostationary"])


def test_altitudes_are_consistent_with_regime() -> None:
    """Altitudes match their regime, and are above the ellipsoid rather than from Earth's centre.

    The classic silent error here is reporting radius instead of altitude, which inflates every
    low-Earth-orbit value by ~6378 km. A LEO altitude above 2000 km would catch that immediately.
    """
    for question in _by_category("altitude"):
        regime = question["context"]["regime"]
        altitude = question["answer"]["value"]

        if regime.startswith("LEO"):
            assert 150 < altitude < 2000, (
                f"LEO altitude {altitude} km is outside the LEO band - possibly a radius "
                "reported as an altitude"
            )
        elif regime == "geostationary":
            assert 35000 < altitude < 36500, f"geostationary altitude {altitude} km is wrong"


def test_dataset_is_dominated_by_unmemorizable_questions() -> None:
    """Most questions cannot be answered from a model's memory.

    This is what gives the accuracy chart its meaning. A set of publicly-known facts would let a
    bare model score well without computing anything, and the comparison would show no
    difference between grounding and guessing.
    """
    summary = _dataset()["summary"]

    assert summary["unmemorizable"] > summary["memorizable_control"] * 5, (
        "the memorizable control has grown large enough to dilute the measurement"
    )
    assert summary["memorizable_control"] > 0, (
        "the control set is needed to show a bare model performing well where recall suffices"
    )


def test_every_question_has_a_tolerance_and_a_unit() -> None:
    """No question can be scored ambiguously.

    An answer without a unit cannot be compared, and one without a tolerance would demand exact
    float equality, which no honest method achieves.
    """
    for question in _dataset()["questions"]:
        answer = question["answer"]
        assert answer["unit"], f"{question['id']} has no unit"
        assert "tolerance_abs" in answer, f"{question['id']} has no tolerance"
        assert answer["tolerance_abs"] >= 0


def test_ground_truth_is_not_self_generated() -> None:
    """Ground truth comes from the independent implementation, never from this project's code.

    If this ever reads 'skyfield' or 'helios', the evaluation has become circular and the chart
    measures nothing but self-consistency.
    """
    dataset = _dataset()

    assert dataset["_provenance"]["source"] == "orekit"
    for question in dataset["questions"]:
        assert question["ground_truth_source"] == "orekit"


# --------------------------------------------------------------------------------------------
# Evaluation fixture pinning
# --------------------------------------------------------------------------------------------


def test_fixtures_can_pin_the_catalog() -> None:
    """Frozen element sets load into the catalog and are served instead of live data.

    Ground truth was computed from these exact element sets. A system answering with fresher ones
    is graded against a target it was never given, and loses accuracy it did not actually lose --
    an error that grows silently as element sets age. That would make the accuracy chart quietly
    wrong rather than visibly broken, which is the worse failure.
    """
    from science import catalog

    fixtures_path = DATASET_PATH.parent / "tle_fixtures.json"
    if not fixtures_path.exists():
        pytest.skip("tle_fixtures.json not generated")

    catalog.clear_cache()
    try:
        count = catalog.load_fixtures(str(fixtures_path))
        assert count > 0

        # Served from the pin, with no network call: a live fetch would set a different source.
        tle = catalog.get_tle(25544)
        assert tle.source == "eval-fixture"

        expected = json.loads(fixtures_path.read_text(encoding="utf-8"))["satellites"]
        pinned = next(item for item in expected if item["norad_id"] == 25544)
        assert tle.line1 == pinned["line1"]
        assert tle.line2 == pinned["line2"]
    finally:
        catalog.clear_cache()


def test_pinned_fixtures_do_not_expire_mid_run() -> None:
    """Pinned element sets outlive an evaluation run.

    The normal cache lives an hour. A long run crossing that boundary would silently switch to
    live data partway through, so half the questions would be graded against different inputs
    from the other half -- and nothing would report that it happened.
    """
    from science import catalog

    fixtures_path = DATASET_PATH.parent / "tle_fixtures.json"
    if not fixtures_path.exists():
        pytest.skip("tle_fixtures.json not generated")

    catalog.clear_cache()
    try:
        catalog.load_fixtures(str(fixtures_path))
        expires_at, _ = catalog._cache[25544]
        remaining_days = (expires_at - time.time()) / 86400
        assert remaining_days > 1, (
            f"pinned fixtures expire in {remaining_days:.2f} days; a long run could outlive them"
        )
    finally:
        catalog.clear_cache()


# Independent expectations for how far each body can be from Earth, in AU. Derived from published
# semi-major axes and eccentricities, not from this project's ephemeris: the widest separation is
# the sum of the two aphelia and the closest is the difference of the extremes. A generator using
# the wrong origin (heliocentric rather than geocentric) or the wrong unit fails these.
EXPECTED_EARTH_DISTANCE_AU: dict[str, tuple[float, float]] = {
    "mercury": (0.50, 1.50),
    "venus": (0.25, 1.76),
    "mars": (0.36, 2.70),
    "jupiter": (3.92, 6.48),
    "saturn": (8.00, 11.15),
    "uranus": (17.25, 21.15),
    "neptune": (28.70, 31.45),
    # 356,500 to 406,700 km.
    "moon": (0.00238, 0.00272),
    # Earth's own perihelion and aphelion.
    "sun": (0.980, 1.020),
}


def test_planetary_distances_lie_inside_published_ranges() -> None:
    """Every planetary answer falls within the range that body's orbit permits.

    This is the check that catches an origin or unit error. A heliocentric distance would put
    Mars at 1.4-1.7 AU rather than 0.4-2.7, and metres or kilometres would miss by orders of
    magnitude -- both of which look entirely plausible in isolation.
    """
    questions = _by_category("earth_distance")
    assert questions, "no planetary questions in the dataset"

    for question in questions:
        body = question["context"]["body"]
        low, high = EXPECTED_EARTH_DISTANCE_AU[body]
        value = question["answer"]["value"]

        assert question["answer"]["unit"] == "AU", f"{body} is not in AU"
        assert low <= value <= high, (
            f"{body} at {question['context']['epoch_utc']} is {value} AU, outside the "
            f"{low}-{high} AU its orbit allows"
        )


def test_planetary_tolerances_scale_with_distance() -> None:
    """Tolerance is relative, not one fixed figure across three orders of magnitude.

    A single absolute tolerance cannot serve this set: tight enough to be meaningful at Neptune
    is unreachable at the Moon, and loose enough for Neptune would make the Moon question free.
    If this ever collapses to a constant, the far bodies stop being tested and the near ones
    stop being answerable.
    """
    questions = _by_category("earth_distance")

    for question in questions:
        value = question["answer"]["value"]
        tolerance = question["answer"]["tolerance_abs"]

        assert tolerance > 0, "a zero tolerance can never be met"
        # Either it tracks the distance, or it is the documented floor for the Moon.
        assert tolerance == pytest.approx(value * 1.0e-4) or tolerance == pytest.approx(1.0e-5), (
            f"tolerance {tolerance} for {question['context']['body']} is neither 1e-4 of "
            f"{value} AU nor the 1e-5 AU floor"
        )


def test_planetary_questions_are_not_answerable_from_recall() -> None:
    """No planetary question is marked as a memorizable control.

    At 1e-4 relative, recalling a round number fails -- the Sun's distance varies by 0.017 AU
    over a year against a tolerance of about 1e-4, and the Moon's by some 50,000 km against a
    floor of roughly 1,500 km. Marking any of these memorizable would misdescribe the dataset.
    """
    for question in _by_category("earth_distance"):
        assert question["memorizable"] is False

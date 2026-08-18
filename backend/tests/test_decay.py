"""Tests for orbital decay lifetime.

Two layers, per .agent/test.md:

* **Tier 1** — properties that must hold whatever the atmosphere says: monotonicity in altitude,
  correct scaling with ballistic coefficient, ordering of the solar scenarios, and physical
  plausibility against published guidance.
* **Tier 3** — the simplified circular-orbit model graded against full numerical propagation in
  Orekit, with a real force model and a real atmosphere sampled at the satellite's position.

The differential tolerance is a *measured* figure. The simplified model runs 1.14x-1.39x long
across the reference cases, consistently in one direction, so the bound is set at 1.7x: loose
enough to survive regenerating the reference, tight enough that the failures that matter still
trip it. A units error shows up as ~1000x, and forgetting to set spacecraft mass -- which
actually happened while building the reference -- as ~300x.
"""

from __future__ import annotations

import json
from itertools import pairwise
from pathlib import Path

import pytest

from science.decay import (
    LIFETIME_CAP_YEARS,
    SCENARIO_LABELS,
    density,
    lifetime,
    lifetime_range,
)
from science.provenance import Tier

REFERENCE_PATH = Path(__file__).parent / "reference" / "decay.json"

# Measured worst departure is 1.39x; see module docstring.
MAX_RATIO_VS_NUMERICAL = 1.7

# A 3U cubesat: Cd 2.2, 0.03 m^2, 3.3 kg.
CUBESAT_BALLISTIC_TERM = 0.02


# --------------------------------------------------------------------------------------------
# Tier 1: properties
# --------------------------------------------------------------------------------------------


def test_density_falls_monotonically_with_altitude() -> None:
    """Density decreases with height, in every solar scenario.

    A table that rose anywhere would mean a corrupted or misordered grid, and every lifetime
    computed from it would be wrong in a way the integration cannot detect.
    """
    for scenario in SCENARIO_LABELS:
        values = [density(alt, scenario) for alt in range(150, 1000, 25)]
        for lower, higher in pairwise(values):
            assert lower > higher, f"{scenario}: density rises with altitude"


def test_solar_activity_raises_density_and_shortens_life() -> None:
    """Stronger solar activity means denser upper atmosphere and faster decay.

    This is the physical mechanism the whole uncertainty story rests on: solar heating expands
    the thermosphere. If the ordering ever inverted, the reported range would be backwards.
    """
    assert density(500, "STRONG") > density(500, "AVERAGE") > density(500, "WEAK")

    strong = lifetime(500, CUBESAT_BALLISTIC_TERM, "STRONG").years
    average = lifetime(500, CUBESAT_BALLISTIC_TERM, "AVERAGE").years
    weak = lifetime(500, CUBESAT_BALLISTIC_TERM, "WEAK").years

    assert strong < average < weak


def test_solar_sensitivity_grows_with_altitude() -> None:
    """The solar-cycle spread widens with height.

    Solar heating expands the upper atmosphere, so the scenarios diverge as altitude rises and
    nearly converge low down. A table with a constant ratio would mean a scale factor had been
    applied rather than a real model sampled.
    """
    low = density(150, "STRONG") / density(150, "WEAK")
    high = density(800, "STRONG") / density(800, "WEAK")

    assert high > low * 2, f"spread barely changes with altitude: {low:.2f}x to {high:.2f}x"


def test_higher_orbits_live_longer() -> None:
    """Lifetime increases with altitude."""
    lifetimes = [
        lifetime(alt, CUBESAT_BALLISTIC_TERM, "AVERAGE").years for alt in (300, 400, 500, 600)
    ]
    for shorter, longer in pairwise(lifetimes):
        assert longer > shorter


def test_lifetime_scales_inversely_with_ballistic_term() -> None:
    """Doubling drag area per unit mass roughly halves the lifetime.

    Decay rate is linear in ``Cd*A/m``, so lifetime should scale close to inversely. It is not
    exact because the atmosphere is not a single exponential, hence the loose bracket.
    """
    single = lifetime(500, 0.01, "AVERAGE").years
    double = lifetime(500, 0.02, "AVERAGE").years

    ratio = single / double
    assert 1.7 < ratio < 2.3, f"expected near-inverse scaling, got {ratio:.2f}x"


def test_twenty_five_year_threshold_lands_where_guidance_says() -> None:
    """A typical cubesat crosses the 25-year disposal guideline in the 500-700 km band.

    This is the one number the tool exists to inform, and the band is widely published. Landing
    far outside it would mean the model is wrong in a way no internal consistency check catches.
    """
    crossing = None
    for altitude in range(400, 900, 10):
        if lifetime(altitude, CUBESAT_BALLISTIC_TERM, "AVERAGE").years > 25:
            crossing = altitude
            break

    assert crossing is not None, "never crossed 25 years below 900 km"
    assert 500 <= crossing <= 700, f"25-year crossing at {crossing} km is outside the known band"


def test_very_high_orbits_report_a_floor_rather_than_a_number() -> None:
    """Above the table, the answer is 'longer than we will say', not an extrapolation.

    Extrapolating an atmosphere model past its data would produce a confident figure with no
    support behind it -- the exact failure this project exists to avoid.
    """
    estimate = lifetime(1200, CUBESAT_BALLISTIC_TERM, "AVERAGE")

    assert estimate.capped is True
    assert estimate.years == LIFETIME_CAP_YEARS


def test_range_is_predicted_and_states_the_spread() -> None:
    """Every lifetime is tiered predicted and carries the scenario range.

    Lifetime propagates a model forward under a forecast nobody can make. Marking it derived
    would claim far more than the physics supports.
    """
    result = lifetime_range(500, CUBESAT_BALLISTIC_TERM)

    assert set(result) == {"shortest", "nominal", "longest"}
    assert result["shortest"].value < result["nominal"].value < result["longest"].value

    for value in result.values():
        assert value.tier is Tier.PREDICTED
        assert value.unit == "years"

        uncertainty = value.receipt.uncertainty
        assert uncertainty is not None
        assert uncertainty["spread_factor"] > 1.0
        assert "solar activity" in uncertainty["basis"]

        assert "circular orbit" in (value.receipt.notes or "")


def test_rejects_a_nonsensical_ballistic_term() -> None:
    """A zero or negative ballistic term is refused rather than silently producing infinity."""
    for bad in (0.0, -0.01):
        with pytest.raises(ValueError):
            lifetime(500, bad, "AVERAGE")


# --------------------------------------------------------------------------------------------
# Tier 3: against full numerical propagation
# --------------------------------------------------------------------------------------------


def _cases() -> list[dict[str, float]]:
    """Reference cases, for parametrizing."""
    if not REFERENCE_PATH.exists():
        return []
    return json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", _cases(), ids=lambda c: f"{c['start_altitude_km']:.0f}km")
def test_matches_numerical_propagation_within_measured_bound(case: dict[str, float]) -> None:
    """The simplified model tracks full numerical propagation within the measured bound.

    Orekit propagates with an 8x8 gravity field and NRLMSISE-00 evaluated at the satellite's
    actual position, with no circular-orbit assumption. Agreement to better than 1.4x means the
    simplification is sound enough for the question being asked, and - importantly - that the
    model error is *smaller* than the solar-cycle uncertainty it reports.
    """
    if not case["decayed"]:
        pytest.skip("reference case did not decay within the simulated window")

    numerical_days = case["lifetime_days"]
    simplified_days = (
        lifetime(case["start_altitude_km"], case["ballistic_term_m2_per_kg"], "AVERAGE").years
        * 365.25
    )

    ratio = max(simplified_days / numerical_days, numerical_days / simplified_days)

    assert ratio <= MAX_RATIO_VS_NUMERICAL, (
        f"{case['start_altitude_km']:.0f} km: simplified {simplified_days:.1f} d vs numerical "
        f"{numerical_days:.1f} d = {ratio:.2f}x, over the {MAX_RATIO_VS_NUMERICAL}x bound"
    )


def test_model_error_is_smaller_than_the_uncertainty_it_reports() -> None:
    """The solar-cycle spread exceeds the model's own error.

    This is what makes the reported range meaningful rather than decorative: if the simplified
    model were less accurate than the spread between scenarios, the range would be measuring the
    model's shortcomings rather than the physics it claims to describe.
    """
    result = lifetime_range(500, CUBESAT_BALLISTIC_TERM)
    spread = result["longest"].value / result["shortest"].value

    assert spread > MAX_RATIO_VS_NUMERICAL, (
        f"solar spread {spread:.2f}x does not exceed the model bound "
        f"{MAX_RATIO_VS_NUMERICAL}x; the range would be dominated by model error"
    )


def test_reference_records_its_method() -> None:
    """The reference states how it was produced."""
    if not REFERENCE_PATH.exists():
        pytest.skip("decay.json not generated")

    provenance = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))["_provenance"]

    assert provenance["source"] == "orekit"
    assert "NRLMSISE-00" in provenance["method"]
    assert provenance["orekit_jpype_version"] != "unknown"

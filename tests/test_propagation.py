"""Differential tests: SGP4 propagation against Orekit.

Tier-3 per .agent/test.md.

**What this grades.** `science.orbit` propagates via skyfield, which delegates the propagation
itself to the `sgp4` package and then layers a frame conversion on top. This module compares the
propagation in TEME -- SGP4's native frame -- so a failure is unambiguously a propagation
problem. The frame conversion skyfield adds is graded separately by `test_frames.py`. Comparing
after conversion would blur the two.

**What this does not prove.** Both implementations descend from the same published SGP4
algorithm, so agreement is not evidence the algorithm models reality. It is evidence about
implementation choices -- epoch interpretation, units, TLE field parsing, frame convention --
which is where the two libraries were written independently, and which is where the realistic
bugs live.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from sgp4.api import Satrec, jday

REFERENCE_PATH = Path(__file__).parent / "reference" / "tle_propagation.json"

# Measured agreement between the sgp4 package and Orekit, across all reference offsets:
#     position   <= 0.06 mm      velocity  <= 0.0005 mm/s
# even after a full day of propagation. The two implement the same algorithm, so only
# floating-point ordering differs. One metre leaves four orders of magnitude of headroom while
# remaining far below any real defect: a mis-parsed TLE field, a wrong epoch, or a km/m unit
# error all produce errors of kilometres or more.
POSITION_TOLERANCE_M = 1.0
VELOCITY_TOLERANCE_M_S = 0.01


def _reference() -> dict[str, Any]:
    """Load the committed Orekit propagation reference."""
    return json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))


def _states() -> list[dict[str, Any]]:
    """Return reference states, for parametrizing tests."""
    if not REFERENCE_PATH.exists():
        return []
    return _reference()["states"]


def _propagate_teme(line1: str, line2: str, epoch_iso: str) -> tuple[np.ndarray, np.ndarray]:
    """Propagate a TLE to an instant with the sgp4 package, in TEME.

    Args:
        line1: TLE line 1.
        line2: TLE line 2.
        epoch_iso: Target instant, ISO-8601 UTC as emitted by Orekit.

    Returns:
        Position in metres and velocity in metres per second, both TEME.

    Raises:
        AssertionError: If the propagator reports an error code.
    """
    moment = datetime.fromisoformat(epoch_iso)

    satellite = Satrec.twoline2rv(line1, line2)
    julian_day, fraction = jday(
        moment.year,
        moment.month,
        moment.day,
        moment.hour,
        moment.minute,
        moment.second + moment.microsecond / 1e6,
    )

    code, position_km, velocity_km_s = satellite.sgp4(julian_day, fraction)
    assert code == 0, f"sgp4 returned error code {code} for {epoch_iso}"

    return np.array(position_km) * 1e3, np.array(velocity_km_s) * 1e3


@pytest.mark.parametrize("state", _states(), ids=lambda s: f"+{s['offset_minutes']}min")
def test_position_matches_orekit(state: dict[str, Any]) -> None:
    """sgp4 and Orekit agree on TEME position at each offset from epoch.

    A disagreement of kilometres points at a mis-parsed TLE field or a wrong epoch; a
    disagreement scaled by exactly 1000 points at a km/m unit error.
    """
    tle = _reference()["tle"]
    measured, _ = _propagate_teme(tle["line1"], tle["line2"], state["epoch_utc"])
    expected = np.array(state["position_teme_m"])

    separation_m = float(np.linalg.norm(measured - expected))

    assert separation_m <= POSITION_TOLERANCE_M, (
        f"TEME position disagreement at +{state['offset_minutes']} min: "
        f"{separation_m:.6f} m (tolerance {POSITION_TOLERANCE_M} m)\n"
        f"  sgp4:   {measured}\n  orekit: {expected}"
    )


@pytest.mark.parametrize("state", _states(), ids=lambda s: f"+{s['offset_minutes']}min")
def test_velocity_matches_orekit(state: dict[str, Any]) -> None:
    """sgp4 and Orekit agree on TEME velocity at each offset from epoch.

    Velocity is checked separately because a position-only check would miss a sign or component
    ordering error that happens to leave the position magnitude plausible.
    """
    tle = _reference()["tle"]
    _, measured = _propagate_teme(tle["line1"], tle["line2"], state["epoch_utc"])
    expected = np.array(state["velocity_teme_m_s"])

    difference = float(np.linalg.norm(measured - expected))

    assert difference <= VELOCITY_TOLERANCE_M_S, (
        f"TEME velocity disagreement at +{state['offset_minutes']} min: "
        f"{difference:.6f} m/s (tolerance {VELOCITY_TOLERANCE_M_S} m/s)"
    )


def test_orbit_radius_is_physically_plausible() -> None:
    """Reference positions sit at a plausible low-Earth-orbit radius.

    Guards against a reference generated in the wrong units or frame. If the generator emitted
    kilometres where metres were expected, every differential test above would still pass -- both
    sides would be wrong together -- while the numbers were nonsense.
    """
    states = _states()
    if not states:
        pytest.skip("tle_propagation.json not generated")

    for state in states:
        radius_km = float(np.linalg.norm(state["position_teme_m"])) / 1e3
        assert 6500 < radius_km < 7200, (
            f"implausible orbit radius {radius_km:.1f} km at +{state['offset_minutes']} min; "
            "the ISS sits near 6790 km from Earth's centre"
        )


def test_reference_records_its_provenance() -> None:
    """The propagation reference states where it came from and in which frame."""
    if not REFERENCE_PATH.exists():
        pytest.skip("tle_propagation.json not generated")

    provenance = _reference()["_provenance"]

    assert provenance["source"] == "orekit"
    assert provenance["frame"] == "TEME"
    assert provenance["orekit_jpype_version"] != "unknown"

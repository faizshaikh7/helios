"""Conjunction-screening differential, property, and API-contract tests.

The numerical headline is graded against committed Orekit results.  RTN properties and request
validation are regression checks only; they do not contribute to an accuracy claim.
"""

from __future__ import annotations

import json
import math
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.main import app
from science import catalog, conjunction

REFERENCE_PATH = Path(__file__).parent / "reference" / "conjunction.json"

# Measured production-versus-Orekit differences on the committed cases are below 0.0002 seconds,
# 0.001 metres, and 0.001 mm/s. These limits leave broad floating-point headroom while remaining
# tight enough to catch the realistic failures: a missed local minimum, wrong frame, or unit error.
TCA_TOLERANCE_S = 0.05
MISS_DISTANCE_TOLERANCE_KM = 0.01
RELATIVE_SPEED_TOLERANCE_KM_S = 0.00001


def _reference() -> dict[str, Any]:
    """Load the committed Orekit conjunction reference."""
    return json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))


def _cases() -> list[dict[str, Any]]:
    """Return reference cases for pytest parametrization."""
    return _reference()["cases"] if REFERENCE_PATH.exists() else []


def _tle(norad_id: int) -> catalog.TLE:
    """Build one fixed test TLE from the reference document.

    Args:
        norad_id: NORAD catalog number present in the reference file.

    Returns:
        Reproducible element set with no network dependency.
    """
    item = _reference()["objects"][str(norad_id)]
    return catalog.TLE(
        name=item["name"],
        line1=item["line1"],
        line2=item["line2"],
        norad_id=norad_id,
        source="test-fixture",
        fetched_at_unix=time.time(),
    )


@pytest.fixture(autouse=True)
def _offline_catalog():
    """Install every reference object so tests cannot call the live catalog."""
    catalog.clear_cache()
    for norad_id in _reference()["objects"]:
        catalog.seed_cache(_tle(int(norad_id)))
    yield
    catalog.clear_cache()


def _screen(case: dict[str, Any], *, step_seconds: int | None = None) -> conjunction.Encounter:
    """Run the production screen with the fixed inputs for one Orekit case.

    Args:
        case: One entry from the reference file.
        step_seconds: Optional coarse-step override.

    Returns:
        Production closest-approach result.
    """
    reference = _reference()
    return conjunction.screen(
        _tle(case["primary_norad_id"]),
        _tle(case["secondary_norad_id"]),
        datetime.fromisoformat(reference["start_utc"]),
        duration_hours=reference["duration_hours"],
        step_seconds=step_seconds or int(reference["step_seconds"]),
    )


@pytest.mark.parametrize(
    "case",
    _cases(),
    ids=lambda item: f"{item['primary_norad_id']}-{item['secondary_norad_id']}",
)
def test_closest_approach_matches_orekit(case: dict[str, Any]) -> None:
    """TCA, miss distance, and relative speed agree with independent Orekit propagation."""
    result = _screen(case)
    start = datetime.fromisoformat(_reference()["start_utc"])
    measured_offset_s = (result.tca_utc - start).total_seconds()

    assert abs(measured_offset_s - case["offset_seconds"]) <= TCA_TOLERANCE_S
    assert (
        abs(result.miss_distance_km - case["miss_distance_m"] / 1000.0)
        <= MISS_DISTANCE_TOLERANCE_KM
    )
    assert (
        abs(result.relative_speed_km_s - case["relative_speed_m_s"] / 1000.0)
        <= RELATIVE_SPEED_TOLERANCE_KM_S
    )


@pytest.mark.parametrize("case", _cases())
def test_rtn_components_preserve_vector_magnitudes(case: dict[str, Any]) -> None:
    """The orthonormal RTN projection preserves position and velocity magnitudes."""
    result = _screen(case)
    position_magnitude = math.sqrt(sum(value * value for value in result.relative_position_rtn_km))
    velocity_magnitude = math.sqrt(
        sum(value * value for value in result.relative_velocity_rtn_km_s)
    )

    assert position_magnitude == pytest.approx(result.miss_distance_km, abs=1e-9)
    assert velocity_magnitude == pytest.approx(result.relative_speed_km_s, abs=1e-12)


def test_refined_answer_is_stable_across_coarse_steps() -> None:
    """Changing the bracketing resolution does not move the refined closest approach."""
    case = _cases()[0]
    fine = _screen(case, step_seconds=30)
    coarse = _screen(case, step_seconds=120)

    assert abs((fine.tca_utc - coarse.tca_utc).total_seconds()) < TCA_TOLERANCE_S
    assert abs(fine.miss_distance_km - coarse.miss_distance_km) < MISS_DISTANCE_TOLERANCE_KM


def test_api_exposes_units_frames_and_no_fabricated_probability() -> None:
    """The public response distinguishes geometric screening from collision-risk assessment."""
    case = _cases()[0]
    response = TestClient(app).post(
        "/api/conjunction",
        json={
            "primary_norad_id": case["primary_norad_id"],
            "secondary_norad_id": case["secondary_norad_id"],
            "from_utc": _reference()["start_utc"],
            "duration_hours": 24,
            "step_seconds": 60,
            "screening_threshold_km": 1000,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["miss_distance"]["unit"] == "km"
    assert body["relative_speed"]["unit"] == "km/s"
    assert body["receipt"]["frame"].startswith("TEME")
    assert body["receipt"]["time_scale"] == "UTC"
    assert body["risk_assessment"]["collision_probability"] is None
    assert "covariance" in body["risk_assessment"]["reason"]
    assert body["screening"]["inside_threshold"] is True


def test_same_object_and_excessive_work_are_rejected() -> None:
    """Invalid and worker-exhausting screens return typed 422 errors."""
    client = TestClient(app)
    same = client.post(
        "/api/conjunction",
        json={"primary_norad_id": 25544, "secondary_norad_id": 25544},
    )
    oversized = client.post(
        "/api/conjunction",
        json={
            "primary_norad_id": 25544,
            "secondary_norad_id": 33591,
            "duration_hours": 72,
            "step_seconds": 10,
        },
    )

    assert same.status_code == 422
    assert same.json()["error"]["code"] == "same_object"
    assert oversized.status_code == 422
    assert oversized.json()["error"]["code"] == "screen_too_large"


def test_reference_records_independent_provenance() -> None:
    """The numerical target identifies Orekit, TEME, UTC, and a known library version."""
    provenance = _reference()["_provenance"]

    assert provenance["source"] == "orekit"
    assert provenance["frame"] == "TEME"
    assert provenance["time_scale"] == "UTC"
    assert provenance["orekit_jpype_version"] != "unknown"

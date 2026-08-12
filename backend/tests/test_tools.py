"""Tests for the tool layer: provenance rules, pass geometry, and the API contract.

These are tier-1 checks -- properties that must hold regardless of what any external service
returns. They run entirely against a **fixed element set installed via the catalog's mock twin**,
so they never touch the network. That keeps CI deterministic and means a Celestrak outage
cannot turn into a red build.

Numerical agreement with an independent implementation is covered separately by
`test_propagation.py` and `test_frames.py`.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest
from fastapi.testclient import TestClient

from api.main import app
from science import catalog, orbit
from science.provenance import Receipt, Tier, Value, weakest

# ISS (ZARYA), frozen so results are reproducible. Same element set as the propagation reference.
FIXTURE_TLE = catalog.TLE(
    name="ISS (ZARYA)",
    line1="1 25544U 98067A   24001.50000000  .00016717  00000-0  30150-3 0  9004",
    line2="2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.49815310 10000",
    norad_id=25544,
    source="test-fixture",
    fetched_at_unix=time.time(),
)

# Somewhere with a real horizon; latitude below the ISS inclination so passes actually occur.
BANGALORE = orbit.GroundStation(
    name="Bangalore", latitude_deg=12.9716, longitude_deg=77.5946, elevation_m=920.0
)


@pytest.fixture(autouse=True)
def _offline_catalog():
    """Install the fixture element set and guarantee no test reaches the network."""
    catalog.clear_cache()
    catalog.seed_cache(FIXTURE_TLE)
    yield
    catalog.clear_cache()


client = TestClient(app)


# --------------------------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------------------------


def test_weakest_tier_wins() -> None:
    """Monotonic inheritance: the weakest input tier dominates."""
    assert weakest(Tier.OBSERVED, Tier.DERIVED) is Tier.DERIVED
    assert weakest(Tier.OBSERVED, Tier.PREDICTED) is Tier.PREDICTED
    assert weakest(Tier.DERIVED, Tier.SPECULATIVE, Tier.OBSERVED) is Tier.SPECULATIVE
    assert weakest(Tier.OBSERVED, Tier.OBSERVED) is Tier.OBSERVED


def test_exact_arithmetic_cannot_upgrade_a_prediction() -> None:
    """A value derived from a prediction is still a prediction.

    This is the rule that makes the taxonomy worth having. Without it, a chain of exact
    arithmetic launders a modelled guess into something that renders like a measurement.
    """
    predicted = Value(
        value=1.0,
        unit="km",
        tier=Tier.PREDICTED,
        receipt=Receipt(tool="test"),
    )
    observed = Value(value=2.0, unit="km", tier=Tier.OBSERVED, receipt=Receipt(tool="test"))

    combined = Value.derived_from(3.0, "km", Receipt(tool="test"), predicted, observed)

    assert combined.tier is Tier.PREDICTED


# --------------------------------------------------------------------------------------------
# Pass geometry
# --------------------------------------------------------------------------------------------


def test_passes_are_internally_consistent() -> None:
    """Every reported pass rises, culminates, then sets, and clears its own mask."""
    start = orbit.tle_epoch(FIXTURE_TLE)
    found = orbit.find_passes(FIXTURE_TLE, BANGALORE, start, days=2.0, min_elevation_deg=10.0)

    assert found, "expected at least one ISS pass over Bangalore in two days"

    for item in found:
        assert item.rise_utc < item.culmination_utc < item.set_utc
        assert item.duration_s > 0
        assert item.max_elevation_deg >= 10.0, (
            "a pass was reported whose peak elevation is below the requested mask"
        )
        # A LEO pass is minutes, not hours. An hours-long "pass" means rise and set from
        # different passes were paired together.
        assert item.duration_s < 3600


def test_passes_are_chronological_and_disjoint() -> None:
    """Passes come back in order and do not overlap."""
    start = orbit.tle_epoch(FIXTURE_TLE)
    found = orbit.find_passes(FIXTURE_TLE, BANGALORE, start, days=2.0, min_elevation_deg=10.0)

    for earlier, later in pairwise(found):
        assert earlier.set_utc < later.rise_utc


def test_higher_elevation_mask_yields_no_more_passes() -> None:
    """Raising the mask can only remove passes, never add them.

    A monotonicity property that holds regardless of the orbit, so it catches an inverted
    comparison in the mask logic without depending on any specific expected count.
    """
    start = orbit.tle_epoch(FIXTURE_TLE)

    lenient = orbit.find_passes(FIXTURE_TLE, BANGALORE, start, days=3.0, min_elevation_deg=5.0)
    strict = orbit.find_passes(FIXTURE_TLE, BANGALORE, start, days=3.0, min_elevation_deg=40.0)

    assert len(strict) <= len(lenient)


def test_station_below_orbit_inclination_sees_passes_and_polar_station_does_not() -> None:
    """Geometry sanity: the ISS never rises for a station far outside its inclination.

    The ISS orbits at 51.6 degrees inclination, so its ground track never reaches the high
    Arctic. A station there must see no passes, while a low-latitude station must see several.
    Catches a swapped latitude/longitude, which is otherwise easy to miss because the numbers
    still look reasonable.
    """
    start = orbit.tle_epoch(FIXTURE_TLE)
    polar = orbit.GroundStation(name="Alert", latitude_deg=82.5, longitude_deg=-62.3)

    assert orbit.find_passes(FIXTURE_TLE, polar, start, days=2.0, min_elevation_deg=10.0) == []
    assert orbit.find_passes(FIXTURE_TLE, BANGALORE, start, days=2.0, min_elevation_deg=10.0)


def test_subpoint_stays_within_valid_geodetic_bounds() -> None:
    """Sub-satellite points are physically valid and tiered honestly."""
    epoch = orbit.tle_epoch(FIXTURE_TLE)

    at_epoch = orbit.subpoint(FIXTURE_TLE, epoch)
    assert -90 <= at_epoch["latitude"].value <= 90
    assert -180 <= at_epoch["longitude"].value <= 180
    assert 300 < at_epoch["altitude"].value < 500, "ISS altitude should be a few hundred km"
    assert at_epoch["latitude"].tier is Tier.DERIVED

    # Away from epoch the model is being propagated, so the tier must weaken.
    later = orbit.subpoint(FIXTURE_TLE, epoch + timedelta(days=1))
    assert later["latitude"].tier is Tier.PREDICTED


def test_ground_track_is_continuous() -> None:
    """Consecutive ground-track samples are close together.

    A jump larger than a LEO satellite can travel in one step means samples are out of order or
    an epoch was mishandled. Longitude is excluded because it legitimately wraps at the
    antimeridian.
    """
    samples = orbit.ground_track(
        FIXTURE_TLE, orbit.tle_epoch(FIXTURE_TLE), minutes=100, step_seconds=30
    )

    assert len(samples) > 100
    for earlier, later in pairwise(samples):
        # ~7.7 km/s over 30 s is ~230 km, which is at most ~2.1 degrees of latitude.
        assert abs(later["lat"] - earlier["lat"]) < 3.0


# --------------------------------------------------------------------------------------------
# API contract
# --------------------------------------------------------------------------------------------


def test_health_reports_ok() -> None:
    """The health endpoint reports a working science stack."""
    body = client.get("/api/health").json()
    assert body["status"] == "ok", body


def test_satellite_lookup_returns_tiered_values() -> None:
    """Lookup distinguishes a published epoch from a computed age."""
    body = client.get("/api/satellite/25544").json()

    assert body["norad_id"] == 25544
    assert body["epoch"]["tier"] == "observed", "a published epoch is measured, not computed"
    assert body["element_set_age"]["tier"] == "derived"
    assert body["element_set_age"]["unit"] == "days"


def test_passes_endpoint_returns_predictions_with_a_receipt() -> None:
    """Predicted passes carry the predicted tier and a full receipt."""
    response = client.post(
        "/api/passes",
        json={
            "norad_id": 25544,
            "latitude_deg": BANGALORE.latitude_deg,
            "longitude_deg": BANGALORE.longitude_deg,
            "elevation_m": BANGALORE.elevation_m,
            "station_name": "Bangalore",
            "min_elevation_deg": 10.0,
            "days": 2.0,
        },
    )
    assert response.status_code == 200
    body = response.json()

    assert body["tier"] == "predicted", "a future pass is modelled, never measured"

    receipt = body["receipt"]
    assert receipt["time_scale"] == "UTC"
    assert receipt["frame"], "a pass must state the frame its geometry was computed in"
    assert receipt["uncertainty"]["position_error_km"] > 0
    assert "refraction" in receipt["notes"]


def test_invalid_station_coordinates_are_rejected() -> None:
    """Out-of-range input is refused rather than silently producing nonsense."""
    response = client.post(
        "/api/passes",
        json={"norad_id": 25544, "latitude_deg": 120.0, "longitude_deg": 0.0},
    )
    assert response.status_code == 422


def test_unknown_satellite_returns_typed_error() -> None:
    """A catalog failure surfaces as a typed error, never a stack trace."""
    catalog.clear_cache()

    def _fail(_url: str) -> str:
        raise catalog.CatalogError("simulated outage")

    original = catalog._fetch_text
    catalog._fetch_text = _fail  # type: ignore[assignment]
    try:
        response = client.get("/api/satellite/99999")
    finally:
        catalog._fetch_text = original  # type: ignore[assignment]

    assert response.status_code == 502
    body = response.json()
    assert body["error"]["code"] == "catalog_unavailable"
    assert "Traceback" not in str(body)


def test_operational_notice_accompanies_predictions() -> None:
    """Predictions ship with the not-for-operational-use notice.

    The licence disclaims warranty in a file nobody opens. This is the same limit stated where a
    user will actually encounter it.
    """
    body = client.post(
        "/api/passes",
        json={
            "norad_id": 25544,
            "latitude_deg": BANGALORE.latitude_deg,
            "longitude_deg": BANGALORE.longitude_deg,
        },
    ).json()

    assert "mission operations" in body["notice"]


def test_now_is_after_the_fixture_epoch() -> None:
    """Guard: the frozen fixture is in the past, so live queries propagate forward.

    If this ever fails the fixture has drifted into the future and the pass tests would be
    exercising backward propagation without anyone noticing.
    """
    assert orbit.tle_epoch(FIXTURE_TLE) < datetime.now(UTC)


# --------------------------------------------------------------------------------------------
# Error-message quality
#
# Found by an external review of the running app: an unknown catalog number surfaced a raw
# "HTTP Error 404" and an out-of-range elevation mask surfaced a bare "HTTP 422". The rejections
# were correct; the messages were not actionable. These lock in the fixes.
# --------------------------------------------------------------------------------------------


def test_unknown_satellite_returns_404_with_a_readable_message() -> None:
    """An unknown catalog number is the caller's mistake, so 404 and a useful message."""
    catalog.clear_cache()

    def _not_found(_url: str) -> str:
        return "No GP data found"

    original = catalog._fetch_text
    catalog._fetch_text = _not_found  # type: ignore[assignment]
    try:
        response = client.get("/api/satellite/99999999")
    finally:
        catalog._fetch_text = original  # type: ignore[assignment]

    assert response.status_code == 404, "an unknown satellite is not a server failure"

    body = response.json()
    assert body["error"]["code"] == "satellite_not_found"
    assert "99999999" in body["error"]["message"]
    assert "HTTP" not in body["error"]["message"], "raw transport detail leaked to the user"


def test_a_missing_satellite_is_not_retried() -> None:
    """A 404 is not transient, so it must not be retried.

    Retrying an unknown catalog number three times delays the answer and is impolite to
    Celestrak, for a result that cannot change.
    """
    catalog.clear_cache()
    attempts = 0

    def _counting(_url: str) -> str:
        nonlocal attempts
        attempts += 1
        return "No GP data found"

    original = catalog._fetch_text
    catalog._fetch_text = _counting  # type: ignore[assignment]
    try:
        client.get("/api/satellite/99999999")
    finally:
        catalog._fetch_text = original  # type: ignore[assignment]

    assert attempts == 1, f"expected a single attempt for a missing object, made {attempts}"


def test_out_of_range_elevation_mask_explains_itself() -> None:
    """A rejected elevation mask names the field and the reason, not just a status code."""
    response = client.post(
        "/api/passes",
        json={
            "norad_id": 25544,
            "latitude_deg": 12.97,
            "longitude_deg": 77.59,
            "min_elevation_deg": 95.0,
        },
    )

    assert response.status_code == 422

    body = response.json()
    assert body["error"]["code"] == "invalid_input"
    assert "min_elevation_deg" in body["error"]["message"], "the failing field must be named"
    assert len(body["error"]["message"]) > 20, "the message must say more than that it failed"


# --------------------------------------------------------------------------------------------
# Query windows
#
# Found by the evaluation: the agent scored 12% on pass counts against a 100% tool ceiling,
# because /api/passes always searched from now and no parameter existed to say otherwise. A
# question about a named window could only be answered by searching a different one, so counts
# came back consistently low. The tool was right; the API could not express the question.
# --------------------------------------------------------------------------------------------


def test_passes_can_search_from_a_stated_instant() -> None:
    """A pass search honours an explicit start time rather than always using now."""
    start = "2026-08-12T00:00:00Z"

    response = client.post(
        "/api/passes",
        json={
            "norad_id": 25544,
            "latitude_deg": BANGALORE.latitude_deg,
            "longitude_deg": BANGALORE.longitude_deg,
            "elevation_m": BANGALORE.elevation_m,
            "min_elevation_deg": 10.0,
            "days": 3,
            "from_utc": start,
        },
    )
    assert response.status_code == 200

    body = response.json()
    assert body["searched_from_utc"].startswith("2026-08-12T00:00:00"), (
        "the search must begin where the caller asked, not at the current time"
    )

    for item in body["passes"]:
        assert item["rise_utc"] >= start, "a pass before the requested window leaked in"


def test_pass_window_changes_the_answer() -> None:
    """Different windows produce different results.

    Guards against the parameter being accepted and ignored -- which is exactly how the original
    defect behaved, and why it survived a passing test suite.
    """
    def count(from_utc: str) -> int:
        return client.post(
            "/api/passes",
            json={
                "norad_id": 25544,
                "latitude_deg": BANGALORE.latitude_deg,
                "longitude_deg": BANGALORE.longitude_deg,
                "min_elevation_deg": 10.0,
                "days": 1,
                "from_utc": from_utc,
            },
        ).json()["passes"]

    first = count("2026-08-12T00:00:00Z")
    later = count("2026-08-20T00:00:00Z")

    assert first != later, "the window parameter appears to be ignored"


def test_state_and_passes_reject_unparseable_timestamps() -> None:
    """A malformed instant is refused with a message naming the field."""
    for path, payload in (
        ("/api/state", {"norad_id": 25544, "at_utc": "not-a-date"}),
        (
            "/api/passes",
            {
                "norad_id": 25544,
                "latitude_deg": 12.97,
                "longitude_deg": 77.59,
                "from_utc": "12/08/2026",
            },
        ),
    ):
        response = client.post(path, json=payload)
        assert response.status_code == 422, f"{path} accepted a bad timestamp"

        body = response.json()
        assert body["error"]["code"] == "invalid_input"
        assert "utc" in body["error"]["message"].lower(), "the failing field must be named"


def test_naive_timestamps_are_treated_as_utc() -> None:
    """A timestamp without an offset is interpreted as UTC rather than local time.

    Every value this service returns is UTC. Silently applying the server's local zone would
    shift results by hours depending on where the process happens to run.
    """
    aware = client.post("/api/state", json={"norad_id": 25544, "at_utc": "2026-08-12T00:00:00Z"})
    naive = client.post("/api/state", json={"norad_id": 25544, "at_utc": "2026-08-12T00:00:00"})

    assert aware.status_code == naive.status_code == 200
    assert aware.json()["latitude"]["value"] == naive.json()["latitude"]["value"]

"""Tests for the science service health endpoint.

These are tier-1 analytic checks per test.md: closed-form values that catch gross errors and
run on every commit. The TT-UTC offset is fixed by definition, so this is a genuine
independent check rather than the library grading its own homework.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from api.main import EXPECTED_TT_MINUS_UTC_SECONDS, app

client = TestClient(app)


def test_health_reports_ok() -> None:
    """The endpoint responds 200 and reports a healthy service.

    A "degraded" result here means a scientific dependency failed to import or the time-scale
    check failed -- either is a real deployment problem, not a flaky test.
    """
    response = client.get("/api/health")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok", f"service degraded: {body}"


def test_all_science_libraries_import() -> None:
    """Every scientific dependency imports and reports a version.

    Provenance depends on knowing which library versions produced a number, so a library that
    cannot report its version is a failure even if it imported.
    """
    body = client.get("/api/health").json()

    for name, version in body["libraries"].items():
        assert not version.startswith("unavailable"), f"{name} failed to import: {version}"


def test_health_identifies_the_running_deployment() -> None:
    """Deployment metadata is always present, even when its local values are placeholders.

    A manual deployment can be healthy while still running an old commit. Keeping the identity
    in the same response as liveness lets the launch smoke test distinguish those states.
    """
    deployment = client.get("/api/health").json()["deployment"]

    assert set(deployment) == {"environment", "commit_sha", "deployment_url"}
    assert all(isinstance(value, str) and value for value in deployment.values())


def test_tt_minus_utc_offset_is_correct() -> None:
    """TT - UTC equals 69.184 s for a 2026 epoch.

    TT - TAI is 32.184 s by definition and TAI - UTC has been 37 s since the 2017 leap second.
    Getting this wrong shifts an epoch by ~69 s, which moves a LEO satellite roughly 500 km
    along-track -- a wrong answer that looks entirely plausible.
    """
    body = client.get("/api/health").json()
    check = body["checks"]["time_scales"]

    assert check["within_tolerance"], check
    assert check["measured_tt_minus_utc_s"] == EXPECTED_TT_MINUS_UTC_SECONDS

"""Health endpoint for the Python science service.

Deployed by Vercel's Python runtime as ``/api/health``.

This is deliberately more than a liveness ping. It proves the scientific stack actually
imported *in the deployed environment* and that time-scale conversion produces the right
answer -- the class of silent failure that rules.md B3 exists to prevent. An endpoint that
only returns ``{"ok": true}`` would still pass while astropy was subtly broken.
"""

from __future__ import annotations

import platform
import sys
from typing import Any

from fastapi import FastAPI

app = FastAPI(
    title="space-sim science service",
    description="Orbital mechanics tools with explicit frames, time scales, and units.",
)

# TT - UTC at any epoch after the 2017 leap second, in seconds:
#   TT - TAI = 32.184 s (fixed by definition)
#   TAI - UTC = 37 s    (constant since 2017-01-01; no leap second has been added since)
# Revisit if a leap second is ever introduced again.
EXPECTED_TT_MINUS_UTC_SECONDS = 69.184
TIME_SCALE_TOLERANCE_SECONDS = 1e-6


def _check_time_scales() -> dict[str, Any]:
    """Verify astropy converts UTC to TT correctly.

    Time-scale confusion is one of the two classic silent failures in orbital software
    (reference frames being the other): mixing UTC and TT silently shifts an epoch by ~69
    seconds, which moves a LEO satellite by roughly 500 km along-track. Checking it here means
    a broken deployment fails loudly at the health endpoint instead of quietly in an answer.

    Returns:
        A dict reporting the measured TT-UTC offset, the expected value, and whether the
        measurement is within tolerance.
    """
    from astropy.time import Time

    epoch_utc = Time("2026-01-01T00:00:00", scale="utc")
    epoch_tt = epoch_utc.tt

    # Use the two-part Julian date rather than the combined `.jd`.
    #
    # A JD near 2_461_041 consumes most of a float64's ~16 significant digits, leaving only
    # ~1e-4 s of resolution -- so `(tt.jd - utc.jd) * 86400` returns 69.184014 s instead of
    # 69.184, an error of 14 microseconds introduced purely by the subtraction. astropy splits
    # every epoch into jd1 (large, usually integral) + jd2 (small fraction) precisely so this
    # difference can be taken without precision collapse.
    #
    # Note this is NOT `epoch_tt - epoch_utc`: those are the same instant expressed in two
    # scales, so subtracting them as Time objects correctly yields zero. The 69.184 s is a
    # difference in numeric representation, not in the instant itself.
    #
    # astropy returns numpy scalars; cast to native Python at this boundary, since numpy types
    # are not JSON-serializable by pydantic.
    offset_seconds = float(
        ((epoch_tt.jd1 - epoch_utc.jd1) + (epoch_tt.jd2 - epoch_utc.jd2)) * 86400.0
    )

    return {
        "measured_tt_minus_utc_s": round(offset_seconds, 9),
        "expected_tt_minus_utc_s": EXPECTED_TT_MINUS_UTC_SECONDS,
        "within_tolerance": bool(
            abs(offset_seconds - EXPECTED_TT_MINUS_UTC_SECONDS) < TIME_SCALE_TOLERANCE_SECONDS
        ),
    }


def _library_versions() -> dict[str, str]:
    """Report the installed version of each scientific dependency.

    Numerical results are only reproducible against a known set of library versions, so the
    deployed versions are part of an answer's provenance. Reporting them here makes the
    deployed environment self-describing.

    Returns:
        A mapping of library name to version string, or to an error marker if it failed to
        import.
    """
    versions: dict[str, str] = {}
    for name in ("astropy", "skyfield", "sgp4", "fastapi", "pydantic"):
        try:
            versions[name] = __import__(name).__version__
        except Exception as exc:  # noqa: BLE001 - surfacing any import failure is the point
            versions[name] = f"unavailable: {type(exc).__name__}"
    return versions


@app.get("/api/health")
def health() -> dict[str, Any]:
    """Report service liveness, environment, and a time-scale self-check.

    Returns:
        Status payload. ``status`` is ``"ok"`` only when every scientific dependency imported
        and the time-scale check passed; otherwise ``"degraded"``.
    """
    versions = _library_versions()

    try:
        time_check = _check_time_scales()
    except Exception as exc:  # noqa: BLE001 - a failed check is a reportable state, not a 500
        time_check = {"error": f"{type(exc).__name__}: {exc}", "within_tolerance": False}

    imports_ok = not any(v.startswith("unavailable") for v in versions.values())
    healthy = imports_ok and bool(time_check.get("within_tolerance"))

    return {
        "status": "ok" if healthy else "degraded",
        "service": "science",
        "python": platform.python_version(),
        "platform": sys.platform,
        "libraries": versions,
        "checks": {"time_scales": time_check},
    }

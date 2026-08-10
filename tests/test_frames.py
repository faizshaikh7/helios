"""Differential tests: astropy reference frames against Orekit.

Tier-3 per .agent/test.md. Frames are the half of the silent-failure surface that time scales
do not cover, and the harder half: a wrong frame does not raise, it returns a clean number in
the wrong place.

Tolerances are angular, not linear, because frame errors are rotations -- the same misrotation
displaces a GEO satellite six times further than a LEO one. A fixed metre budget would be
simultaneously too strict at LEO and too permissive at GEO.

Measured disagreement between the two implementations (see values below) sits far under the
tolerances, which in turn sit far under the magnitude of any real bug.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from astropy import units as u
from astropy.coordinates import GCRS, ITRS, TEME, CartesianRepresentation
from astropy.time import Time
from astropy.utils import iers

REFERENCE_PATH = Path(__file__).parent / "reference" / "frames.json"

# Never reach for the network during a test: CI must be deterministic and offline-safe.
# astropy falls back to its bundled IERS-B table, which is what the tolerances were measured
# against.
iers.conf.auto_download = False

MILLIARCSEC_TO_RAD = np.pi / (180.0 * 3600.0 * 1000.0)

# Tolerances, in milliarcseconds of angular separation.
#
# Measured astropy-vs-Orekit disagreement at r=7000 km:
#     GCRF -> ITRF   0.75 mas  (2.5 cm)
#     TEME -> ITRF  33 mas     (1.12 m)
#
# TEME agrees an order of magnitude worse because it has several competing conventions, where
# GCRF and ITRF are rigidly defined. Each tolerance leaves headroom for Earth-orientation data
# updating underneath us, while staying far below any real defect:
#     omitted polar motion    ~300 mas  (~10 m at LEO)
#     TEME/GCRF confusion   ~4.7e6 mas  (~44 km at LEO)
TOLERANCE_MAS = {"GCRF": 5.0, "TEME": 100.0}

ASTROPY_FRAMES = {"GCRF": GCRS, "TEME": TEME}


def _transforms() -> list[dict[str, Any]]:
    """Return the committed reference transforms, for parametrizing tests."""
    if not REFERENCE_PATH.exists():
        return []
    return json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))["transforms"]


def _case_id(entry: dict[str, Any]) -> str:
    """Build a readable test id from a reference entry."""
    return f"{entry['epoch_utc']}-{entry['source_frame']}-{entry['vector_name']}"


@pytest.mark.parametrize("entry", _transforms(), ids=_case_id)
def test_frame_transform_matches_orekit(entry: dict[str, Any]) -> None:
    """astropy and Orekit agree when rotating a position into ITRF.

    A failure here means one of: a different Earth-orientation dataset, a precession-nutation
    model mismatch, omitted polar motion, or an outright frame mix-up. The magnitude of the
    disagreement distinguishes them -- see the tolerance commentary above.
    """
    epoch = Time(entry["epoch_utc"], scale="utc")
    source_frame = ASTROPY_FRAMES[entry["source_frame"]]

    representation = CartesianRepresentation(*entry["source_xyz_m"], unit=u.m)
    measured = (
        source_frame(representation, obstime=epoch)
        .transform_to(ITRS(obstime=epoch))
        .cartesian.xyz.to_value(u.m)
    )

    expected = np.array(entry["target_xyz_m"])
    separation_m = float(np.linalg.norm(measured - expected))

    radius_m = float(np.linalg.norm(expected))
    tolerance_m = TOLERANCE_MAS[entry["source_frame"]] * MILLIARCSEC_TO_RAD * radius_m
    separation_mas = separation_m / radius_m / MILLIARCSEC_TO_RAD

    assert separation_m <= tolerance_m, (
        f"{entry['source_frame']}->ITRF disagreement at {entry['epoch_utc']} "
        f"({entry['vector_name']}): {separation_m:.4f} m = {separation_mas:.2f} mas, "
        f"tolerance {tolerance_m:.4f} m = {TOLERANCE_MAS[entry['source_frame']]} mas"
    )


def test_teme_and_gcrf_are_actually_different() -> None:
    """The reference distinguishes TEME from GCRF.

    Guards the test suite itself. If a generator bug made both source frames resolve to the same
    Orekit frame, every transform test above would still pass while silently checking nothing --
    and the frame mix-up they exist to catch would sail straight through.
    """
    by_frame: dict[str, dict[str, list[float]]] = {"GCRF": {}, "TEME": {}}
    for entry in _transforms():
        key = f"{entry['epoch_utc']}-{entry['vector_name']}"
        by_frame[entry["source_frame"]][key] = entry["target_xyz_m"]

    if not by_frame["GCRF"]:
        pytest.skip("frames.json not generated")

    for key, gcrf_xyz in by_frame["GCRF"].items():
        separation = float(np.linalg.norm(np.array(gcrf_xyz) - np.array(by_frame["TEME"][key])))
        assert separation > 1e3, (
            f"TEME and GCRF results for {key} differ by only {separation:.1f} m; "
            "they should differ by tens of km"
        )


def test_reference_records_its_provenance() -> None:
    """The frame reference states where it came from and under which conventions."""
    if not REFERENCE_PATH.exists():
        pytest.skip("frames.json not generated")

    provenance = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))["_provenance"]

    assert provenance["source"] == "orekit"
    assert provenance["orekit_jpype_version"] != "unknown"
    assert "simpleEOP=False" in provenance["itrf_convention"]

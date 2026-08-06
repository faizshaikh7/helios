"""Differential tests: astropy time scales against Orekit reference values.

This is a tier-3 check per .agent/test.md -- the headline truth source. Orekit is a separate
codebase, in a separate language, by a separate community, so agreement here is real evidence.
Checking astropy against astropy would prove only that a function can call itself.

Reference values are committed in `tests/reference/time_scales.json`, regenerated deliberately
by `tools/reference/generate_time_scales.py`. CI therefore needs no JVM, and a changed
reference value shows up as a reviewable diff rather than a silent drift.

Epochs straddle leap-second boundaries, which is where independent implementations most often
disagree.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from astropy.time import Time

REFERENCE_PATH = Path(__file__).parent / "reference" / "time_scales.json"

# Both libraries resolve time-scale offsets from the same published leap-second tables, so
# agreement should be near-exact. A nanosecond of slack absorbs float64 representation noise
# without hiding a real disagreement -- a genuine leap-second or table mismatch is a whole
# second, nine orders of magnitude larger.
TOLERANCE_SECONDS = 1e-9


def _load_reference() -> dict[str, Any]:
    """Load the committed Orekit reference document.

    Returns:
        The parsed reference document.

    Raises:
        pytest.skip: If the reference has not been generated yet.
    """
    if not REFERENCE_PATH.exists():
        pytest.skip(
            f"{REFERENCE_PATH.name} not generated - run "
            "`uv run python tools/reference/generate_time_scales.py`"
        )
    return json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))


def _scale_offset_seconds(epoch_utc: str, scale: str) -> float:
    """Compute (scale - UTC) in seconds at a given epoch, using astropy.

    Uses the two-part Julian date rather than the combined ``.jd``. A JD near 2.46e6 consumes
    most of a float64's significant digits, so differencing combined JDs loses roughly 1e-4 s
    -- enough to swamp the nanosecond tolerance this test asserts. astropy splits every epoch
    into jd1 (large) + jd2 (small) precisely so this subtraction stays exact.

    Note this is deliberately not ``Time(...).tt - Time(...)``: those are the same instant
    expressed in two scales and correctly subtract to zero. The offset is a difference in
    numeric representation, not in the instant.

    Args:
        epoch_utc: ISO-8601 UTC timestamp.
        scale: Target astropy time scale, e.g. ``"tt"`` or ``"tai"``.

    Returns:
        The offset in seconds.
    """
    utc = Time(epoch_utc, scale="utc")
    target = getattr(utc, scale)
    return float(((target.jd1 - utc.jd1) + (target.jd2 - utc.jd2)) * 86400.0)


def _epochs() -> list[dict[str, Any]]:
    """Return the reference epochs, for parametrizing tests."""
    if not REFERENCE_PATH.exists():
        return []
    return json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))["epochs"]


@pytest.mark.parametrize("entry", _epochs(), ids=lambda e: e["epoch_utc"])
def test_tt_minus_utc_matches_orekit(entry: dict[str, Any]) -> None:
    """astropy and Orekit agree on TT - UTC at each reference epoch.

    A disagreement of ~1 s means the two disagree about leap seconds. A disagreement of 32.184 s
    means one of them confused TT with TAI. Either would silently corrupt every downstream
    epoch, and a 69 s error moves a low-Earth-orbit satellite roughly 500 km along-track.
    """
    measured = _scale_offset_seconds(entry["epoch_utc"], "tt")
    expected = entry["tt_minus_utc_s"]

    assert measured == pytest.approx(expected, abs=TOLERANCE_SECONDS), (
        f"TT-UTC disagreement at {entry['epoch_utc']}: "
        f"astropy={measured!r} orekit={expected!r} (delta={measured - expected:.3e} s)"
    )


@pytest.mark.parametrize("entry", _epochs(), ids=lambda e: e["epoch_utc"])
def test_tai_minus_utc_matches_orekit(entry: dict[str, Any]) -> None:
    """astropy and Orekit agree on TAI - UTC at each reference epoch.

    TAI - UTC is the accumulated leap-second count, so this directly tests that both libraries
    carry the same leap-second table across the 2012 and 2017 transitions.
    """
    measured = _scale_offset_seconds(entry["epoch_utc"], "tai")
    expected = entry["tai_minus_utc_s"]

    assert measured == pytest.approx(expected, abs=TOLERANCE_SECONDS), (
        f"TAI-UTC disagreement at {entry['epoch_utc']}: "
        f"astropy={measured!r} orekit={expected!r} (delta={measured - expected:.3e} s)"
    )


def test_reference_records_its_provenance() -> None:
    """The reference file states where it came from.

    A reference value with no provenance is indistinguishable from a number someone typed in to
    make a test pass, which is exactly the failure this whole tier exists to prevent.
    """
    provenance = _load_reference()["_provenance"]

    assert provenance["source"] == "orekit"
    assert provenance["orekit_jpype_version"] != "unknown"
    assert provenance["generated_utc"]

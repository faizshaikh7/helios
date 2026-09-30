"""Generate conjunction-screening references with Orekit's independent SGP4 implementation.

Two fixed object pairs are scanned over a fixed 24-hour interval.  Orekit supplies every
propagated state; a bounded scalar search refines each coarse local minimum.  The resulting file
is committed so ordinary tests need neither a JVM nor network access.

Run manually from ``backend/``:

    uv run python ../tools/reference/generate_conjunction.py
"""

from __future__ import annotations

import json
import math
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from orekit_setup import REPO_ROOT, initialise, package_version

OUTPUT_PATH = REPO_ROOT / "backend" / "tests" / "reference" / "conjunction.json"
START_UTC = datetime(2026, 8, 12, tzinfo=UTC)
DURATION_S = 24 * 3600.0
STEP_S = 60.0
TOLERANCE_S = 0.0001

OBJECTS = {
    25544: {
        "name": "ISS (ZARYA)",
        "line1": "1 25544U 98067A   26222.18186727  .00004351  00000+0  85915-4 0  9998",
        "line2": "2 25544  51.6326  32.8714 0007377  31.2060 328.9366 15.49401464580127",
    },
    20580: {
        "name": "HST",
        "line1": "1 20580U 90037B   26221.98467551  .00003912  00000+0  11826-3 0  9997",
        "line2": "2 20580  28.4728  72.7277 0002381  30.1378 329.9355 15.31308603796824",
    },
    33591: {
        "name": "NOAA 19",
        "line1": "1 33591U 09005A   26222.20202360  .00000006  00000+0  26832-4 0  9994",
        "line2": "2 33591  98.9488 293.1071 0012901 234.8178 125.1787 14.13481610902111",
    },
}
PAIRS = [(25544, 33591), (20580, 33591)]


def _bounded_minimum(
    function: Callable[[float], float], lower: float, upper: float
) -> tuple[float, float]:
    """Refine one bracketed Orekit separation minimum with golden-section search.

    Args:
        function: Squared separation as a function of seconds from the interval start.
        lower: Inclusive lower bracket in seconds.
        upper: Inclusive upper bracket in seconds.

    Returns:
        Minimizing offset and squared separation.
    """
    inverse_phi = (math.sqrt(5.0) - 1.0) / 2.0
    left, right = lower, upper
    c = right - inverse_phi * (right - left)
    d = left + inverse_phi * (right - left)
    value_c, value_d = function(c), function(d)

    while right - left > TOLERANCE_S:
        if value_c <= value_d:
            right, d, value_d = d, c, value_c
            c = right - inverse_phi * (right - left)
            value_c = function(c)
        else:
            left, c, value_c = c, d, value_d
            d = left + inverse_phi * (right - left)
            value_d = function(d)

    candidates = [(lower, function(lower)), (upper, function(upper)), (c, value_c), (d, value_d)]
    return min(candidates, key=lambda item: item[1])


def generate() -> dict[str, Any]:
    """Run both fixed screens in Orekit and return the reference document.

    Returns:
        Provenance, fixed inputs, and closest-approach results for each object pair.
    """
    initialise()

    from org.orekit.propagation.analytical.tle import (  # type: ignore[import-not-found]
        TLE,
        TLEPropagator,
    )
    from org.orekit.time import AbsoluteDate, TimeScalesFactory  # type: ignore[import-not-found]

    utc = TimeScalesFactory.getUTC()
    start = AbsoluteDate(
        START_UTC.year,
        START_UTC.month,
        START_UTC.day,
        START_UTC.hour,
        START_UTC.minute,
        float(START_UTC.second),
        utc,
    )
    propagators = {
        norad_id: TLEPropagator.selectExtrapolator(TLE(item["line1"], item["line2"]))
        for norad_id, item in OBJECTS.items()
    }

    cases = []
    for primary_id, secondary_id in PAIRS:
        primary = propagators[primary_id]
        secondary = propagators[secondary_id]
        frame = primary.getFrame()

        def relative_state(
            offset_s: float,
            primary_propagator: Any = primary,
            secondary_propagator: Any = secondary,
            reference_frame: Any = frame,
        ) -> tuple[list[float], list[float]]:
            """Return Orekit secondary-minus-primary TEME state in SI units.

            The Java objects are bound as defaults so each loop iteration's objective remains
            attached to its own pair if the generator is later made lazy.
            """
            date = start.shiftedBy(float(offset_s))
            primary_pv = primary_propagator.propagate(date).getPVCoordinates(reference_frame)
            secondary_pv = secondary_propagator.propagate(date).getPVCoordinates(reference_frame)
            position = secondary_pv.getPosition().subtract(primary_pv.getPosition())
            velocity = secondary_pv.getVelocity().subtract(primary_pv.getVelocity())
            return (
                [float(position.getX()), float(position.getY()), float(position.getZ())],
                [float(velocity.getX()), float(velocity.getY()), float(velocity.getZ())],
            )

        def squared_distance(offset_s: float) -> float:
            """Return Orekit squared TEME separation in square metres."""
            position, _ = relative_state(offset_s)
            return sum(component * component for component in position)

        offsets = [float(value) for value in range(0, int(DURATION_S) + 1, int(STEP_S))]
        squared = [squared_distance(offset) for offset in offsets]
        candidates = [(offsets[0], squared[0]), (offsets[-1], squared[-1])]
        for index in range(1, len(offsets) - 1):
            if squared[index] <= squared[index - 1] and squared[index] <= squared[index + 1]:
                candidates.append(
                    _bounded_minimum(squared_distance, offsets[index - 1], offsets[index + 1])
                )

        best_offset, best_squared = min(candidates, key=lambda item: item[1])
        relative_position, relative_velocity = relative_state(best_offset)
        tca = start.shiftedBy(best_offset)
        relative_speed = math.sqrt(sum(component * component for component in relative_velocity))

        cases.append(
            {
                "primary_norad_id": primary_id,
                "secondary_norad_id": secondary_id,
                "tca_utc": tca.toString(utc),
                "offset_seconds": round(best_offset, 6),
                "miss_distance_m": round(math.sqrt(best_squared), 6),
                "relative_speed_m_s": round(relative_speed, 9),
                "relative_position_teme_m": [round(value, 6) for value in relative_position],
                "relative_velocity_teme_m_s": [round(value, 9) for value in relative_velocity],
            }
        )

    return {
        "_provenance": {
            "source": "orekit",
            "orekit_jpype_version": package_version("orekit_jpype"),
            "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "generator": "tools/reference/generate_conjunction.py",
            "frame": "TEME",
            "time_scale": "UTC",
            "note": (
                "Orekit supplied every propagated state. The coarse scan and bounded refinement "
                "are independent of the production Skyfield/sgp4 code path."
            ),
        },
        "start_utc": START_UTC.isoformat(),
        "duration_hours": DURATION_S / 3600.0,
        "step_seconds": STEP_S,
        "objects": {str(key): value for key, value in OBJECTS.items()},
        "cases": cases,
    }


def main() -> int:
    """Generate and write the committed conjunction reference.

    Returns:
        Process exit code.
    """
    document = generate()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT_PATH.relative_to(REPO_ROOT)} ({len(document['cases'])} cases)")
    for case in document["cases"]:
        print(
            f"  {case['primary_norad_id']} / {case['secondary_norad_id']}: "
            f"{case['miss_distance_m'] / 1000:.6f} km at {case['tca_utc']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

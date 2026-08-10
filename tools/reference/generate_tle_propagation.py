"""Generate SGP4 propagation reference values using Orekit.

Grades skyfield/sgp4 -- the propagator every answer in slice 1 depends on -- against Orekit's
independent SGP4 implementation.

Both implementations descend from the same published SGP4 algorithm, so this does not prove the
algorithm correct. What it does catch is the far likelier failure: wrong frame, wrong epoch
interpretation, wrong units, or a mishandled TLE field. Those are implementation choices where
the two libraries were written independently.

A **fixed, hard-coded TLE** is used rather than a live fetch, so the reference is reproducible
forever and the test never depends on the network or on what Celestrak happens to be serving.

Run manually; never in CI:

    uv run python tools/reference/generate_tle_propagation.py
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import Any

from orekit_setup import REPO_ROOT, initialise, package_version

OUTPUT_PATH = REPO_ROOT / "tests" / "reference" / "tle_propagation.json"

# ISS (ZARYA), NORAD 25544. Frozen deliberately -- see module docstring.
TLE_NAME = "ISS (ZARYA)"
TLE_LINE1 = "1 25544U 98067A   24001.50000000  .00016717  00000-0  30150-3 0  9004"
TLE_LINE2 = "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.49815310 10000"

# Offsets from the element-set epoch, in minutes. Zero exercises the epoch itself; the rest
# exercise propagation, including a full day where SGP4 error is materially larger.
OFFSETS_MINUTES = [0, 10, 60, 360, 1440]


def generate() -> dict[str, Any]:
    """Propagate the fixed TLE with Orekit and record TEME positions and velocities.

    TEME is what SGP4 natively produces; converting to another frame here would fold a frame
    transformation into what is meant to be a propagation check. Frame conversion is verified
    separately by tests/test_frames.py.

    Returns:
        A reference document with provenance metadata and one entry per offset.
    """
    initialise()

    from org.orekit.propagation.analytical.tle import (  # type: ignore[import-not-found]
        TLE,
        TLEPropagator,
    )
    from org.orekit.time import TimeScalesFactory  # type: ignore[import-not-found]

    utc = TimeScalesFactory.getUTC()
    tle = TLE(TLE_LINE1, TLE_LINE2)
    propagator = TLEPropagator.selectExtrapolator(tle)

    epoch = tle.getDate()
    teme = propagator.getFrame()

    entries = []
    for minutes in OFFSETS_MINUTES:
        date = epoch.shiftedBy(float(minutes * 60))
        state = propagator.propagate(date)
        pv = state.getPVCoordinates(teme)

        position = pv.getPosition()
        velocity = pv.getVelocity()

        entries.append(
            {
                "offset_minutes": minutes,
                "epoch_utc": date.toString(utc),
                "position_teme_m": [
                    round(float(position.getX()), 4),
                    round(float(position.getY()), 4),
                    round(float(position.getZ()), 4),
                ],
                "velocity_teme_m_s": [
                    round(float(velocity.getX()), 6),
                    round(float(velocity.getY()), 6),
                    round(float(velocity.getZ()), 6),
                ],
            }
        )

    return {
        "_provenance": {
            "source": "orekit",
            "orekit_jpype_version": package_version("orekit_jpype"),
            "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "generator": "tools/reference/generate_tle_propagation.py",
            "frame": "TEME",
            "note": (
                "Independent SGP4 implementation used as ground truth for skyfield/sgp4. Both "
                "descend from the same published algorithm, so this checks implementation "
                "choices -- frame, epoch handling, units, TLE field parsing -- not the algorithm."
            ),
        },
        "tle": {"name": TLE_NAME, "line1": TLE_LINE1, "line2": TLE_LINE2},
        "states": entries,
    }


def main() -> int:
    """Generate the propagation reference file and write it to disk.

    Returns:
        Process exit code.
    """
    document = generate()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

    print(f"wrote {OUTPUT_PATH.relative_to(REPO_ROOT)}  ({len(document['states'])} states)")
    for entry in document["states"]:
        x, y, z = entry["position_teme_m"]
        print(f"  +{entry['offset_minutes']:>5} min  [{x / 1e3:10.3f}, {y / 1e3:10.3f}, "
              f"{z / 1e3:10.3f}] km")
    return 0


if __name__ == "__main__":
    sys.exit(main())

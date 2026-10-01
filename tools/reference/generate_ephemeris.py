"""Generate planetary-position references from Orekit's JPL ephemeris.

The reference grades both production modes: packaged JPL DE421 and the ERFA analytic fallback.
Orekit reads the JPL ephemeris in its own data bundle through an independent implementation, so
the comparison measures cross-release kernel disagreement for precision mode and model departure
for analytic mode without letting either production code path grade itself.

Positions are barycentric ICRF, which is what both sides can express without ambiguity.

Run manually; never in CI:

    uv run python tools/reference/generate_ephemeris.py
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import Any

from orekit_setup import REPO_ROOT, initialise, package_version

OUTPUT_PATH = REPO_ROOT / "backend" / "tests" / "reference" / "ephemeris.json"

BODIES = [
    "mercury",
    "venus",
    "earth",
    "mars",
    "jupiter",
    "saturn",
    "uranus",
    "neptune",
    "moon",
    "sun",
]

# Epochs spanning a wide arc. An analytic series is fitted over a finite interval and degrades
# away from it, so sampling one year would flatter it.
EPOCHS_TDB = [
    "2000-01-01T12:00:00",
    "2015-06-15T00:00:00",
    "2026-08-18T00:00:00",
    "2030-03-20T12:00:00",
    "2040-12-31T00:00:00",
]

# Orekit's celestial-body names.
OREKIT_NAMES = {
    "mercury": "MERCURY",
    "venus": "VENUS",
    "earth": "EARTH",
    "mars": "MARS",
    "jupiter": "JUPITER",
    "saturn": "SATURN",
    "uranus": "URANUS",
    "neptune": "NEPTUNE",
    "moon": "MOON",
    "sun": "SUN",
}


def generate() -> dict[str, Any]:
    """Sample each body's barycentric position at each epoch.

    Returns:
        A reference document with provenance and one entry per body and epoch.
    """
    initialise()

    from org.orekit.bodies import CelestialBodyFactory  # type: ignore[import-not-found]
    from org.orekit.frames import FramesFactory  # type: ignore[import-not-found]
    from org.orekit.time import (  # type: ignore[import-not-found]
        AbsoluteDate,
        TimeScalesFactory,
    )

    tdb = TimeScalesFactory.getTDB()

    # ICRF, barycentric. Orekit's GCRF is Earth-centred; the solar-system barycentre frame is
    # what makes planetary positions comparable without an extra translation.
    icrf = FramesFactory.getICRF()

    entries = []
    for body_name in BODIES:
        body = CelestialBodyFactory.getBody(OREKIT_NAMES[body_name])

        for iso in EPOCHS_TDB:
            date = AbsoluteDate(iso, tdb)
            position = body.getPVCoordinates(date, icrf).getPosition()

            entries.append(
                {
                    "body": body_name,
                    "epoch_tdb": iso,
                    # Metres, barycentric ICRF. Kept in metres rather than AU so a unit error
                    # in the module under test cannot be absorbed by a matching conversion here.
                    "x_m": float(position.getX()),
                    "y_m": float(position.getY()),
                    "z_m": float(position.getZ()),
                }
            )

        print(f"  {body_name:9} sampled at {len(EPOCHS_TDB)} epochs")

    return {
        "_provenance": {
            "source": "orekit",
            "method": "CelestialBodyFactory against the JPL DE ephemeris in the Orekit data bundle",
            "frame": "ICRF, solar-system barycentre",
            "time_scale": "TDB",
            "orekit_jpype_version": package_version("orekit_jpype"),
            "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "generator": "tools/reference/generate_ephemeris.py",
            "note": (
                "Used to grade packaged JPL DE421 and the ERFA analytic fallback. Independent "
                "JPL releases are not expected to agree to the metre; the point is to measure "
                "and publish each mode's gap rather than infer precision from its model name."
            ),
        },
        "positions": entries,
    }


def main() -> int:
    """Generate the ephemeris reference and write it to disk."""
    document = generate()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {OUTPUT_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

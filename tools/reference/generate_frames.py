"""Generate reference-frame transformation values using Orekit.

Reference frames are the other half of the silent-failure surface that time scales occupy, and
the harder half. Getting a frame wrong does not raise -- it returns a clean, confident number in
the wrong place. Confusing TEME with GCRF at LEO displaces a satellite by ~44 km.

Two conversions matter for the slice-1 tool surface:

* **GCRF -> ITRF** -- inertial to Earth-fixed. Needed for anything expressed as a latitude,
  longitude, or ground station.
* **TEME -> ITRF** -- SGP4 emits TEME, so every TLE-derived answer passes through this.

Run manually; never in CI. Output is committed so tests compare against a fixed target:

    uv run python tools/reference/generate_frames.py

Reference generation writes shared files under `data/` and is NOT safe to run concurrently with
another generator.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import Any

from orekit_setup import REPO_ROOT, initialise, package_version

OUTPUT_PATH = REPO_ROOT / "backend" / "tests" / "reference" / "frames.json"

# Epochs spread across years so differing Earth-orientation data would show up as drift rather
# than a constant offset.
EPOCHS_UTC = [
    "2020-06-15T12:00:00",
    "2024-03-01T06:30:00",
    "2026-01-01T00:00:00",
]

# Test vectors, in metres. Frame errors are angular, so linear error grows with radius: GEO
# amplifies the same misrotation by six times relative to LEO. The off-axis vector ensures all
# three components participate rather than hiding a sign error in a zero.
TEST_VECTORS_M: list[dict[str, Any]] = [
    {"name": "leo_x_axis", "xyz": [7000e3, 0.0, 0.0]},
    {"name": "leo_off_axis", "xyz": [3500e3, -4200e3, 4900e3]},
    {"name": "geo_x_axis", "xyz": [42164e3, 0.0, 0.0]},
]

SOURCE_FRAMES = ("GCRF", "TEME")


def generate() -> dict[str, Any]:
    """Transform each test vector from each source frame into ITRF at each epoch.

    ITRF is built with ``simpleEOP=False`` so the full Earth-orientation model is applied --
    including polar motion, which is the term most often silently omitted and which displaces a
    LEO position by roughly 10 m when missing.

    Returns:
        A reference document with provenance metadata and one entry per (epoch, frame, vector).
    """
    initialise()

    from org.hipparchus.geometry.euclidean.threed import (  # type: ignore[import-not-found]
        Vector3D,
    )
    from org.orekit.frames import FramesFactory  # type: ignore[import-not-found]
    from org.orekit.time import (  # type: ignore[import-not-found]
        AbsoluteDate,
        TimeScalesFactory,
    )
    from org.orekit.utils import IERSConventions  # type: ignore[import-not-found]

    utc = TimeScalesFactory.getUTC()
    itrf = FramesFactory.getITRF(IERSConventions.IERS_2010, False)
    sources = {"GCRF": FramesFactory.getGCRF(), "TEME": FramesFactory.getTEME()}

    entries = []
    for iso in EPOCHS_UTC:
        date = AbsoluteDate(iso, utc)

        for frame_name in SOURCE_FRAMES:
            transform = sources[frame_name].getTransformTo(itrf, date)

            for vector in TEST_VECTORS_M:
                result = transform.transformPosition(Vector3D(*vector["xyz"]))
                entries.append(
                    {
                        "epoch_utc": iso,
                        "source_frame": frame_name,
                        "target_frame": "ITRF",
                        "vector_name": vector["name"],
                        "source_xyz_m": vector["xyz"],
                        "target_xyz_m": [
                            round(float(result.getX()), 6),
                            round(float(result.getY()), 6),
                            round(float(result.getZ()), 6),
                        ],
                    }
                )

    return {
        "_provenance": {
            "source": "orekit",
            "orekit_jpype_version": package_version("orekit_jpype"),
            "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "generator": "tools/reference/generate_frames.py",
            "itrf_convention": "IERS_2010, simpleEOP=False",
            "note": (
                "Independent implementation used as ground truth for astropy frame handling. "
                "Regenerate only deliberately; a changed value here means Orekit, its data "
                "bundle, or the Earth-orientation parameters changed."
            ),
        },
        "transforms": entries,
    }


def main() -> int:
    """Generate the frame reference file and write it to disk.

    Returns:
        Process exit code.
    """
    document = generate()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

    print(f"wrote {OUTPUT_PATH.relative_to(REPO_ROOT)}  ({len(document['transforms'])} entries)")
    for entry in document["transforms"][:3]:
        xyz = entry["target_xyz_m"]
        print(
            f"  {entry['epoch_utc']}  {entry['source_frame']}->ITRF  {entry['vector_name']}"
            f"  [{xyz[0]:.3f}, {xyz[1]:.3f}, {xyz[2]:.3f}]"
        )
    print("  …")
    return 0


if __name__ == "__main__":
    sys.exit(main())

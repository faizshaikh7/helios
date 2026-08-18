"""Tabulate orbit-averaged atmospheric density from Orekit, per solar-activity scenario.

Orbital decay is driven by atmospheric density, and density at satellite altitudes swings by
more than an order of magnitude across the solar cycle. Any lifetime estimate is therefore a
forecast of solar activity as much as of orbital mechanics -- which is exactly why this project
reports a *range* rather than a number.

**Why a committed table rather than a runtime model.** Every Python atmosphere library was
unusable here: `nrlmsise00` is a C extension needing a compiler, `poliastro` pins an
incompatible numpy, and `pyatmos` requires the removed `pkg_resources` and downloads IERS files
at import -- a deployed service must not fetch Earth-orientation data to start. Typing the
constants in from memory was the one option ruled out on principle. So the density comes from
Orekit's NRLMSISE-00, the same independent implementation already trusted elsewhere in this
project, sampled once and committed with provenance.

Solar scenarios are NASA Marshall's published future-estimate strength levels, not invented
multipliers: WEAK, AVERAGE and STRONG bracket the plausible cycle.

Density varies strongly with latitude and local solar time (the diurnal bulge), so each altitude
is averaged over a grid of both, across a full solar cycle. A lifetime integration sees an
orbit-averaged density over years, so that is what the table holds.

Run manually; never in CI:

    uv run python tools/reference/generate_atmosphere.py
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import Any

from orekit_setup import REPO_ROOT, initialise, package_version

OUTPUT_PATH = REPO_ROOT / "backend" / "science" / "data" / "atmosphere.json"

# Altitude grid, km. Dense low down where density changes fastest and where a decaying satellite
# spends its final, rapid phase; coarser high up where lifetimes are dominated by the long tail.
ALTITUDES_KM = [
    120, 130, 140, 150, 160, 180, 200, 220, 250, 280,
    300, 330, 360, 400, 440, 480, 520, 560, 600,
    650, 700, 750, 800, 900, 1000,
]

# Sample points for the orbit average: latitudes spanning the globe and local times spanning the
# diurnal bulge, which alone moves density by a factor of several.
SAMPLE_LATITUDES_DEG = [-60.0, -30.0, 0.0, 30.0, 60.0]
SAMPLE_LONGITUDES_DEG = [0.0, 90.0, 180.0, 270.0]

# Dates spanning a full ~11-year solar cycle, at four points around the year.
#
# Sampling one year would tie the table to whatever cycle phase that year happened to be, and a
# multi-year decay spans the whole cycle: density at these altitudes moves by close to an order
# of magnitude between solar minimum and maximum. Averaging across the cycle is what an orbit
# integrated over years actually experiences.
SAMPLE_DATES = [
    f"{year}-{month}T{hour}:00:00"
    for year in range(2026, 2037)
    for month, hour in (
        ("03-20", "06"),
        ("06-21", "12"),
        ("09-22", "18"),
        ("12-21", "00"),
    )
]

SCENARIOS = ("WEAK", "AVERAGE", "STRONG")


def generate() -> dict[str, Any]:
    """Sample Orekit's NRLMSISE-00 across altitude and solar-activity scenario.

    Returns:
        A table document with provenance and one density series per scenario.
    """
    initialise()

    from org.orekit.bodies import (  # type: ignore[import-not-found]
        CelestialBodyFactory,
        GeodeticPoint,
        OneAxisEllipsoid,
    )
    from org.orekit.frames import FramesFactory  # type: ignore[import-not-found]
    from org.orekit.models.earth.atmosphere import (  # type: ignore[import-not-found]
        NRLMSISE00,
    )
    from org.orekit.models.earth.atmosphere.data import (  # type: ignore[import-not-found]
        MarshallSolarActivityFutureEstimation,
    )
    from org.orekit.time import (  # type: ignore[import-not-found]
        AbsoluteDate,
        TimeScalesFactory,
    )
    from org.orekit.utils import (  # type: ignore[import-not-found]
        Constants,
        IERSConventions,
    )

    utc = TimeScalesFactory.getUTC()
    itrf = FramesFactory.getITRF(IERSConventions.IERS_2010, False)
    earth = OneAxisEllipsoid(
        Constants.WGS84_EARTH_EQUATORIAL_RADIUS,
        Constants.WGS84_EARTH_FLATTENING,
        itrf,
    )
    sun = CelestialBodyFactory.getSun()

    deg = 3.141592653589793 / 180.0
    dates = [AbsoluteDate(iso, utc) for iso in SAMPLE_DATES]

    scenarios: dict[str, list[float]] = {}

    for level_name in SCENARIOS:
        level = getattr(
            MarshallSolarActivityFutureEstimation.StrengthLevel, level_name
        )
        solar = MarshallSolarActivityFutureEstimation(
            MarshallSolarActivityFutureEstimation.DEFAULT_SUPPORTED_NAMES, level
        )
        atmosphere = NRLMSISE00(solar, sun, earth)

        densities: list[float] = []
        for altitude_km in ALTITUDES_KM:
            samples: list[float] = []
            for date in dates:
                for lat in SAMPLE_LATITUDES_DEG:
                    for lon in SAMPLE_LONGITUDES_DEG:
                        point = GeodeticPoint(lat * deg, lon * deg, altitude_km * 1000.0)
                        position = earth.transform(point)
                        samples.append(
                            float(atmosphere.getDensity(date, position, itrf))
                        )

            # Geometric mean, not arithmetic: density is roughly log-normal in altitude and the
            # diurnal bulge spans a factor of several, so an arithmetic mean would be dragged
            # toward the daytime maximum and overstate drag.
            log_sum = sum(_safe_log(value) for value in samples)
            densities.append(_safe_exp(log_sum / len(samples)))

        scenarios[level_name] = densities
        print(f"  {level_name:8} 400 km -> {densities[ALTITUDES_KM.index(400)]:.4e} kg/m^3")

    return {
        "_provenance": {
            "source": "orekit",
            "model": "NRLMSISE-00",
            "solar_activity": "MarshallSolarActivityFutureEstimation (NASA MSFC)",
            "orekit_jpype_version": package_version("orekit_jpype"),
            "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "generator": "tools/reference/generate_atmosphere.py",
            "averaging": (
                "Geometric mean over 5 latitudes x 4 longitudes x 44 dates spanning 2026-2036, so "
                "the diurnal bulge, the seasons, and a full ~11-year solar cycle are all "
                "averaged rather than sampled at one phase."
            ),
            "note": (
                "Committed rather than computed at runtime: no usable Python atmosphere model "
                "installs cleanly, and a deployed service must not download Earth-orientation "
                "data at import. Regenerate deliberately."
            ),
        },
        "altitudes_km": ALTITUDES_KM,
        "density_kg_m3": scenarios,
    }


def _safe_log(value: float) -> float:
    """Natural log with a floor, since density underflows to zero at the top of the grid."""
    import math

    return math.log(max(value, 1e-20))


def _safe_exp(value: float) -> float:
    """Inverse of `_safe_log`."""
    import math

    return math.exp(value)


def main() -> int:
    """Generate the atmosphere table and write it to disk."""
    document = generate()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

    print(f"\nwrote {OUTPUT_PATH.relative_to(REPO_ROOT)}")
    weak = document["density_kg_m3"]["WEAK"]
    strong = document["density_kg_m3"]["STRONG"]
    index = ALTITUDES_KM.index(400)
    print(f"  solar-cycle spread at 400 km: {strong[index] / weak[index]:.1f}x")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Generate orbital-element and derived-orbit reference values using Orekit.

Grades `science.elements` -- the state-vector-to-elements reduction and the apsis altitudes
computed from it -- against Orekit's independent implementation.

Two failure modes make this worth generating separately from `generate_tle_propagation.py`, which
already covers the propagation itself:

* **Element conversion.** A state vector reduces to Keplerian elements through a chain of cross
  products and quadrant-sensitive inverse trigonometry. A sign or quadrant error there leaves the
  state vector untouched and only shows up in RAAN, argument of perigee, or true anomaly.
* **Altitude datum.** An apsis *altitude* is a height above the WGS84 ellipsoid; an apsis *radius*
  is measured from Earth's centre. The two differ by about 6370 km, and a spherical-Earth
  approximation sits about 11 km from the right answer for the ISS. All three are plausible-looking
  numbers, so an independent implementation is the only reliable way to tell them apart.

The **same frozen ISS TLE and the same offsets** as `tests/reference/tle_propagation.json` are
used, so the two reference sets describe the same instants and can be read side by side.

Orekit's own gravitational parameter for the TLE propagator is recorded in the provenance block
rather than assumed: elements depend on mu, so comparing elements derived under two different
values of mu would be comparing two different physical models.

Run manually; never in CI. Output is committed so tests compare against a fixed target:

    uv run python tools/reference/generate_elements.py

Reference generation writes shared files under `data/` and is NOT safe to run concurrently with
another generator.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import UTC, datetime
from typing import Any

from orekit_setup import REPO_ROOT, initialise, package_version

OUTPUT_PATH = REPO_ROOT / "backend" / "tests" / "reference" / "elements.json"

# ISS (ZARYA), NORAD 25544. Identical to tests/reference/tle_propagation.json -- see module
# docstring. Changing it here without changing it there would silently decouple the two references.
TLE_NAME = "ISS (ZARYA)"
TLE_LINE1 = "1 25544U 98067A   24001.50000000  .00016717  00000-0  30150-3 0  9004"
TLE_LINE2 = "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.49815310 10000"

# Offsets from the element-set epoch, in minutes. Zero exercises the epoch itself; the rest
# exercise propagation. Matched to tle_propagation.json.
OFFSETS_MINUTES = [0, 10, 60, 360, 1440]

SECONDS_PER_DAY = 86400.0


def _degrees(radians: float) -> float:
    """Convert an angle to degrees in the range [0, 360).

    Orekit returns angles in radians, some of them negative. Normalising here means the committed
    reference never carries the same angle in two representations, which would otherwise show up
    as a spurious 360-degree disagreement.

    Args:
        radians: Angle in radians.

    Returns:
        The angle in degrees, wrapped into [0, 360).
    """
    return math.degrees(float(radians)) % 360.0


def generate() -> dict[str, Any]:
    """Propagate the fixed TLE with Orekit and record elements and derived orbit properties.

    Elements are taken in **TEME**, the frame the TLE propagator works in and the frame SGP4
    natively produces. Converting to GCRF first would move the inclination and RAAN by the
    precession-nutation angle and turn a frame question into an apparent element error.

    Apsis altitudes are computed by placing a Keplerian orbit at true anomaly 0 and 180 degrees,
    then reducing the resulting position onto a WGS84 ``OneAxisEllipsoid`` expressed in ITRF. That
    is a geodetic height above the ellipsoid surface -- the same datum `science.elements` uses, and
    deliberately not a radius from Earth's centre.

    Returns:
        A reference document with provenance metadata and one entry per offset.
    """
    initialise()

    from org.orekit.bodies import OneAxisEllipsoid  # type: ignore[import-not-found]
    from org.orekit.frames import FramesFactory  # type: ignore[import-not-found]
    from org.orekit.orbits import (  # type: ignore[import-not-found]
        KeplerianOrbit,
        PositionAngleType,
    )
    from org.orekit.propagation.analytical.tle import (  # type: ignore[import-not-found]
        TLE,
        TLEPropagator,
    )
    from org.orekit.time import TimeScalesFactory  # type: ignore[import-not-found]
    from org.orekit.utils import (  # type: ignore[import-not-found]
        Constants,
        IERSConventions,
    )

    utc = TimeScalesFactory.getUTC()
    tle = TLE(TLE_LINE1, TLE_LINE2)
    propagator = TLEPropagator.selectExtrapolator(tle)

    epoch = tle.getDate()
    teme = propagator.getFrame()

    # simpleEOP=False applies the full Earth-orientation model, including polar motion. It is the
    # term most often silently omitted, and the same choice tests/reference/frames.json was
    # generated with.
    itrf = FramesFactory.getITRF(IERSConventions.IERS_2010, False)
    earth = OneAxisEllipsoid(
        Constants.WGS84_EARTH_EQUATORIAL_RADIUS, Constants.WGS84_EARTH_FLATTENING, itrf
    )

    mu = float(propagator.propagate(epoch).getOrbit().getMu())

    entries = []
    for minutes in OFFSETS_MINUTES:
        date = epoch.shiftedBy(float(minutes * 60))
        state = propagator.propagate(date)

        pv = state.getPVCoordinates(teme)
        position = pv.getPosition()
        velocity = pv.getVelocity()

        keplerian = KeplerianOrbit(state.getOrbit())

        semi_major_axis_m = float(keplerian.getA())
        eccentricity = float(keplerian.getE())
        inclination = float(keplerian.getI())
        perigee_argument = float(keplerian.getPerigeeArgument())
        raan = float(keplerian.getRightAscensionOfAscendingNode())

        altitudes_m = []
        for true_anomaly in (0.0, math.pi):
            apsis = KeplerianOrbit(
                semi_major_axis_m,
                eccentricity,
                inclination,
                perigee_argument,
                raan,
                true_anomaly,
                PositionAngleType.TRUE,
                teme,
                date,
                mu,
            )
            apsis_point = apsis.getPVCoordinates().getPosition()
            altitudes_m.append(float(earth.transform(apsis_point, teme, date).getAltitude()))

        perigee_altitude_m, apogee_altitude_m = altitudes_m

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
                "semi_major_axis_m": round(semi_major_axis_m, 6),
                "eccentricity": round(eccentricity, 12),
                "inclination_deg": round(_degrees(inclination), 9),
                "raan_deg": round(_degrees(raan), 9),
                "argument_of_perigee_deg": round(_degrees(perigee_argument), 9),
                "true_anomaly_deg": round(_degrees(keplerian.getTrueAnomaly()), 9),
                "orbital_period_s": round(float(keplerian.getKeplerianPeriod()), 6),
                "mean_motion_rev_per_day": round(
                    float(keplerian.getKeplerianMeanMotion()) * SECONDS_PER_DAY / (2.0 * math.pi),
                    9,
                ),
                "perigee_radius_m": round(semi_major_axis_m * (1.0 - eccentricity), 6),
                "apogee_radius_m": round(semi_major_axis_m * (1.0 + eccentricity), 6),
                "perigee_altitude_m": round(perigee_altitude_m, 6),
                "apogee_altitude_m": round(apogee_altitude_m, 6),
            }
        )

    return {
        "_provenance": {
            "source": "orekit",
            "orekit_jpype_version": package_version("orekit_jpype"),
            "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "generator": "tools/reference/generate_elements.py",
            "frame": "TEME",
            "time_scale": "UTC",
            "element_type": "osculating",
            "mu_m3_s2": mu,
            "altitude_datum": (
                "geodetic height above the WGS84 ellipsoid (OneAxisEllipsoid in ITRF, "
                "IERS_2010, simpleEOP=False) -- NOT a radius from Earth's centre"
            ),
            "note": (
                "Independent implementation used as ground truth for the skyfield-based element "
                "reduction in science/elements.py. Elements are osculating, not the TLE's own "
                "mean elements, and depend on mu -- which is recorded above so a comparison can "
                "confirm both sides used the same gravitational parameter."
            ),
        },
        "tle": {"name": TLE_NAME, "line1": TLE_LINE1, "line2": TLE_LINE2},
        "states": entries,
    }


def main() -> int:
    """Generate the element reference file and write it to disk.

    Returns:
        Process exit code.
    """
    document = generate()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

    print(f"wrote {OUTPUT_PATH.relative_to(REPO_ROOT)}  ({len(document['states'])} states)")
    print(f"  mu = {document['_provenance']['mu_m3_s2']:.6e} m^3/s^2")
    for entry in document["states"]:
        print(
            f"  +{entry['offset_minutes']:>5} min  a={entry['semi_major_axis_m'] / 1e3:9.3f} km  "
            f"e={entry['eccentricity']:.7f}  i={entry['inclination_deg']:7.3f} deg  "
            f"peri_alt={entry['perigee_altitude_m'] / 1e3:7.3f} km  "
            f"apo_alt={entry['apogee_altitude_m'] / 1e3:7.3f} km"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

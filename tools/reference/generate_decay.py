"""Generate decay reference values by numerically propagating with drag in Orekit.

`science/decay.py` integrates a simplified circular-orbit model against a tabulated,
orbit-averaged atmosphere. This grades that simplification against a full numerical propagation:
a real force model, a real atmosphere evaluated at the satellite's actual position each step, and
no circular-orbit assumption.

The two will not agree closely, and that is the point. The simplified model exists to be fast
enough to answer interactively; this reference establishes *how far off* it is, so the tolerance
in the test is a measured figure rather than a hopeful one, and so the error can be stated to a
user instead of hidden.

Low altitudes only. Decay from 600 km takes decades of simulated time, which is hours of
numerical propagation; 250-350 km decays in weeks and gives the same comparison for minutes of
compute.

Run manually; never in CI:

    uv run python tools/reference/generate_decay.py
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import Any

from orekit_setup import REPO_ROOT, initialise, package_version

OUTPUT_PATH = REPO_ROOT / "backend" / "tests" / "reference" / "decay.json"

# Starting altitudes, km. Chosen so each decays within weeks of simulated time.
START_ALTITUDES_KM = [250.0, 300.0, 350.0]

# A 3U cubesat: Cd 2.2, 0.03 m^2 cross-section, 3.3 kg -> Cd*A/m = 0.02 m^2/kg.
#
# The mass MUST be set on the spacecraft state. Orekit defaults to 1000 kg, which silently
# makes the ballistic term 300x too small and produces a satellite that never decays -- the
# first run of this script reported all three cases still in orbit after 400 days.
DRAG_AREA_M2 = 0.03
DRAG_COEFFICIENT = 2.2
MASS_KG = 3.3
BALLISTIC_TERM = DRAG_COEFFICIENT * DRAG_AREA_M2 / MASS_KG

# Reentry threshold, matching science/decay.py so the two measure the same thing.
REENTRY_ALTITUDE_KM = 120.0

# Cap on simulated time, so a case that decays slower than expected fails loudly rather than
# running forever.
MAX_SIMULATED_DAYS = 400.0


def generate() -> dict[str, Any]:
    """Propagate each case numerically until it falls to the reentry altitude.

    Returns:
        A reference document with provenance and one entry per starting altitude.
    """
    initialise()

    from org.hipparchus.ode.nonstiff import (  # type: ignore[import-not-found]
        DormandPrince853Integrator,
    )
    from org.orekit.attitudes import LofOffset  # type: ignore[import-not-found]
    from org.orekit.bodies import (  # type: ignore[import-not-found]
        CelestialBodyFactory,
        OneAxisEllipsoid,
    )
    from org.orekit.forces.drag import (  # type: ignore[import-not-found]
        DragForce,
        IsotropicDrag,
    )
    from org.orekit.forces.gravity import (  # type: ignore[import-not-found]
        HolmesFeatherstoneAttractionModel,
    )
    from org.orekit.forces.gravity.potential import (  # type: ignore[import-not-found]
        GravityFieldFactory,
    )
    from org.orekit.frames import (  # type: ignore[import-not-found]
        FramesFactory,
        LOFType,
    )
    from org.orekit.models.earth.atmosphere import (
        NRLMSISE00,  # type: ignore[import-not-found]
    )
    from org.orekit.models.earth.atmosphere.data import (  # type: ignore[import-not-found]
        MarshallSolarActivityFutureEstimation,
    )
    from org.orekit.orbits import (  # type: ignore[import-not-found]
        CartesianOrbit,
        KeplerianOrbit,
        OrbitType,
        PositionAngleType,
    )
    from org.orekit.propagation import SpacecraftState  # type: ignore[import-not-found]
    from org.orekit.propagation.events import (  # type: ignore[import-not-found]
        AltitudeDetector,
    )
    from org.orekit.propagation.events.handlers import (  # type: ignore[import-not-found]
        StopOnEvent,
    )
    from org.orekit.propagation.numerical import (  # type: ignore[import-not-found]
        NumericalPropagator,
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
    inertial = FramesFactory.getEME2000()
    itrf = FramesFactory.getITRF(IERSConventions.IERS_2010, False)
    earth = OneAxisEllipsoid(
        Constants.WGS84_EARTH_EQUATORIAL_RADIUS,
        Constants.WGS84_EARTH_FLATTENING,
        itrf,
    )
    sun = CelestialBodyFactory.getSun()

    solar = MarshallSolarActivityFutureEstimation(
        MarshallSolarActivityFutureEstimation.DEFAULT_SUPPORTED_NAMES,
        MarshallSolarActivityFutureEstimation.StrengthLevel.AVERAGE,
    )
    atmosphere = NRLMSISE00(solar, sun, earth)

    start = AbsoluteDate("2026-01-01T00:00:00", utc)
    reentry_radius = Constants.WGS84_EARTH_EQUATORIAL_RADIUS + REENTRY_ALTITUDE_KM * 1000.0

    entries = []
    for altitude_km in START_ALTITUDES_KM:
        radius = Constants.WGS84_EARTH_EQUATORIAL_RADIUS + altitude_km * 1000.0

        orbit = KeplerianOrbit(
            radius, 1.0e-4, 0.9, 0.0, 0.0, 0.0,
            PositionAngleType.TRUE, inertial, start, Constants.WGS84_EARTH_MU,
        )

        integrator = DormandPrince853Integrator(1.0, 300.0, 1.0e-3, 1.0e-3)
        propagator = NumericalPropagator(integrator)
        propagator.setOrbitType(OrbitType.CARTESIAN)
        propagator.setInitialState(SpacecraftState(CartesianOrbit(orbit)).withMass(MASS_KG))
        propagator.setAttitudeProvider(LofOffset(inertial, LOFType.LVLH))

        # Gravity to a modest degree: enough for a realistic orbit without the cost of a full
        # field, since the answer is dominated by drag rather than by geopotential detail.
        propagator.addForceModel(
            HolmesFeatherstoneAttractionModel(
                itrf, GravityFieldFactory.getNormalizedProvider(8, 8)
            )
        )
        propagator.addForceModel(
            DragForce(atmosphere, IsotropicDrag(DRAG_AREA_M2, DRAG_COEFFICIENT))
        )

        # Stop exactly at the reentry altitude with an event detector. Stepping day by day and
        # checking afterwards lets the propagator continue past the surface within a step, at
        # which point the orbit goes hyperbolic and the solver fails to converge - which is
        # precisely how the first version of this script died.
        detector = (
            AltitudeDetector(REENTRY_ALTITUDE_KM * 1000.0, earth)
            .withMaxCheck(60.0)
            .withThreshold(1.0e-3)
            .withHandler(StopOnEvent())
        )
        propagator.addEventDetector(detector)

        final = propagator.propagate(start.shiftedBy(MAX_SIMULATED_DAYS * 86400.0))
        elapsed_days = float(final.getDate().durationFrom(start)) / 86400.0
        decayed = elapsed_days < MAX_SIMULATED_DAYS - 1.0

        entries.append(
            {
                "start_altitude_km": altitude_km,
                "ballistic_term_m2_per_kg": BALLISTIC_TERM,
                "lifetime_days": round(elapsed_days, 2) if decayed else None,
                "lifetime_years": round(elapsed_days / 365.25, 4) if decayed else None,
                "decayed": decayed,
            }
        )
        print(
            f"  {altitude_km:.0f} km -> "
            + (f"{elapsed_days:.0f} days ({elapsed_days / 365.25:.3f} yr)" if decayed
               else f"still in orbit after {MAX_SIMULATED_DAYS:.0f} days")
        )

    return {
        "_provenance": {
            "source": "orekit",
            "method": "NumericalPropagator, DormandPrince853, 8x8 gravity + NRLMSISE-00 drag",
            "solar_activity": "NASA MSFC AVERAGE",
            "orekit_jpype_version": package_version("orekit_jpype"),
            "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "generator": "tools/reference/generate_decay.py",
            "note": (
                "Full numerical propagation with a real force model, used to measure how far "
                "the simplified circular-orbit model in science/decay.py departs from it. The "
                "two are not expected to agree closely; the point is to know the size of the "
                "gap and state it."
            ),
        },
        "cases": entries,
    }


def main() -> int:
    """Generate the decay reference and write it to disk."""
    document = generate()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {OUTPUT_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

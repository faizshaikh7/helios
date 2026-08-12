"""Generate eclipse and beta-angle reference values using Orekit.

Eclipse geometry is where three separate silent-failure surfaces meet: the propagator, the
reference frame, and a solar ephemeris. None of them raises when it is wrong. A satellite whose
Sun vector is a degree off still produces a clean, plausible eclipse duration -- one that is
simply not the duration the battery will actually see.

Orekit grades `science.eclipse` on two things:

* **Beta angle** -- the orbit-plane-to-Sun angle. It is exquisitely sensitive to frame handling:
  confusing TEME with GCRF tilts the orbit plane by roughly a third of a degree at a 2024 epoch,
  which is two orders of magnitude larger than any legitimate difference between the two
  implementations.
* **Umbra and penumbra boundary times** -- the quantity a power budget is actually written in.

**The shadow model is pinned to match `science.eclipse` exactly**: a spherical Earth of the IERS
2010 equatorial radius (flattening zero) and the IAU 2015 nominal solar radius, both hard-coded
below. That is deliberate. A differential test is only informative if the two sides model the
same physical situation; if Orekit used an oblate Earth and skyfield a sphere, the test would
measure a modelling choice that was made on purpose rather than an implementation defect. The
values used are written into the reference file so `tests/test_eclipse.py` can assert the science
module still agrees with them -- otherwise a future edit to either constant would silently turn
this into a comparison of two different problems.

Setting the flattening to zero has a second benefit: a sphere has no orientation, so Earth
orientation parameters drop out of the comparison entirely and a failure here can never be blamed
on EOP drift. Frame handling is still exercised, because the beta angle and the Sun vector are
both expressed in GCRF.

A **fixed, hard-coded TLE** is used -- the same ISS element set as
`generate_tle_propagation.py` -- so the reference is reproducible forever and never depends on
what Celestrak happens to be serving.

Run manually; never in CI. Output is committed so tests compare against a fixed target:

    uv run python tools/reference/generate_eclipse.py

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

OUTPUT_PATH = REPO_ROOT / "backend" / "tests" / "reference" / "eclipse.json"

# ISS (ZARYA), NORAD 25544. Frozen deliberately -- see module docstring.
TLE_NAME = "ISS (ZARYA)"
TLE_LINE1 = "1 25544U 98067A   24001.50000000  .00016717  00000-0  30150-3 0  9004"
TLE_LINE2 = "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.49815310 10000"

# Shadow model, pinned to science.eclipse. See module docstring for why these are duplicated
# rather than read from Orekit's own Constants class.
#   Earth: IERS 2010 equatorial radius, the value skyfield exposes as `skyfield.constants.ERAD`.
#   Sun:   IAU 2015 Resolution B3 nominal solar radius, the value astropy exposes as
#          `astropy.constants.R_sun`.
EARTH_RADIUS_M = 6378136.6
EARTH_FLATTENING = 0.0
SUN_RADIUS_M = 695700000.0

# One day covers about sixteen ISS revolutions, so every eclipse in the file is a repeat
# measurement of the same geometry under a slowly rotating orbit plane. That is enough to catch a
# systematic offset without making the committed reference unreadable.
SPAN_DAYS = 1.0

# Beta angle sampled every six hours. Beta drifts a few degrees a day as the node regresses, so
# these five samples also record the drift rate -- a test that only checked one instant could
# pass with a static Sun vector.
BETA_SAMPLE_OFFSETS_MINUTES = [0, 360, 720, 1080, 1440]

# Event search settings. The shortest feature being resolved is the penumbra, about twelve
# seconds at low Earth orbit, and each detector's switching function crosses zero only twice per
# revolution, so a one-minute coarse check is ample. The threshold is the convergence tolerance
# on an event date; a microsecond is far finer than the seconds-level tolerance the test applies.
MAX_CHECK_S = 60.0
THRESHOLD_S = 1.0e-6


def _iso(date: Any, utc: Any) -> str:
    """Format an Orekit date as an ISO-8601 UTC string.

    Args:
        date: An ``AbsoluteDate``.
        utc: Orekit's UTC time scale.

    Returns:
        The date rendered in UTC, matching the format used by the other reference files.
    """
    return date.toString(utc)


def _shadow_events(tle: Any, sun: Any, earth: Any, span: tuple[Any, Any], *, umbra: bool) -> list:
    """Propagate the element set once and log every crossing of one shadow boundary.

    Umbra and penumbra are found in two separate propagations rather than one propagation with
    two detectors, so an event can never be attributed to the wrong detector -- the failure mode
    would be a reference file that looks perfectly well-formed while pairing an umbra entry with
    a penumbra exit.

    Args:
        tle: An Orekit ``TLE``.
        sun: The Sun as a position provider.
        earth: The occulting body.
        span: ``(start, end)`` as ``AbsoluteDate`` objects.
        umbra: Detect the total-shadow boundary if True, the partial-shadow boundary if False.

    Returns:
        Logged events in chronological order.
    """
    from org.orekit.propagation.analytical.tle import (  # type: ignore[import-not-found]
        TLEPropagator,
    )
    from org.orekit.propagation.events import (  # type: ignore[import-not-found]
        EclipseDetector,
        EventsLogger,
    )
    from org.orekit.propagation.events.handlers import (  # type: ignore[import-not-found]
        ContinueOnEvent,
    )

    detector = EclipseDetector(sun, SUN_RADIUS_M, earth)
    detector = detector.withUmbra() if umbra else detector.withPenumbra()
    detector = (
        detector.withMaxCheck(MAX_CHECK_S)
        .withThreshold(THRESHOLD_S)
        .withHandler(ContinueOnEvent())
    )

    propagator = TLEPropagator.selectExtrapolator(tle)
    logger = EventsLogger()
    propagator.addEventDetector(logger.monitorDetector(detector))
    propagator.propagate(span[0], span[1])

    return list(logger.getLoggedEvents())


def _complete_intervals(events: list) -> list[tuple[Any, Any]]:
    """Pair logged events into complete shadow intervals.

    Orekit's eclipse switching function is positive in sunlight and negative in shadow, so a
    decreasing crossing is an entry and an increasing one is an exit. Intervals still open at
    either end of the span are dropped, matching `science.eclipse`, which omits an eclipse whose
    entry or exit falls outside the search window rather than reporting a missing boundary.

    The polarity assumption is not taken on trust: `main` re-evaluates the occultation geometry
    at the midpoint of every interval produced here and refuses to write the file if the
    satellite is not actually in shadow there.

    Args:
        events: Logged events from one detector, in chronological order.

    Returns:
        Complete ``(entry, exit)`` pairs of ``AbsoluteDate`` objects.
    """
    intervals: list[tuple[Any, Any]] = []
    entry = None

    for event in events:
        if not event.isIncreasing():
            entry = event.getDate()
        elif entry is not None:
            intervals.append((entry, event.getDate()))
            entry = None

    return intervals


def _assert_in_shadow(
    engine: Any, propagator: Any, interval: tuple[Any, Any], *, umbra: bool
) -> None:
    """Verify that the midpoint of a computed interval really is in shadow.

    This is the guard that makes the generator safe to trust. It re-derives the occultation
    geometry from Orekit's own ``OccultationEngine`` -- angular separation of the two discs
    against their apparent radii -- instead of assuming the sign convention of the detector's
    switching function. If that convention were ever inverted, the intervals produced would be
    the *sunlit* arcs, which are perfectly plausible-looking numbers; this check rejects them.

    Args:
        engine: An ``OccultationEngine`` for the same Sun and Earth.
        propagator: A propagator for the same element set.
        interval: The ``(entry, exit)`` pair to check.
        umbra: Whether the interval is meant to be total shadow.

    Raises:
        AssertionError: If the midpoint is not inside the expected shadow region.
    """
    entry, exit_ = interval
    duration = exit_.durationFrom(entry)
    assert duration > 0.0, f"non-positive interval duration {duration} s"

    angles = engine.angles(propagator.propagate(entry.shiftedBy(duration / 2.0)))
    separation = float(angles.getSeparation())
    limb = float(angles.getLimbRadius())
    occulted = float(angles.getOccultedApparentRadius())

    limit = limb - occulted if umbra else limb + occulted
    region = "umbra" if umbra else "penumbra"
    assert separation < limit, (
        f"midpoint of a supposed {region} interval starting {entry} is not in shadow: "
        f"separation {separation} rad, limit {limit} rad. The detector's sign convention is not "
        f"what this generator assumes -- fix _complete_intervals rather than this check."
    )


def generate() -> dict[str, Any]:
    """Compute beta angles and shadow boundary times for the fixed element set.

    Returns:
        A reference document with provenance metadata, the pinned shadow model, beta-angle
        samples, and one entry per complete eclipse.
    """
    initialise()

    from org.hipparchus.geometry.euclidean.threed import (  # type: ignore[import-not-found]
        Vector3D,
    )
    from org.orekit.bodies import (  # type: ignore[import-not-found]
        CelestialBodyFactory,
        OneAxisEllipsoid,
    )
    from org.orekit.frames import FramesFactory  # type: ignore[import-not-found]
    from org.orekit.propagation.analytical.tle import (  # type: ignore[import-not-found]
        TLE,
        TLEPropagator,
    )
    from org.orekit.time import TimeScalesFactory  # type: ignore[import-not-found]
    from org.orekit.utils import (  # type: ignore[import-not-found]
        IERSConventions,
        OccultationEngine,
    )

    utc = TimeScalesFactory.getUTC()
    gcrf = FramesFactory.getGCRF()
    itrf = FramesFactory.getITRF(IERSConventions.IERS_2010, False)

    sun = CelestialBodyFactory.getSun()
    earth = OneAxisEllipsoid(EARTH_RADIUS_M, EARTH_FLATTENING, itrf)

    tle = TLE(TLE_LINE1, TLE_LINE2)
    epoch = tle.getDate()
    end = epoch.shiftedBy(SPAN_DAYS * 86400.0)

    propagator = TLEPropagator.selectExtrapolator(tle)
    period_s = 2.0 * math.pi / float(tle.getMeanMotion())

    # Beta angle: the angle between the orbit plane and the Sun, measured in GCRF so that the
    # orbit normal and the Sun vector are expressed in one and the same inertial frame.
    beta_samples = []
    for minutes in BETA_SAMPLE_OFFSETS_MINUTES:
        date = epoch.shiftedBy(float(minutes * 60))
        pv = propagator.propagate(date).getPVCoordinates(gcrf)

        normal = Vector3D.crossProduct(pv.getPosition(), pv.getVelocity()).normalize()
        sun_position = sun.getPosition(date, gcrf)
        sun_direction = sun_position.normalize()

        beta_rad = math.asin(max(-1.0, min(1.0, float(Vector3D.dotProduct(normal, sun_direction)))))

        beta_samples.append(
            {
                "offset_minutes": minutes,
                "epoch_utc": _iso(date, utc),
                "beta_deg": round(math.degrees(beta_rad), 9),
                "sun_gcrf_m": [
                    round(float(sun_position.getX()), 3),
                    round(float(sun_position.getY()), 3),
                    round(float(sun_position.getZ()), 3),
                ],
            }
        )

    # Shadow boundaries, umbra and penumbra found independently and then nested.
    engine = OccultationEngine(sun, SUN_RADIUS_M, earth)
    span = (epoch, end)

    penumbra = _complete_intervals(_shadow_events(tle, sun, earth, span, umbra=False))
    umbra = _complete_intervals(_shadow_events(tle, sun, earth, span, umbra=True))

    for interval in penumbra:
        _assert_in_shadow(engine, propagator, interval, umbra=False)
    for interval in umbra:
        _assert_in_shadow(engine, propagator, interval, umbra=True)

    eclipses = []
    for entry, exit_ in penumbra:
        nested = [
            pair
            for pair in umbra
            if pair[0].durationFrom(entry) >= 0.0 and exit_.durationFrom(pair[0]) >= 0.0
        ]
        duration_s = float(exit_.durationFrom(entry))
        umbra_duration_s = float(nested[0][1].durationFrom(nested[0][0])) if nested else 0.0

        eclipses.append(
            {
                "entry_utc": _iso(entry, utc),
                "exit_utc": _iso(exit_, utc),
                "umbra_entry_utc": _iso(nested[0][0], utc) if nested else None,
                "umbra_exit_utc": _iso(nested[0][1], utc) if nested else None,
                "duration_s": round(duration_s, 6),
                "umbra_duration_s": round(umbra_duration_s, 6),
                "orbit_fraction": round(duration_s / period_s, 9),
            }
        )

    return {
        "_provenance": {
            "source": "orekit",
            "orekit_jpype_version": package_version("orekit_jpype"),
            "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "generator": "tools/reference/generate_eclipse.py",
            "frame": "GCRF",
            "shadow_model": "conical (Orekit EclipseDetector, umbra and penumbra separately)",
            "note": (
                "Independent implementation used as ground truth for science.eclipse, which "
                "builds on skyfield for the orbit and astropy's built-in ERFA ephemeris for the "
                "Sun. The two do not share a solar ephemeris, a propagator implementation, or a "
                "frame stack. Orekit reports the Sun's geometric position while astropy's "
                "get_sun reports its apparent position, a known ~21 arcsecond difference that "
                "the test tolerances account for explicitly."
            ),
        },
        "tle": {"name": TLE_NAME, "line1": TLE_LINE1, "line2": TLE_LINE2},
        "model": {
            "earth_radius_m": EARTH_RADIUS_M,
            "earth_flattening": EARTH_FLATTENING,
            "sun_radius_m": SUN_RADIUS_M,
            "orbital_period_s": round(period_s, 6),
            "note": (
                "Pinned to match science.eclipse so the differential test measures "
                "implementation, not modelling choice. A spherical Earth also removes Earth "
                "orientation parameters from the comparison entirely."
            ),
        },
        "span": {
            "start_utc": _iso(epoch, utc),
            "days": SPAN_DAYS,
            "max_check_s": MAX_CHECK_S,
            "threshold_s": THRESHOLD_S,
        },
        "beta_angles": beta_samples,
        "eclipses": eclipses,
    }


def main() -> int:
    """Generate the eclipse reference file and write it to disk.

    Returns:
        Process exit code.
    """
    document = generate()

    if not document["eclipses"]:
        print("no complete eclipses found -- refusing to write an empty reference", file=sys.stderr)
        return 1

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

    print(
        f"wrote {OUTPUT_PATH.relative_to(REPO_ROOT)}  "
        f"({len(document['beta_angles'])} beta samples, {len(document['eclipses'])} eclipses)"
    )
    for sample in document["beta_angles"]:
        print(f"  +{sample['offset_minutes']:>5} min  beta {sample['beta_deg']:9.4f} deg")
    for entry in document["eclipses"][:3]:
        print(
            f"  {entry['entry_utc']} -> {entry['exit_utc']}  "
            f"{entry['duration_s']:.3f} s total, {entry['umbra_duration_s']:.3f} s umbra"
        )
    print("  …")
    return 0


if __name__ == "__main__":
    sys.exit(main())

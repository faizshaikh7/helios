"""Generate the evaluation question set, with ground truth computed by Orekit.

This produces the dataset behind the project's headline artifact: accuracy of a tool-grounded
system against a bare frontier model. Everything about its design follows from one requirement --
**the questions must be ones a language model cannot answer from memory.**

That constraint is easy to get wrong. "What is the ISS's altitude?" is in every model's training
data; a bare model answers "about 400 km" and scores a hit, and the chart shows no difference
between a grounded system and a guess. Such questions measure recall, not grounding.

So the set is weighted toward quantities that are *unmemorizable by construction*:

* the sub-satellite point at a specific timestamp -- changes every second, never published
* pass counts and geometry for a specific station over a specific window
* range and elevation at a specific instant

A minority of memorizable questions (period, inclination) are kept deliberately as a **control**.
A grounded system should score near-perfectly on both; a bare model should score well on the
control and collapse on the rest. If the bare model also does well on the unmemorizable set,
something is wrong with the harness, not with the model.

Ground truth comes from Orekit -- an independent implementation, never from the code being
graded. See .agent/test.md.

Run manually; never in CI. Not safe to run concurrently with another Orekit generator:

    uv run python tools/eval/generate_questions.py
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools" / "reference"))

from orekit_setup import initialise, package_version

FIXTURES_PATH = REPO_ROOT / "eval" / "tle_fixtures.json"
OUTPUT_PATH = REPO_ROOT / "eval" / "questions.json"

# Fixed evaluation epochs. Absolute and frozen so every regeneration asks about the same
# instants -- a moving "now" would make two runs of the chart incomparable.
EVAL_EPOCHS = [
    "2026-08-12T00:00:00",
    "2026-08-12T06:30:00",
    "2026-08-13T14:45:00",
    "2026-08-14T21:10:00",
]

# Stations spread across latitudes and hemispheres.
STATIONS = [
    {"name": "Bangalore", "lat": 12.9716, "lon": 77.5946, "alt_m": 920.0},
    {"name": "London", "lat": 51.5072, "lon": -0.1276, "alt_m": 11.0},
    {"name": "Quito", "lat": -0.1807, "lon": -78.4678, "alt_m": 2850.0},
    {"name": "Sydney", "lat": -33.8688, "lon": 151.2093, "alt_m": 58.0},
]

# Tolerances per category, and why each is what it is.
#
# These define "acceptably correct", not "identical". They must be loose enough that a correct
# method using slightly different Earth-orientation data still passes, and tight enough that a
# plausible guess fails. A degree of latitude is ~111 km, so 0.5 deg is a real constraint on a
# position nobody can recall.
TOLERANCES: dict[str, dict[str, Any]] = {
    "orbital_period": {"abs": 0.5, "unit": "minutes"},
    # 0.1 deg, not tighter, because "the inclination" is genuinely ambiguous: a TLE publishes
    # Brouwer MEAN elements, while anything derived from a propagated state vector is OSCULATING.
    # The two differ legitimately -- by ~0.055 deg for the eccentric orbits in this set -- so a
    # tighter tolerance would score a correct method wrong over an unstated convention rather
    # than over an error. The question text names the convention; this absorbs the difference.
    "inclination": {"abs": 0.1, "unit": "deg"},
    "subpoint_latitude": {"abs": 0.5, "unit": "deg"},
    "subpoint_longitude": {"abs": 0.5, "unit": "deg"},
    "altitude": {"abs": 10.0, "unit": "km"},
    "speed": {"abs": 0.05, "unit": "km/s"},
    "pass_count": {"abs": 0, "unit": "passes"},
    "max_elevation": {"abs": 2.0, "unit": "deg"},
}

# Regimes where ground-station passes are a meaningful question. A geostationary satellite is
# either permanently visible or permanently not, so "how many passes" is not a real question.
PASS_REGIMES = {"LEO", "LEO low-inclination", "sun-synchronous"}


def _load_fixtures() -> list[dict[str, Any]]:
    """Load the frozen element sets.

    Returns:
        The fixture satellites.

    Raises:
        FileNotFoundError: If fixtures have not been fetched.
    """
    if not FIXTURES_PATH.exists():
        raise FileNotFoundError(
            f"{FIXTURES_PATH} missing - run `uv run python tools/eval/fetch_fixtures.py` first"
        )
    return json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))["satellites"]


def generate() -> dict[str, Any]:
    """Build the question set with Orekit-computed answers.

    Returns:
        A dataset document with provenance metadata and one entry per question.
    """
    initialise()

    from org.orekit.bodies import (  # type: ignore[import-not-found]
        GeodeticPoint,
        OneAxisEllipsoid,
    )
    from org.orekit.frames import (  # type: ignore[import-not-found]
        FramesFactory,
        TopocentricFrame,
    )
    from org.orekit.propagation.analytical.tle import (  # type: ignore[import-not-found]
        TLE,
        TLEPropagator,
    )
    from org.orekit.propagation.events import (  # type: ignore[import-not-found]
        ElevationDetector,
        EventsLogger,
    )
    from org.orekit.propagation.events.handlers import (  # type: ignore[import-not-found]
        ContinueOnEvent,
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

    questions: list[dict[str, Any]] = []

    def add(
        category: str,
        prompt: str,
        answer: float,
        unit: str,
        context: dict[str, Any],
        memorizable: bool,
    ) -> None:
        """Append a question with its ground-truth answer."""
        tolerance = TOLERANCES[category]
        questions.append(
            {
                "id": f"{category}-{len(questions):04d}",
                "category": category,
                "question": prompt,
                "answer": {
                    "value": round(float(answer), 6),
                    "unit": unit,
                    "tolerance_abs": tolerance["abs"],
                },
                "memorizable": memorizable,
                "context": context,
                "ground_truth_source": "orekit",
            }
        )

    for fixture in _load_fixtures():
        name = fixture["name"]
        norad = fixture["norad_id"]
        regime = fixture["regime"]

        tle = TLE(fixture["line1"], fixture["line2"])
        propagator = TLEPropagator.selectExtrapolator(tle)

        # --- Control questions: publicly known, plausibly memorizable ---------------------
        mean_motion_rev_per_day = tle.getMeanMotion() * 86400.0 / (2.0 * 3.141592653589793)
        add(
            "orbital_period",
            f"What is the orbital period of {name} (NORAD {norad}), in minutes?",
            1440.0 / mean_motion_rev_per_day,
            "minutes",
            {"norad_id": norad, "regime": regime},
            memorizable=True,
        )
        add(
            "inclination",
            f"What is the orbital inclination of {name} (NORAD {norad}), in degrees? "
            f"Mean or osculating inclination are both acceptable; they differ by well under a "
            f"tenth of a degree.",
            float(tle.getI()) * 180.0 / 3.141592653589793,
            "deg",
            {"norad_id": norad, "regime": regime},
            memorizable=True,
        )

        # --- Unmemorizable: state at a specific instant -----------------------------------
        for epoch_iso in EVAL_EPOCHS:
            date = AbsoluteDate(epoch_iso, utc)
            state = propagator.propagate(date)
            pv_itrf = state.getPVCoordinates(itrf)
            geodetic = earth.transform(pv_itrf.getPosition(), itrf, date)

            context = {"norad_id": norad, "regime": regime, "epoch_utc": epoch_iso}
            rad_to_deg = 180.0 / 3.141592653589793

            add(
                "subpoint_latitude",
                f"At {epoch_iso}Z, what is the sub-satellite latitude of {name} "
                f"(NORAD {norad}), in degrees north?",
                float(geodetic.getLatitude()) * rad_to_deg,
                "deg",
                context,
                memorizable=False,
            )
            add(
                "subpoint_longitude",
                f"At {epoch_iso}Z, what is the sub-satellite longitude of {name} "
                f"(NORAD {norad}), in degrees east?",
                float(geodetic.getLongitude()) * rad_to_deg,
                "deg",
                context,
                memorizable=False,
            )
            add(
                "altitude",
                f"At {epoch_iso}Z, what is the altitude of {name} (NORAD {norad}) above the "
                f"WGS84 ellipsoid, in kilometres?",
                float(geodetic.getAltitude()) / 1000.0,
                "km",
                context,
                memorizable=False,
            )
            add(
                "speed",
                f"At {epoch_iso}Z, what is the inertial speed of {name} (NORAD {norad}), "
                f"in kilometres per second?",
                float(state.getPVCoordinates().getVelocity().getNorm()) / 1000.0,
                "km/s",
                context,
                memorizable=False,
            )

        # --- Unmemorizable: station-specific access ---------------------------------------
        if regime not in PASS_REGIMES:
            continue

        for station in STATIONS:
            deg_to_rad = 3.141592653589793 / 180.0
            point = GeodeticPoint(
                station["lat"] * deg_to_rad,
                station["lon"] * deg_to_rad,
                station["alt_m"],
            )
            topo = TopocentricFrame(earth, point, station["name"])

            start = AbsoluteDate(EVAL_EPOCHS[0], utc)
            end = start.shiftedBy(3 * 86400.0)

            # maxCheck MUST be shorter than the shortest event being detected. Orekit's default
            # is 600 s, but a low-Earth-orbit pass above 10 degrees lasts roughly 6 minutes -- so
            # the default steps straight over real passes and silently undercounts by about half.
            # This was caught by the tool-ceiling evaluation disagreeing 2:1 with the reference,
            # in a pattern too systematic to be numerical error.
            detector = (
                ElevationDetector(topo)
                .withConstantElevation(10.0 * deg_to_rad)
                .withMaxCheck(30.0)
                .withThreshold(1.0e-3)
                .withHandler(ContinueOnEvent())
            )

            logger = EventsLogger()
            fresh = TLEPropagator.selectExtrapolator(TLE(fixture["line1"], fixture["line2"]))
            fresh.addEventDetector(logger.monitorDetector(detector))
            fresh.propagate(start, end)

            events = list(logger.getLoggedEvents())
            # Rising crossings mark pass starts; falling crossings mark ends. Counting only
            # rises avoids double-counting and avoids a partial pass in progress at the window
            # boundary being counted as a whole one.
            rises = [event for event in events if event.isIncreasing()]

            context = {
                "norad_id": norad,
                "regime": regime,
                "station": station,
                "from_utc": EVAL_EPOCHS[0],
                "days": 3,
                "min_elevation_deg": 10.0,
            }

            add(
                "pass_count",
                f"How many times does {name} (NORAD {norad}) rise above 10 degrees elevation "
                f"as seen from {station['name']} "
                f"({station['lat']:.4f}N, {station['lon']:.4f}E) during the 3 days from "
                f"{EVAL_EPOCHS[0]}Z?",
                len(rises),
                "passes",
                context,
                memorizable=False,
            )

            if rises:
                first_rise = rises[0].getState().getDate()
                # Sample the elevation across the pass to find its peak. A pass is minutes
                # long, so 10-second steps resolve the maximum well within tolerance.
                peak = 0.0
                for step in range(0, 1200, 10):
                    moment = first_rise.shiftedBy(float(step))
                    position = fresh.propagate(moment).getPVCoordinates(itrf).getPosition()
                    elevation = float(topo.getElevation(position, itrf, moment)) * 180.0 / 3.14159
                    peak = max(peak, elevation)

                # The mask and the coordinates must both be stated. Without a mask, "the first
                # pass" is whichever grazing pass happens to clear the horizon first, and a
                # correct method answering 0.16 degrees would be scored wrong against a 10-degree
                # reference. Without coordinates, the model has to recall the city's position and
                # can put London in the wrong hemisphere. Neither ambiguity measures grounding.
                add(
                    "max_elevation",
                    f"For the first pass of {name} (NORAD {norad}) over {station['name']} "
                    f"({station['lat']:.4f}N, {station['lon']:.4f}E) rising above a "
                    f"10 degree elevation mask after {EVAL_EPOCHS[0]}Z, "
                    f"what is the maximum elevation in degrees?",
                    peak,
                    "deg",
                    context,
                    memorizable=False,
                )

    memorizable_count = sum(1 for q in questions if q["memorizable"])

    return {
        "_provenance": {
            "source": "orekit",
            "orekit_jpype_version": package_version("orekit_jpype"),
            "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "generator": "tools/eval/generate_questions.py",
            "note": (
                "Ground truth from an independent implementation, never from the code being "
                "graded. Questions are weighted toward quantities a language model cannot "
                "recall; the memorizable minority is a control, not filler."
            ),
        },
        "summary": {
            "total": len(questions),
            "memorizable_control": memorizable_count,
            "unmemorizable": len(questions) - memorizable_count,
            "categories": sorted({q["category"] for q in questions}),
        },
        "questions": questions,
    }


def main() -> int:
    """Generate the evaluation dataset and write it to disk.

    Returns:
        Process exit code.
    """
    document = generate()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

    summary = document["summary"]
    print(f"wrote {OUTPUT_PATH.relative_to(REPO_ROOT)}")
    print(f"  total questions:     {summary['total']}")
    print(f"  unmemorizable:       {summary['unmemorizable']}")
    print(f"  memorizable control: {summary['memorizable_control']}")
    print(f"  categories:          {', '.join(summary['categories'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

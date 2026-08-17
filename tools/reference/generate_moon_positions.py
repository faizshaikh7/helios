"""Fetch true moon positions from JPL Horizons, to measure two-body drift.

`science/smallbodies.py` propagates committed element sets as a two-body orbit, which ignores the
parent planet's oblateness, the Sun, and the sibling moons. That is a real simplification, and
this measures its cost instead of asserting one.

Horizons is asked for actual state vectors at dates spread out from the element epoch, so the
test can report how far the propagation has drifted after a week, a month, and half a year.

Run manually; never in CI:

    uv run python tools/reference/generate_moon_positions.py
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import UTC, datetime
from typing import Any
from urllib import parse as urlparse
from urllib import request as urlrequest

from orekit_setup import REPO_ROOT

OUTPUT_PATH = REPO_ROOT / "backend" / "tests" / "reference" / "moon_positions.json"

HORIZONS_URL = "https://ssd.jpl.nasa.gov/api/horizons.api"

# One moon per parent, spanning close-in to far-out orbits, so the drift is characterised across
# the range rather than at one convenient case.
SAMPLES = [
    {"id": "301", "name": "Moon", "centre": "500@399"},
    {"id": "401", "name": "Phobos", "centre": "500@499"},
    {"id": "501", "name": "Io", "centre": "500@599"},
    {"id": "606", "name": "Titan", "centre": "500@699"},
    {"id": "705", "name": "Miranda", "centre": "500@799"},
    {"id": "801", "name": "Triton", "centre": "500@899"},
]

# Element epoch is 2026-01-01, so these are one week, one month, and roughly six months out.
DATES = ["2026-01-08", "2026-02-01", "2026-07-01"]

REQUEST_TIMEOUT_S = 90
REQUEST_INTERVAL_S = 1.5

USER_AGENT = "helios/0.1 (orbital mechanics research tool)"

VECTOR_PATTERN = re.compile(r"X\s*=\s*([-+0-9.Ee]+)\s*Y\s*=\s*([-+0-9.Ee]+)\s*Z\s*=\s*([-+0-9.Ee]+)")


def _fetch(body_id: str, centre: str, date: str) -> tuple[float, float, float]:
    """Ask Horizons for one state vector.

    Args:
        body_id: Horizons body identifier.
        centre: Coordinate centre.
        date: Calendar date, TDB.

    Returns:
        Position in kilometres, ecliptic, planet-centred.

    Raises:
        RuntimeError: If the response has no vector.
    """
    query = urlparse.urlencode(
        {
            "format": "text",
            "COMMAND": f"'{body_id}'",
            "OBJ_DATA": "NO",
            "MAKE_EPHEM": "YES",
            "EPHEM_TYPE": "VECTORS",
            "CENTER": f"'{centre}'",
            "START_TIME": f"'{date}'",
            "STOP_TIME": f"'{date} 01:00'",
            "STEP_SIZE": "'1h'",
            "REF_PLANE": "'ECLIPTIC'",
            "OUT_UNITS": "'KM-S'",
            "VEC_TABLE": "'1'",
        }
    )

    request = urlrequest.Request(f"{HORIZONS_URL}?{query}", headers={"User-Agent": USER_AGENT})
    with urlrequest.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
        text = response.read().decode("utf-8", errors="replace")

    if "$$SOE" not in text:
        raise RuntimeError("no ephemeris block")

    block = text.split("$$SOE", 1)[1].split("$$EOE", 1)[0]
    match = VECTOR_PATTERN.search(block)
    if not match:
        raise RuntimeError("no vector in the ephemeris block")

    return (float(match.group(1)), float(match.group(2)), float(match.group(3)))


def main() -> int:
    """Fetch every sample and write the reference."""
    entries: list[dict[str, Any]] = []
    first = True

    for sample in SAMPLES:
        for date in DATES:
            if not first:
                time.sleep(REQUEST_INTERVAL_S)
            first = False

            try:
                x, y, z = _fetch(sample["id"], sample["centre"], date)
            except Exception as exc:  # noqa: BLE001 - one failure must not lose the rest
                print(f"  {sample['name']:8} {date}  FAILED: {exc}")
                continue

            entries.append(
                {
                    "name": sample["name"],
                    "date_tdb": date,
                    "x_km": x,
                    "y_km": y,
                    "z_km": z,
                }
            )
            print(f"  {sample['name']:8} {date}  ({x:>14,.1f}, {y:>14,.1f}, {z:>12,.1f}) km")

    document = {
        "_provenance": {
            "source": "JPL Horizons",
            "url": HORIZONS_URL,
            "frame": "ecliptic of J2000, planet-centred",
            "element_epoch": "2026-01-01",
            "fetched_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "generator": "tools/reference/generate_moon_positions.py",
            "note": (
                "True positions used to measure how far the two-body propagation in "
                "science/smallbodies.py drifts as the element set ages. The dates are one week, "
                "one month and six months past the element epoch."
            ),
        },
        "positions": entries,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

    print(f"\nwrote {OUTPUT_PATH.relative_to(REPO_ROOT)} with {len(entries)} positions")
    return 0


if __name__ == "__main__":
    sys.exit(main())

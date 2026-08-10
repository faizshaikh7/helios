"""Freeze a set of real element sets for the evaluation dataset.

Run once, deliberately. The output is committed so the evaluation set is reproducible forever
and does not depend on what Celestrak happens to be serving on the day a chart is generated.

Satellites are chosen to span orbit regimes, because a question set drawn only from the ISS
would measure competence at one inclination and one altitude. A system can look excellent on
LEO and fall apart on a geostationary or highly-eccentric orbit.

    uv run python tools/eval/fetch_fixtures.py
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from science import catalog

OUTPUT_PATH = REPO_ROOT / "eval" / "tle_fixtures.json"

# Chosen for regime coverage, not fame:
#   ISS        LEO, 51.6 deg, the reference case
#   Hubble     LEO, 28.5 deg, lower inclination
#   NOAA 19    sun-synchronous, ~99 deg retrograde -- catches inclination sign errors
#   Tiangong   LEO, 41.5 deg
#   GOES 16    geostationary -- 6x the radius, where angular errors amplify
#   XMM-Newton highly eccentric, where circular approximations break and SGP4 hands off to
#              the deep-space SDP4 path -- a regime that catches propagator misuse
#   GPS        medium Earth orbit, also deep-space by SGP4's classification
SATELLITES = [
    (25544, "ISS (ZARYA)", "LEO"),
    (20580, "Hubble Space Telescope", "LEO low-inclination"),
    (33591, "NOAA 19", "sun-synchronous"),
    (48274, "Tiangong", "LEO"),
    (41866, "GOES 16", "geostationary"),
    (25989, "XMM-Newton", "highly eccentric"),
    (41328, "GPS BIIF-12", "medium Earth orbit"),
]


def main() -> int:
    """Fetch and freeze the fixture element sets.

    Returns:
        Process exit code; non-zero if nothing could be fetched.
    """
    # Preserve anything already frozen. Celestrak occasionally times out or returns a 500 under
    # rapid sequential requests, and losing a satellite that was fetched successfully on an
    # earlier run would silently shrink the evaluation set.
    existing: dict[int, dict[str, object]] = {}
    if OUTPUT_PATH.exists():
        previous = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
        existing = {int(item["norad_id"]): item for item in previous.get("satellites", [])}

    entries = []

    for index, (norad_id, label, regime) in enumerate(SATELLITES):
        if norad_id in existing:
            entries.append(existing[norad_id])
            print(f"  keep {norad_id:>6}  {existing[norad_id]['name']}")
            continue

        # Space the requests out: seven back-to-back fetches is both impolite and unreliable.
        if index:
            time.sleep(3)

        try:
            tle = catalog.get_tle(norad_id, use_cache=False)
        except catalog.CatalogError as exc:
            print(f"  SKIP {norad_id} {label}: {exc}")
            continue

        entries.append(
            {
                "norad_id": tle.norad_id,
                "name": tle.name,
                "regime": regime,
                "line1": tle.line1,
                "line2": tle.line2,
            }
        )
        print(f"  got  {tle.norad_id:>6}  {tle.name}  ({regime})")

    if not entries:
        print("no element sets retrieved", file=sys.stderr)
        return 1

    document = {
        "_provenance": {
            "source": "celestrak",
            "frozen_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "generator": "tools/eval/fetch_fixtures.py",
            "note": (
                "Frozen deliberately so the evaluation set is reproducible and independent of "
                "what Celestrak serves on any given day. Re-running changes every ground-truth "
                "answer downstream, so treat regeneration as a deliberate act."
            ),
        },
        "satellites": entries,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {OUTPUT_PATH.relative_to(REPO_ROOT)} ({len(entries)} satellites)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

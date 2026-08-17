"""Fetch orbital elements for the largest asteroids from JPL's Small-Body Database.

The asteroid belt is usually drawn as a decorative ring of scattered dots. That is a picture of
an idea rather than of anything measured, and it gives a badly wrong impression: the real belt is
mostly empty, strongly structured by Jupiter's resonances, and the objects in it are on
individually known orbits.

So these are real asteroids with real elements from **JPL SBDB**, propagated the same way the
moons are. The Kirkwood gaps are visible in the result because they are in the data, not because
anything drew them.

Run manually; never in CI:

    uv run python tools/reference/fetch_asteroids.py
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import Any
from urllib import parse as urlparse
from urllib import request as urlrequest

from orekit_setup import REPO_ROOT

OUTPUT_PATH = REPO_ROOT / "backend" / "science" / "data" / "asteroids.json"

SBDB_URL = "https://ssd-api.jpl.nasa.gov/sbdb_query.api"

# Diameter floor in kilometres. Everything above this is a body a mission might actually name,
# and the count stays small enough to render as individual objects rather than as a fog.
MINIMUM_DIAMETER_KM = 50.0

REQUEST_TIMEOUT_S = 120

USER_AGENT = "helios/0.1 (orbital mechanics research tool)"


def fetch() -> list[dict[str, Any]]:
    """Query SBDB for large asteroids with full element sets.

    Returns:
        One record per asteroid.

    Raises:
        RuntimeError: If the response is malformed or implausibly small.
    """
    query = urlparse.urlencode(
        {
            "fields": "full_name,a,e,i,om,w,ma,epoch,diameter,class",
            "sb-kind": "a",
            "sb-cdata": json.dumps({"AND": [f"diameter|GT|{MINIMUM_DIAMETER_KM}"]}),
            "limit": "2000",
        }
    )

    request = urlrequest.Request(f"{SBDB_URL}?{query}", headers={"User-Agent": USER_AGENT})
    with urlrequest.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
        payload = json.loads(response.read().decode("utf-8"))

    fields = payload.get("fields")
    rows = payload.get("data")
    if not fields or not rows:
        raise RuntimeError("SBDB returned no data")

    index = {name: position for position, name in enumerate(fields)}

    records: list[dict[str, Any]] = []
    for row in rows:

        def value(name: str, row: list[Any] = row) -> float | None:
            raw = row[index[name]]
            if raw in (None, ""):
                return None
            try:
                return float(raw)
            except (TypeError, ValueError):
                return None

        semi_major_axis = value("a")
        eccentricity = value("e")
        inclination = value("i")
        node = value("om")
        periapsis = value("w")
        mean_anomaly = value("ma")
        epoch = value("epoch")

        # An incomplete element set cannot be propagated. Dropping the row is correct; filling a
        # gap with a default would invent an orbit for a real object.
        if None in (semi_major_axis, eccentricity, inclination, node, periapsis, mean_anomaly, epoch):
            continue

        name = str(row[index["full_name"]]).strip()

        records.append(
            {
                "name": name,
                "class": str(row[index["class"]]).strip(),
                "semi_major_axis_au": round(semi_major_axis, 6),
                "eccentricity": round(eccentricity, 6),
                "inclination_deg": round(inclination, 4),
                "node_deg": round(node, 4),
                "argument_of_periapsis_deg": round(periapsis, 4),
                "mean_anomaly_deg": round(mean_anomaly, 4),
                "epoch_jd": epoch,
                "diameter_km": value("diameter"),
            }
        )

    if len(records) < 50:
        raise RuntimeError(f"only {len(records)} asteroids parsed; the query looks wrong")

    return records


def main() -> int:
    """Fetch the asteroids and write them to disk."""
    records = fetch()
    records.sort(key=lambda item: -(item["diameter_km"] or 0))

    document = {
        "_provenance": {
            "source": "NASA/JPL Small-Body Database Query API",
            "url": SBDB_URL,
            "selection": f"asteroids with a measured diameter above {MINIMUM_DIAMETER_KM} km",
            "frame": "heliocentric ecliptic, J2000",
            "elements": "osculating, at each object's own epoch as published",
            "fetched_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "generator": "tools/reference/fetch_asteroids.py",
            "note": (
                "Real objects on real orbits, propagated as two-body. Each element set has its "
                "own epoch, which is honoured rather than assumed common. This is a size-limited "
                "selection, not the whole belt: over a million asteroids are known, and drawing "
                "a representative scatter instead would be a picture of an idea rather than of "
                "anything measured."
            ),
        },
        "count": len(records),
        "asteroids": records,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(document, separators=(",", ":")) + "\n", encoding="utf-8")

    print(f"wrote {OUTPUT_PATH.relative_to(REPO_ROOT)}")
    print(f"  {len(records)} asteroids larger than {MINIMUM_DIAMETER_KM} km")
    print(f"  largest: {records[0]['name'].strip()} at {records[0]['diameter_km']} km")
    print(f"  size: {OUTPUT_PATH.stat().st_size / 1024:.0f} kB")
    return 0


if __name__ == "__main__":
    sys.exit(main())

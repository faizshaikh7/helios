"""Fetch the bright-star catalogue from VizieR.

The starfield in the solar-system view was decorative: dots at made-up positions, labelled as
such. That was honest but weak, and it is the last invented thing left in the picture. This
replaces it with the **Yale Bright Star Catalogue** (V/50), the standard catalogue of naked-eye
stars, served through CDS VizieR.

With this, a star in the view is at its real right ascension and declination, with its real
apparent magnitude and colour index -- so the constellations are the actual constellations, and
Orion looks like Orion.

Committed rather than fetched at runtime, matching every other dataset here: a deployed service
must not need the network to start, and a committed file makes a change to the data a reviewable
diff.

Run manually; never in CI:

    uv run python tools/reference/fetch_stars.py
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import Any
from urllib import parse as urlparse
from urllib import request as urlrequest

from orekit_setup import REPO_ROOT

OUTPUT_PATH = REPO_ROOT / "backend" / "science" / "data" / "bright_stars.json"

VIZIER_URL = "https://vizier.cds.unistra.fr/viz-bin/asu-tsv"

# Yale Bright Star Catalogue, 5th revised edition.
CATALOGUE = "V/50/catalog"

# Naked-eye limit is about magnitude 6.5. Going fainter adds tens of thousands of stars that
# render as noise at any sensible point size.
MAGNITUDE_LIMIT = 6.5

REQUEST_TIMEOUT_S = 120

USER_AGENT = "helios/0.1 (orbital mechanics research tool)"


def _sexagesimal_to_degrees(text: str, *, hours: bool) -> float | None:
    """Convert a sexagesimal coordinate to degrees.

    Args:
        text: Coordinate as "hh mm ss.s" or "+dd mm ss".
        hours: True when the value is in hours, so it scales by 15 degrees per hour.

    Returns:
        Degrees, or None if the field is blank or malformed.
    """
    parts = text.strip().split()
    if len(parts) != 3:
        return None

    try:
        first = float(parts[0])
        minutes = float(parts[1])
        seconds = float(parts[2])
    except ValueError:
        return None

    # The sign belongs to the whole value, so it must be applied after combining the parts --
    # otherwise a declination of -00 30 00 comes out positive, because -0.0 has no sign to carry.
    sign = -1.0 if text.strip().startswith("-") else 1.0
    magnitude = abs(first) + minutes / 60.0 + seconds / 3600.0

    return sign * magnitude * (15.0 if hours else 1.0)


def fetch() -> list[dict[str, Any]]:
    """Query VizieR and parse the response into star records.

    Returns:
        One record per star, with position in degrees.

    Raises:
        RuntimeError: If the catalogue cannot be read.
    """
    query = urlparse.urlencode(
        {
            "-source": CATALOGUE,
            "-out": "RAJ2000,DEJ2000,Vmag,B-V,Name",
            "-out.max": "100000",
            "Vmag": f"<{MAGNITUDE_LIMIT}",
        }
    )

    request = urlrequest.Request(f"{VIZIER_URL}?{query}", headers={"User-Agent": USER_AGENT})
    with urlrequest.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
        body = response.read().decode("utf-8", errors="replace")

    stars: list[dict[str, Any]] = []
    seen_separator = False

    for line in body.splitlines():
        if line.startswith("#"):
            continue
        if line.startswith("---"):
            # Everything before the rule is header; everything after is data.
            seen_separator = True
            continue
        if not seen_separator or not line.strip():
            continue

        columns = line.split("\t")
        if len(columns) < 4:
            continue

        right_ascension = _sexagesimal_to_degrees(columns[0], hours=True)
        declination = _sexagesimal_to_degrees(columns[1], hours=False)
        if right_ascension is None or declination is None:
            continue

        try:
            magnitude = float(columns[2])
        except ValueError:
            continue

        try:
            colour_index = float(columns[3])
        except ValueError:
            # B-V is genuinely missing for some entries. None is correct; a default would
            # invent a colour for a real star.
            colour_index = None

        name = columns[4].strip() if len(columns) > 4 else ""

        stars.append(
            {
                "ra_deg": round(right_ascension, 5),
                "dec_deg": round(declination, 5),
                "vmag": round(magnitude, 3),
                "bv": round(colour_index, 3) if colour_index is not None else None,
                "name": name or None,
            }
        )

    if len(stars) < 1000:
        raise RuntimeError(
            f"only {len(stars)} stars parsed; the catalogue should hold several thousand "
            "brighter than magnitude 6.5, so the response format has probably changed"
        )

    return stars


def main() -> int:
    """Fetch the catalogue and write it to disk."""
    stars = fetch()
    stars.sort(key=lambda star: star["vmag"])

    document = {
        "_provenance": {
            "source": "Yale Bright Star Catalogue, 5th revised edition (Hoffleit & Warren 1991)",
            "catalogue": CATALOGUE,
            "served_by": "CDS VizieR, vizier.cds.unistra.fr",
            "equinox": "J2000",
            "magnitude_limit": MAGNITUDE_LIMIT,
            "fetched_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "generator": "tools/reference/fetch_stars.py",
            "note": (
                "Real stars at real positions, replacing a decorative starfield. Positions are "
                "J2000 mean places: proper motion is not applied, which moves the fastest stars "
                "by under an arcminute per century and nothing at all at this rendering scale."
            ),
            "acknowledgement": (
                "This research has made use of the VizieR catalogue access tool, CDS, "
                "Strasbourg, France (DOI 10.26093/cds/vizier)."
            ),
        },
        "count": len(stars),
        "stars": stars,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(document, separators=(",", ":")) + "\n", encoding="utf-8")

    print(f"wrote {OUTPUT_PATH.relative_to(REPO_ROOT)}")
    print(f"  {len(stars)} stars brighter than magnitude {MAGNITUDE_LIMIT}")
    print(f"  brightest: {stars[0]['name']} at V={stars[0]['vmag']}")
    print(f"  size: {OUTPUT_PATH.stat().st_size / 1024:.0f} kB")
    return 0


if __name__ == "__main__":
    sys.exit(main())

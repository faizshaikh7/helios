"""Fetch osculating elements for the major moons from JPL Horizons.

astropy's builtin ephemeris knows the Sun, the planets and Earth's Moon, and nothing else. Every
other moon needs a source, and typing orbital elements in from memory is the one thing this
project rules out on principle.

So the elements come from **JPL Horizons**, the authoritative ephemeris service, relative to each
moon's own parent planet and referred to the ecliptic -- the same plane the renderer uses. They
are committed with their epoch, and `science/moons.py` propagates them as a two-body orbit.

**Secular rates, measured rather than assumed.** Pure two-body propagation of a single element
set fails badly for close-in moons. Measured against Horizons, Phobos came out wrong by twice its
own orbit radius after six months, and Io by 78% of its. The cause is the parent planet's
equatorial bulge, which makes the orbit plane and the line of apsides precess.

Rather than hard-coding a J2 for every planet, this asks Horizons for a *series* of element sets
across half a year and fits the drift in the node and the argument of periapsis directly. The
rates are therefore measured from the authoritative ephemeris, and they absorb third-body effects
along with the oblateness instead of modelling only one of them.

What remains unmodelled is everything periodic. That residual is measured in
`tests/test_smallbodies.py` and reported on every value.

Run manually; never in CI:

    uv run python tools/reference/fetch_moons.py
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

OUTPUT_PATH = REPO_ROOT / "backend" / "science" / "data" / "moons.json"

HORIZONS_URL = "https://ssd.jpl.nasa.gov/api/horizons.api"

# Elements are taken at this epoch and propagated from there.
EPOCH = "2026-01-01"

# A series is requested from the epoch to this date so secular precession rates can be fitted.
# One request returns the whole series, so this costs no extra requests.
SERIES_END = "2026-07-01"
SERIES_STEP = "15d"

REQUEST_TIMEOUT_S = 90

# Horizons asks for restraint; one request per moon with a pause between is comfortably polite.
REQUEST_INTERVAL_S = 1.5

USER_AGENT = "helios/0.1 (orbital mechanics research tool)"

# Major moons, by Horizons body id, with the planet-centre code they orbit.
#
# Restricted to bodies large enough to be worth drawing: everything here is either round or a
# well-known irregular. The list is not "all moons" -- there are hundreds -- and the panel says so.
MOONS: list[dict[str, Any]] = [
    {"id": "301", "name": "Moon", "planet": "earth", "centre": "500@399"},
    {"id": "401", "name": "Phobos", "planet": "mars", "centre": "500@499"},
    {"id": "402", "name": "Deimos", "planet": "mars", "centre": "500@499"},
    {"id": "501", "name": "Io", "planet": "jupiter", "centre": "500@599"},
    {"id": "502", "name": "Europa", "planet": "jupiter", "centre": "500@599"},
    {"id": "503", "name": "Ganymede", "planet": "jupiter", "centre": "500@599"},
    {"id": "504", "name": "Callisto", "planet": "jupiter", "centre": "500@599"},
    {"id": "601", "name": "Mimas", "planet": "saturn", "centre": "500@699"},
    {"id": "602", "name": "Enceladus", "planet": "saturn", "centre": "500@699"},
    {"id": "603", "name": "Tethys", "planet": "saturn", "centre": "500@699"},
    {"id": "604", "name": "Dione", "planet": "saturn", "centre": "500@699"},
    {"id": "605", "name": "Rhea", "planet": "saturn", "centre": "500@699"},
    {"id": "606", "name": "Titan", "planet": "saturn", "centre": "500@699"},
    {"id": "608", "name": "Iapetus", "planet": "saturn", "centre": "500@699"},
    {"id": "701", "name": "Ariel", "planet": "uranus", "centre": "500@799"},
    {"id": "702", "name": "Umbriel", "planet": "uranus", "centre": "500@799"},
    {"id": "703", "name": "Titania", "planet": "uranus", "centre": "500@799"},
    {"id": "704", "name": "Oberon", "planet": "uranus", "centre": "500@799"},
    {"id": "705", "name": "Miranda", "planet": "uranus", "centre": "500@799"},
    {"id": "801", "name": "Triton", "planet": "neptune", "centre": "500@899"},
]

ELEMENT_PATTERN = re.compile(r"([A-Z]{1,2})\s*=\s*([-+0-9.Ee]+)")

RADIUS_PATTERN = re.compile(
    r"(?:Radius|Mean radius|Vol\.? [Mm]ean [Rr]adius)[^=\n]*=\s*([0-9.]+)", re.IGNORECASE
)


def _request(body_id: str, centre: str) -> str:
    """Ask Horizons for one moon's elements at the epoch.

    Args:
        body_id: Horizons body identifier.
        centre: Coordinate centre, the parent planet.

    Returns:
        The raw text response.
    """
    query = urlparse.urlencode(
        {
            "format": "text",
            "COMMAND": f"'{body_id}'",
            "OBJ_DATA": "YES",
            "MAKE_EPHEM": "YES",
            "EPHEM_TYPE": "ELEMENTS",
            "CENTER": f"'{centre}'",
            "START_TIME": f"'{EPOCH}'",
            "STOP_TIME": f"'{SERIES_END}'",
            "STEP_SIZE": f"'{SERIES_STEP}'",
            "REF_PLANE": "'ECLIPTIC'",
            "OUT_UNITS": "'KM-S'",
        }
    )

    request = urlrequest.Request(f"{HORIZONS_URL}?{query}", headers={"User-Agent": USER_AGENT})
    with urlrequest.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
        return response.read().decode("utf-8", errors="replace")


def _unwrap(values: list[float]) -> list[float]:
    """Remove 360-degree jumps from a sequence of angles.

    A precessing node crosses zero, and fitting a rate through the raw values would read that
    wrap as an enormous slope. Samples are close enough together that the true step between them
    is far below half a turn, so the nearest continuation is the correct one.

    Args:
        values: Angles in degrees, in time order.

    Returns:
        The same angles, made continuous.
    """
    unwrapped = [values[0]]
    for value in values[1:]:
        previous = unwrapped[-1]
        candidate = value
        while candidate - previous > 180.0:
            candidate -= 360.0
        while previous - candidate > 180.0:
            candidate += 360.0
        unwrapped.append(candidate)
    return unwrapped


def _slope(days: list[float], values: list[float]) -> float:
    """Least-squares slope of `values` against `days`, in units per day."""
    count = len(days)
    mean_x = sum(days) / count
    mean_y = sum(values) / count

    covariance = sum((x - mean_x) * (y - mean_y) for x, y in zip(days, values, strict=True))
    variance = sum((x - mean_x) ** 2 for x in days)

    return covariance / variance if variance else 0.0


def _parse(text: str) -> dict[str, float]:
    """Extract a series of element sets, fit secular rates, and read the body radius.

    Args:
        text: Raw Horizons text.

    Returns:
        Elements at the epoch, fitted precession rates, and radius_km where available.

    Raises:
        RuntimeError: If the ephemeris block is missing or too short to fit.
    """
    if "$$SOE" not in text or "$$EOE" not in text:
        raise RuntimeError("no ephemeris block in the response")

    block = text.split("$$SOE", 1)[1].split("$$EOE", 1)[0]

    samples: list[dict[str, float]] = []
    epochs: list[float] = []

    lines = [line for line in block.splitlines() if line.strip()]
    index = 0
    while index < len(lines):
        head = lines[index]
        if "=" not in head or "A.D." not in head:
            index += 1
            continue

        try:
            julian_date = float(head.split("=", 1)[0].strip())
        except ValueError:
            index += 1
            continue

        # A Horizons element sample is a date line followed by *four* lines of elements. Reading
        # only three drops the semi-major axis, which then fails the completeness check below --
        # so every sample is skipped and the fit sees nothing at all.
        body = "\n".join(lines[index + 1 : index + 5])
        values = {key: float(value) for key, value in ELEMENT_PATTERN.findall(body)}

        if {"EC", "IN", "OM", "W", "MA", "A", "N"} <= values.keys():
            samples.append(values)
            epochs.append(julian_date)

        index += 5

    if len(samples) < 3:
        raise RuntimeError(f"only {len(samples)} element sets parsed; need three to fit a rate")

    days = [epoch - epochs[0] for epoch in epochs]

    node_rate = _slope(days, _unwrap([sample["OM"] for sample in samples]))

    # Fit the *longitude* of periapsis, not the argument of periapsis.
    #
    # These moons are very nearly circular -- Io's eccentricity is 0.004 -- and on a near-circular
    # orbit the argument of periapsis is barely defined: the osculating value swings wildly
    # between samples because there is almost no ellipse to measure it against. Fitting a rate to
    # that produces a confident, wrong number, which measured against Horizons made Io's position
    # worse than having no precession model at all.
    #
    # The longitude of periapsis, OM + W, stays well conditioned as the eccentricity falls,
    # because the two ill-defined halves move oppositely and their sum does not.
    periapsis_longitude_rate = _slope(
        days, _unwrap([sample["OM"] + sample["W"] for sample in samples])
    )
    periapsis_rate = periapsis_longitude_rate - node_rate

    def mean(key: str) -> float:
        return sum(sample[key] for sample in samples) / len(samples)

    # Fit the mean *longitude* rate rather than trusting the osculating mean motion.
    #
    # The body's true angular progression is d(lambda)/dt where lambda = OM + W + MA. Precessing
    # the node and periapsis while advancing MA at Horizons' N double-counts the precession: for
    # Io that shifts the orbital rate by 0.37%, which is 136 degrees of phase over six months and
    # put it on the wrong side of Jupiter.
    #
    # MA cycles many times between samples -- Io goes round eight and a half times in fifteen
    # days -- so the longitude cannot be unwrapped by proximity. The revolution count is instead
    # resolved against a prediction from N, which is far more accurate than the half-turn needed
    # to pick the right cycle.
    longitude_estimate_rate = mean("N") * 86400.0 + node_rate + periapsis_rate
    base_longitude = samples[0]["OM"] + samples[0]["W"] + samples[0]["MA"]

    # Cycles are resolved step by step from the previous sample, not from the first one. The
    # starting rate estimate is off by about a degree a day for Io, which is harmless across one
    # fifteen-day step but exceeds half a revolution by the end of six months -- and a single
    # miscounted cycle wrecks the fit. Stepping keeps the prediction error to one interval's
    # worth however long the series runs.
    longitudes = [base_longitude]
    for index in range(1, len(samples)):
        observed = samples[index]["OM"] + samples[index]["W"] + samples[index]["MA"]
        predicted = longitudes[-1] + longitude_estimate_rate * (days[index] - days[index - 1])
        revolutions = round((predicted - observed) / 360.0)
        longitudes.append(observed + revolutions * 360.0)

    longitude_rate = _slope(days, longitudes)

    radius = RADIUS_PATTERN.search(text)

    return {
        # Shape and size are averaged across the series: the osculating values wobble with
        # short-period terms, and the mean is a better constant than any single sample.
        "eccentricity": mean("EC"),
        "inclination_deg": mean("IN"),
        "semi_major_axis_km": mean("A"),
        "mean_motion_deg_per_s": mean("N"),
        # Angles are taken at the epoch, since that is where propagation starts.
        "node_deg": samples[0]["OM"],
        "argument_of_periapsis_deg": samples[0]["W"],
        "mean_anomaly_deg": samples[0]["MA"],
        # Mean longitude and its fitted rate. The mean anomaly is reconstructed from these at
        # propagation time as MA = lambda - OM - W, which keeps the three angles consistent.
        "mean_longitude_deg": base_longitude,
        "mean_longitude_rate_deg_per_day": longitude_rate,
        # Fitted secular drift, degrees per day.
        "node_rate_deg_per_day": node_rate,
        "periapsis_rate_deg_per_day": periapsis_rate,
        "samples": len(samples),
        "radius_km": float(radius.group(1)) if radius else None,
    }


def main() -> int:
    """Fetch every moon's elements and write them to disk."""
    records = []

    for index, moon in enumerate(MOONS):
        if index:
            time.sleep(REQUEST_INTERVAL_S)

        try:
            parsed = _parse(_request(moon["id"], moon["centre"]))
        except Exception as exc:  # noqa: BLE001 - one bad moon must not lose the other nineteen
            print(f"  {moon['name']:10} FAILED: {exc}")
            continue

        records.append({"name": moon["name"], "planet": moon["planet"], **parsed})
        print(
            f"  {moon['name']:10} a={parsed['semi_major_axis_km']:>12,.0f} km  "
            f"e={parsed['eccentricity']:.4f}  "
            f"node {parsed['node_rate_deg_per_day']:+9.4f}/d  "
            f"peri {parsed['periapsis_rate_deg_per_day']:+9.4f}/d"
        )

    if len(records) < len(MOONS) * 0.8:
        raise RuntimeError(f"only {len(records)}/{len(MOONS)} moons fetched; refusing to write")

    document = {
        "_provenance": {
            "source": "JPL Horizons",
            "url": HORIZONS_URL,
            "epoch_tdb": EPOCH,
            "frame": "ecliptic of J2000, centred on the parent planet",
            "elements": (
                "Angles at the epoch; shape averaged over the series; node and periapsis "
                "precession rates fitted by least squares across the series."
            ),
            "series": f"{EPOCH} to {SERIES_END} every {SERIES_STEP}",
            "fetched_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "generator": "tools/reference/fetch_moons.py",
            "note": (
                "Two-body propagation with fitted secular precession of the node and "
                "periapsis. The rates are measured from a Horizons series rather than modelled "
                "from a J2, so they absorb third-body effects too. Periodic terms remain "
                "unmodelled; the residual is measured in tests/test_smallbodies.py."
            ),
        },
        "count": len(records),
        "moons": records,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

    print(f"\nwrote {OUTPUT_PATH.relative_to(REPO_ROOT)} with {len(records)} moons")
    return 0


if __name__ == "__main__":
    sys.exit(main())

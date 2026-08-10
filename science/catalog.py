"""Satellite catalog access.

The public app serves **Celestrak** data. Space-Track is the authoritative full catalog but its
user agreement restricts bulk redistribution to third parties, so it is reserved for evaluation
ground truth and internal work. See .agent/context.md.

TLEs change slowly -- a fresh element set is published a few times a day at most -- so responses
are cached aggressively. Hammering Celestrak per request would be both rude and slow.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from urllib import error as urlerror
from urllib import request as urlrequest

CELESTRAK_GP_URL = "https://celestrak.org/NORAD/elements/gp.php"
REQUEST_TIMEOUT_S = 20
REQUEST_ATTEMPTS = 3
CACHE_TTL_S = 3600  # TLEs are republished a few times a day; an hour is comfortably fresh.

USER_AGENT = "helios/0.1 (orbital mechanics research tool)"


class CatalogError(RuntimeError):
    """Raised when a satellite cannot be retrieved."""


@dataclass(frozen=True)
class TLE:
    """A two-line element set, as published.

    Attributes:
        name: Satellite name as Celestrak reports it.
        line1: TLE line 1.
        line2: TLE line 2.
        norad_id: NORAD catalog number.
        source: Where this came from, for provenance.
        fetched_at_unix: When it was retrieved, for provenance and staleness checks.
    """

    name: str
    line1: str
    line2: str
    norad_id: int
    source: str
    fetched_at_unix: float


# Module-level cache: {norad_id: (expires_at_unix, TLE)}. Adequate for a single process; a
# shared cache belongs behind the API once there is more than one instance.
_cache: dict[int, tuple[float, TLE]] = {}


def _fetch_text(url: str) -> str:
    """Fetch a URL as text, with timeout and retry.

    Args:
        url: Fully-formed request URL.

    Returns:
        Response body decoded as UTF-8.

    Raises:
        CatalogError: If every attempt fails.
    """
    last_error: Exception | None = None

    for attempt in range(1, REQUEST_ATTEMPTS + 1):
        try:
            request = urlrequest.Request(url, headers={"User-Agent": USER_AGENT})
            with urlrequest.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
                return response.read().decode("utf-8", errors="replace")
        except (urlerror.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < REQUEST_ATTEMPTS:
                time.sleep(1.0 * attempt)

    raise CatalogError(f"could not reach Celestrak after {REQUEST_ATTEMPTS} attempts: {last_error}")


def _parse_tle_response(text: str, norad_id: int) -> TLE:
    """Parse Celestrak's three-line TLE format.

    Args:
        text: Raw response body.
        norad_id: The catalog number requested, recorded on the result.

    Returns:
        The parsed element set.

    Raises:
        CatalogError: If the response is not a usable TLE.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    # Celestrak returns a plain-text error rather than an HTTP error for unknown objects.
    if len(lines) < 3 or not lines[1].startswith("1 ") or not lines[2].startswith("2 "):
        detail = lines[0] if lines else "empty response"
        raise CatalogError(f"no element set for NORAD {norad_id}: {detail}")

    return TLE(
        name=lines[0],
        line1=lines[1],
        line2=lines[2],
        norad_id=norad_id,
        source="celestrak",
        fetched_at_unix=time.time(),
    )


def get_tle(norad_id: int, *, use_cache: bool = True) -> TLE:
    """Retrieve the current element set for a satellite.

    Args:
        norad_id: NORAD catalog number, e.g. 25544 for the ISS.
        use_cache: Set False to force a network fetch.

    Returns:
        The satellite's current TLE.

    Raises:
        CatalogError: If the satellite is unknown or Celestrak is unreachable.
    """
    now = time.time()

    if use_cache:
        cached = _cache.get(norad_id)
        if cached and cached[0] > now:
            return cached[1]

    url = f"{CELESTRAK_GP_URL}?CATNR={int(norad_id)}&FORMAT=TLE"
    tle = _parse_tle_response(_fetch_text(url), norad_id)

    _cache[norad_id] = (now + CACHE_TTL_S, tle)
    return tle


def seed_cache(tle: TLE, *, ttl_s: float = CACHE_TTL_S) -> None:
    """Insert an element set into the cache directly.

    This is the mock twin required by rules.md for external calls: tests exercise the full tool
    path against a fixed, known element set without touching the network, which keeps them
    deterministic and keeps CI offline.

    Args:
        tle: The element set to install.
        ttl_s: How long it should remain valid.
    """
    _cache[tle.norad_id] = (time.time() + ttl_s, tle)


def clear_cache() -> None:
    """Empty the cache. Intended for tests."""
    _cache.clear()

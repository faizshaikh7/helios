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
    """Raised when the catalog cannot be reached."""


class CatalogNotFound(CatalogError):
    """Raised when the catalog is reachable but has no such object.

    Distinct from `CatalogError` because the two need different responses: an unknown catalog
    number is the caller's mistake and should be a 404, while an unreachable catalog is the
    service's problem and should be a 502. Collapsing them tells the user to retry something that
    will never work.
    """


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


def _not_found_message(norad_id: int) -> str:
    """The single message shown for an unknown catalog number.

    Args:
        norad_id: The number that was requested.

    Returns:
        A message naming the number and offering a known one to try instead.
    """
    return (
        f"No satellite with NORAD catalog number {norad_id} was found in the catalog. "
        "Check the number, or try a known one such as 25544 (ISS)."
    )


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

        except urlerror.HTTPError as exc:
            # A 4xx is a statement about the request, not a transient fault: an unknown catalog
            # number will still be unknown on the third attempt. Retrying wastes the caller's
            # time and is impolite to Celestrak.
            if 400 <= exc.code < 500:
                raise CatalogNotFound(str(exc.code)) from exc
            last_error = exc

        except (urlerror.URLError, TimeoutError, OSError) as exc:
            last_error = exc

        if attempt < REQUEST_ATTEMPTS:
            time.sleep(1.0 * attempt)

    raise CatalogError(
        f"Could not reach Celestrak after {REQUEST_ATTEMPTS} attempts. "
        f"The catalog may be temporarily unavailable. ({last_error})"
    )


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

    # Celestrak often returns 200 with a plain-text message rather than an HTTP error for unknown
    # objects, so a successful response is not evidence the satellite exists.
    if len(lines) < 3 or not lines[1].startswith("1 ") or not lines[2].startswith("2 "):
        raise CatalogNotFound(_not_found_message(norad_id))

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

    # Celestrak signals an unknown object two different ways -- an HTTP 4xx, or a 200 carrying a
    # plain-text message. Both mean the same thing to a user, so both produce the same message
    # here rather than leaking which path happened to be taken.
    try:
        body = _fetch_text(url)
    except CatalogNotFound as exc:
        raise CatalogNotFound(_not_found_message(norad_id)) from exc

    tle = _parse_tle_response(body, norad_id)

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


def load_fixtures(path: str) -> int:
    """Pin the catalog to a frozen set of element sets from a JSON file.

    Used only for evaluation. Ground truth is computed from specific element sets, so a system
    answering with *fresher* ones is measured against the wrong target and loses accuracy it did
    not actually lose. That error grows silently as element sets age, which would make the
    accuracy chart quietly wrong rather than visibly broken -- the worst failure mode available.

    Enabled by setting ``EVAL_FIXTURES`` to a fixtures path. Never set in production, where live
    data is the whole point.

    Args:
        path: Path to a fixtures file with a ``satellites`` array.

    Returns:
        How many element sets were pinned.
    """
    import json
    from pathlib import Path

    document = json.loads(Path(path).read_text(encoding="utf-8"))

    for entry in document["satellites"]:
        seed_cache(
            TLE(
                name=entry["name"],
                line1=entry["line1"],
                line2=entry["line2"],
                norad_id=int(entry["norad_id"]),
                source="eval-fixture",
                # Far-future expiry: pinned element sets must not silently fall back to live data
                # partway through an evaluation run.
                fetched_at_unix=time.time(),
            ),
            ttl_s=365 * 86400,
        )

    return len(document["satellites"])

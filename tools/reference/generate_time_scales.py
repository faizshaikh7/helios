"""Generate time-scale reference values using Orekit.

This is the independent implementation that grades astropy. Orekit is a mature, flight-proven
Java library maintained by a different community, written in a different language, from a
different codebase -- so agreement between it and astropy is meaningful evidence, where
astropy checked against astropy would be circular (see .agent/test.md).

Run manually; do not run in CI. Output is committed to `tests/reference/` so tests compare
against a fixed target and CI needs no JVM:

    uv run python tools/reference/generate_time_scales.py

Epochs deliberately straddle leap-second boundaries, because that is where independent
implementations most often disagree.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib import request as urlrequest

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
OUTPUT_PATH = REPO_ROOT / "tests" / "reference" / "time_scales.json"

# GitLab builds this archive on demand, so it is slow and prone to resetting mid-transfer.
OREKIT_DATA_URL = "https://gitlab.orekit.org/orekit/orekit-data/-/archive/main/orekit-data-main.zip"
DOWNLOAD_ATTEMPTS = 4
DOWNLOAD_TIMEOUT_S = 300
DOWNLOAD_BACKOFF_S = 5

# Epochs chosen to exercise leap-second transitions:
#   2000-01-01  TAI-UTC = 32 s
#   2012-07-02  TAI-UTC = 35 s (after the 2012-06-30 leap second)
#   2016-12-31  TAI-UTC = 36 s (the day before the most recent leap second)
#   2017-01-02  TAI-UTC = 37 s (after it; unchanged since)
#   2026-01-01  TAI-UTC = 37 s (the epoch asserted by /api/health)
EPOCHS_UTC = [
    "2000-01-01T00:00:00",
    "2012-07-02T00:00:00",
    "2016-12-31T00:00:00",
    "2017-01-02T00:00:00",
    "2026-01-01T00:00:00",
]


def _replace_with_retry(source: Path, destination: Path, attempts: int = 6) -> None:
    """Move ``source`` onto ``destination``, retrying while the file is locked.

    On Windows a file that was just written can stay locked briefly by the antivirus scanner,
    which surfaces as ``PermissionError`` WinError 32 on rename. The write itself succeeded, so
    failing here would throw away a completed multi-megabyte download.

    Args:
        source: Path to move from.
        destination: Path to move to.
        attempts: How many times to retry before giving up.

    Raises:
        PermissionError: If the file is still locked after every attempt.
    """
    for attempt in range(1, attempts + 1):
        try:
            source.replace(destination)
            return
        except PermissionError:
            if attempt == attempts:
                raise
            time.sleep(1.5 * attempt)


def _remove_quietly(path: Path) -> None:
    """Delete a file, ignoring failure.

    Used for cleaning up partial downloads in an exception handler. A cleanup failure must never
    replace the original error -- that hides the real cause behind an irrelevant one.

    Args:
        path: File to remove.
    """
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def ensure_orekit_data() -> Path:
    """Download the Orekit data bundle if it is not already present, with retries.

    Orekit needs leap-second tables, Earth orientation parameters, and related data that ship
    separately from the library. The bundle is large and gitignored, so it is fetched on demand.

    ``orekit_jpype.pyhelpers.download_orekit_data_curdir`` is deliberately not used: it streams
    straight onto the destination path with no timeout, no retry, and no validation. The URL is
    a GitLab archive generated on the fly, which is slow and drops connections -- a mid-transfer
    reset leaves a truncated file sitting exactly where a valid one belongs, so the next run
    treats corrupt data as a cache hit.

    Downloads land on a ``.part`` file, are validated as a real zip, and only then moved into
    place, so a failed attempt can never masquerade as success.

    Returns:
        Path to the validated local Orekit data archive.

    Raises:
        RuntimeError: If every attempt fails.
    """
    DATA_DIR.mkdir(exist_ok=True)
    archive = DATA_DIR / "orekit-data.zip"

    if archive.exists() and zipfile.is_zipfile(archive):
        return archive

    if archive.exists():
        print(f"discarding invalid {archive.name} ({archive.stat().st_size} bytes)")
        archive.unlink()

    partial = archive.with_name(archive.name + ".part")
    last_error: Exception | None = None

    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            print(f"downloading Orekit data (attempt {attempt}/{DOWNLOAD_ATTEMPTS})…")
            request = urlrequest.Request(OREKIT_DATA_URL, headers={"User-Agent": "helios/0.1"})

            with (
                urlrequest.urlopen(request, timeout=DOWNLOAD_TIMEOUT_S) as response,
                partial.open("wb") as handle,
            ):
                shutil.copyfileobj(response, handle, length=1 << 18)

            if not zipfile.is_zipfile(partial):
                raise ValueError("downloaded file is not a valid zip archive")

            _replace_with_retry(partial, archive)
            print(f"got {archive.name} ({archive.stat().st_size / 1e6:.1f} MB)")
            return archive

        except Exception as exc:  # noqa: BLE001 - any transport failure is worth retrying
            last_error = exc
            _remove_quietly(partial)
            print(f"  failed: {type(exc).__name__}: {exc}")
            if attempt < DOWNLOAD_ATTEMPTS:
                delay = DOWNLOAD_BACKOFF_S * (2 ** (attempt - 1))
                print(f"  retrying in {delay}s")
                time.sleep(delay)

    raise RuntimeError(
        f"could not download Orekit data after {DOWNLOAD_ATTEMPTS} attempts: {last_error}. "
        f"Download {OREKIT_DATA_URL} manually and save it as {archive}."
    )


def _offset_seconds(value: Any) -> float:
    """Coerce an Orekit time offset to a plain float in seconds.

    Orekit 13 returns a ``TimeOffset`` object from ``offsetFromTAI`` where earlier versions
    returned a primitive double. Handle both so this generator is not pinned to one minor
    version.

    Args:
        value: The value returned by an Orekit time-scale offset call.

    Returns:
        The offset in seconds.
    """
    for accessor in ("toDouble", "getSeconds"):
        method = getattr(value, accessor, None)
        if method is not None:
            return float(method())
    return float(value)


def generate() -> dict[str, Any]:
    """Compute TAI-UTC and TT-UTC at each reference epoch using Orekit.

    Orekit expresses time scales as offsets from TAI, so:
        TAI - UTC = -(UTC offset from TAI)
        TT  - UTC = (TT offset from TAI) - (UTC offset from TAI)

    Returns:
        A reference document containing provenance metadata and one entry per epoch.
    """
    import orekit_jpype

    orekit_jpype.initVM()

    archive = ensure_orekit_data()

    from orekit_jpype.pyhelpers import setup_orekit_curdir

    setup_orekit_curdir(str(archive))

    from org.orekit.time import AbsoluteDate, TimeScalesFactory  # type: ignore[import-not-found]

    utc = TimeScalesFactory.getUTC()
    tt = TimeScalesFactory.getTT()

    entries = []
    for iso in EPOCHS_UTC:
        date = AbsoluteDate(iso, utc)
        utc_from_tai = _offset_seconds(utc.offsetFromTAI(date))
        tt_from_tai = _offset_seconds(tt.offsetFromTAI(date))

        entries.append(
            {
                "epoch_utc": iso,
                "tai_minus_utc_s": round(-utc_from_tai, 9),
                "tt_minus_utc_s": round(tt_from_tai - utc_from_tai, 9),
            }
        )

    from org.orekit.utils import Constants  # type: ignore[import-not-found]

    return {
        "_provenance": {
            "source": "orekit",
            "orekit_jpype_version": _package_version("orekit_jpype"),
            "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "generator": "tools/reference/generate_time_scales.py",
            "note": (
                "Independent implementation used as ground truth for astropy time-scale "
                "handling. Regenerate only deliberately; a changed value here means either "
                "Orekit changed or the data bundle did."
            ),
            "orekit_wgs84_mu_m3_s2": float(Constants.WGS84_EARTH_MU),
        },
        "epochs": entries,
    }


def _package_version(name: str) -> str:
    """Return an installed package's version, or a marker if it cannot be determined.

    Args:
        name: Distribution name to look up.

    Returns:
        Version string, or ``"unknown"`` if the package reports none.
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(name)
    except PackageNotFoundError:
        return "unknown"


def main() -> int:
    """Generate the reference file and write it to disk.

    Returns:
        Process exit code.
    """
    document = generate()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

    print(f"wrote {OUTPUT_PATH.relative_to(REPO_ROOT)}")
    for entry in document["epochs"]:
        print(
            f"  {entry['epoch_utc']}  TAI-UTC={entry['tai_minus_utc_s']:>7.3f} s"
            f"  TT-UTC={entry['tt_minus_utc_s']:>7.3f} s"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

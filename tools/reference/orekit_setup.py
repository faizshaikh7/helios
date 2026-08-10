"""Shared Orekit bootstrap for reference generators.

Orekit is the independent implementation used to grade astropy (see .agent/test.md). This
module owns starting its JVM and obtaining its data bundle, so every generator gets the same
hardened download path rather than each rolling its own.

Not importable without a JVM: call `initialise()` before importing any `org.orekit` name.
"""

from __future__ import annotations

import shutil
import time
import zipfile
from pathlib import Path
from urllib import request as urlrequest

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
ARCHIVE_PATH = DATA_DIR / "orekit-data.zip"

# GitLab builds this archive on demand, so it is slow and prone to resetting mid-transfer.
OREKIT_DATA_URL = "https://gitlab.orekit.org/orekit/orekit-data/-/archive/main/orekit-data-main.zip"
DOWNLOAD_ATTEMPTS = 4
DOWNLOAD_TIMEOUT_S = 300
DOWNLOAD_BACKOFF_S = 5


def _replace_with_retry(source: Path, destination: Path, attempts: int = 6) -> None:
    """Move ``source`` onto ``destination``, retrying while the file is locked.

    On Windows a just-written file can stay briefly locked by the antivirus scanner, surfacing
    as ``PermissionError`` WinError 32 on rename. The write itself succeeded, so failing here
    would discard a completed multi-megabyte download.

    Args:
        source: Path to move from.
        destination: Path to move to.
        attempts: How many times to retry before giving up.

    Raises:
        PermissionError: If still locked after every attempt.
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

    Used to clean up partial downloads inside an exception handler. A cleanup failure must never
    replace the original error -- that hides the real cause behind an irrelevant one.

    Args:
        path: File to remove.
    """
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def ensure_orekit_data() -> Path:
    """Download and validate the Orekit data bundle if not already present.

    Orekit needs leap-second tables and Earth orientation parameters that ship separately from
    the library. The bundle is large and gitignored, so it is fetched on demand.

    ``orekit_jpype.pyhelpers.download_orekit_data_curdir`` is deliberately not used: it streams
    straight onto the destination path with no timeout, retry, or validation, so a reset
    connection leaves a truncated file exactly where a valid one belongs and the next run treats
    corrupt data as a cache hit -- Orekit would then load a partial leap-second table while
    reporting nothing wrong.

    Downloads land on a ``.part`` file, are validated as a real zip, and only then promoted.

    Returns:
        Path to the validated archive.

    Raises:
        RuntimeError: If every attempt fails.
    """
    DATA_DIR.mkdir(exist_ok=True)

    if ARCHIVE_PATH.exists() and zipfile.is_zipfile(ARCHIVE_PATH):
        return ARCHIVE_PATH

    if ARCHIVE_PATH.exists():
        print(f"discarding invalid {ARCHIVE_PATH.name} ({ARCHIVE_PATH.stat().st_size} bytes)")
        ARCHIVE_PATH.unlink()

    partial = ARCHIVE_PATH.with_name(ARCHIVE_PATH.name + ".part")
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

            _replace_with_retry(partial, ARCHIVE_PATH)
            print(f"got {ARCHIVE_PATH.name} ({ARCHIVE_PATH.stat().st_size / 1e6:.1f} MB)")
            return ARCHIVE_PATH

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
        f"Download {OREKIT_DATA_URL} manually and save it as {ARCHIVE_PATH}."
    )


def initialise() -> Path:
    """Start the Orekit JVM and load its data bundle.

    Must be called before importing any ``org.orekit`` name, since JPype can only resolve Java
    packages once a JVM is running.

    Note that reference generation writes shared files under ``data/`` and is **not safe to run
    concurrently** -- two generators racing produce file locks that look like OS faults.

    Returns:
        Path to the loaded Orekit data archive.
    """
    import orekit_jpype

    orekit_jpype.initVM()

    archive = ensure_orekit_data()

    from orekit_jpype.pyhelpers import setup_orekit_curdir

    setup_orekit_curdir(str(archive))
    return archive


def package_version(name: str) -> str:
    """Return an installed package's version, or ``"unknown"``.

    Numerical results are only reproducible against known library versions, so the generating
    version belongs in every reference file's provenance.

    Args:
        name: Distribution name to look up.

    Returns:
        Version string, or ``"unknown"``.
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(name)
    except PackageNotFoundError:
        return "unknown"

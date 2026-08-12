"""Generate time-scale reference values using Orekit.

This is the independent implementation that grades astropy. Orekit is a mature, flight-proven
Java library maintained by a different community, written in a different language, from a
different codebase -- so agreement between it and astropy is meaningful evidence, where astropy
checked against astropy would be circular (see .agent/test.md).

Run manually; never in CI. Output is committed to `tests/reference/` so tests compare against a
fixed target and CI needs no JVM:

    uv run python tools/reference/generate_time_scales.py

Epochs deliberately straddle leap-second boundaries, because that is where independent
implementations most often disagree.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import Any

from orekit_setup import REPO_ROOT, initialise, package_version

OUTPUT_PATH = REPO_ROOT / "backend" / "tests" / "reference" / "time_scales.json"

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


def _offset_seconds(value: Any) -> float:
    """Coerce an Orekit time offset to a plain float in seconds.

    Orekit 13 returns a ``TimeOffset`` from ``offsetFromTAI`` where earlier versions returned a
    primitive double. Handle both so this generator is not pinned to one minor version.

    Args:
        value: Value returned by an Orekit time-scale offset call.

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
    initialise()

    from org.orekit.time import (  # type: ignore[import-not-found]
        AbsoluteDate,
        TimeScalesFactory,
    )

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

    return {
        "_provenance": {
            "source": "orekit",
            "orekit_jpype_version": package_version("orekit_jpype"),
            "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "generator": "tools/reference/generate_time_scales.py",
            "note": (
                "Independent implementation used as ground truth for astropy time-scale "
                "handling. Regenerate only deliberately; a changed value here means either "
                "Orekit changed or the data bundle did."
            ),
        },
        "epochs": entries,
    }


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

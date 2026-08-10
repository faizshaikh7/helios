"""Score a solver against the evaluation question set.

Before an agent exists, the useful thing to measure is the **tool ceiling**: if every question
were routed to exactly the right tool with exactly the right parameters, how accurate would the
system be? That number is the upper bound on anything the agent can achieve, and establishing it
early de-risks the whole plan -- if the tools cannot hit the ground truth, no amount of clever
prompting will.

It is also a real differential test in its own right. The tools compute with skyfield; the ground
truth came from Orekit. A high ceiling means two independent implementations agree across 157
questions spanning five orbital regimes.

Once the agent lands it becomes the second solver here, and the gap between the two is the cost
of tool selection -- attributable, rather than tangled up with numerical error.

    uv run python tools/eval/run_eval.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from science import catalog, elements, orbit

DATASET_PATH = REPO_ROOT / "eval" / "questions.json"
FIXTURES_PATH = REPO_ROOT / "eval" / "tle_fixtures.json"
RESULTS_PATH = REPO_ROOT / "eval" / "results.json"


class Solver(Protocol):
    """Something that answers an evaluation question."""

    name: str

    def answer(self, question: dict[str, Any]) -> float | None:
        """Return a numeric answer, or None if the solver declines.

        Declining is a first-class outcome, not a failure to paper over: a system that says "I
        cannot compute this" is behaving better than one that guesses, and the scoring keeps the
        two apart.
        """
        ...


def _tle_for(norad_id: int) -> catalog.TLE:
    """Load a frozen fixture element set into the catalog cache.

    The evaluation must never depend on live data: ground truth was computed from these exact
    element sets, so answering with a fresher TLE would score a correct method as wrong.
    """
    fixtures = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))["satellites"]
    entry = next(item for item in fixtures if item["norad_id"] == norad_id)

    tle = catalog.TLE(
        name=entry["name"],
        line1=entry["line1"],
        line2=entry["line2"],
        norad_id=entry["norad_id"],
        source="eval-fixture",
        fetched_at_unix=0.0,
    )
    catalog.seed_cache(tle)
    return tle


class ToolCeilingSolver:
    """Routes each question to the correct tool by category.

    This is deliberately not an agent: category dispatch is perfect by construction, so what it
    measures is the tools' numerical agreement with independent ground truth, isolated from any
    question of whether a model picks the right tool.
    """

    name = "tool-ceiling"

    def answer(self, question: dict[str, Any]) -> float | None:
        """Compute an answer for a question, or None if the category is unsupported."""
        category = question["category"]
        context = question["context"]
        tle = _tle_for(context["norad_id"])

        epoch_iso = context.get("epoch_utc")
        when = datetime.fromisoformat(epoch_iso).replace(tzinfo=UTC) if epoch_iso else None

        if category == "subpoint_latitude":
            return float(orbit.subpoint(tle, when)["latitude"].value)

        if category == "subpoint_longitude":
            return float(orbit.subpoint(tle, when)["longitude"].value)

        if category == "altitude":
            return float(orbit.subpoint(tle, when)["altitude"].value)

        if category == "speed":
            state = elements.state_vector(tle, when)
            return float(state["speed"].value) / 1000.0

        if category == "orbital_period":
            reference = orbit.tle_epoch(tle)
            seconds = float(elements.derived_orbit_properties(tle, reference)["orbital_period"].value)
            return seconds / 60.0

        if category == "inclination":
            reference = orbit.tle_epoch(tle)
            return float(elements.classical_elements(tle, reference)["inclination"].value)

        if category in ("pass_count", "max_elevation"):
            station = orbit.GroundStation(
                name=context["station"]["name"],
                latitude_deg=context["station"]["lat"],
                longitude_deg=context["station"]["lon"],
                elevation_m=context["station"]["alt_m"],
            )
            start = datetime.fromisoformat(context["from_utc"]).replace(tzinfo=UTC)
            found = orbit.find_passes(
                tle,
                station,
                start,
                days=float(context["days"]),
                min_elevation_deg=float(context["min_elevation_deg"]),
            )

            if category == "pass_count":
                return float(len(found))
            return float(found[0].max_elevation_deg) if found else None

        return None


def score(solver: Solver, questions: list[dict[str, Any]]) -> dict[str, Any]:
    """Run a solver over the question set and score it.

    Longitude is compared with wraparound: -179.9 and 180.1 differ by 0.2 degrees, not 360.

    Args:
        solver: The system under test.
        questions: The evaluation questions.

    Returns:
        A results document with per-category and overall accuracy.
    """
    results = []
    by_category: dict[str, list[bool]] = defaultdict(list)

    for question in questions:
        expected = question["answer"]["value"]
        tolerance = question["answer"]["tolerance_abs"]

        try:
            produced = solver.answer(question)
            error: str | None = None
        except Exception as exc:  # noqa: BLE001 - a solver crash is a scoreable outcome
            produced, error = None, f"{type(exc).__name__}: {exc}"

        if produced is None:
            correct, delta = False, None
        else:
            delta = abs(produced - expected)
            if question["category"] == "subpoint_longitude":
                delta = min(delta, 360.0 - delta)
            correct = delta <= tolerance

        by_category[question["category"]].append(correct)
        results.append(
            {
                "id": question["id"],
                "category": question["category"],
                "memorizable": question["memorizable"],
                "expected": expected,
                "produced": produced,
                "abs_error": round(delta, 6) if delta is not None else None,
                "tolerance": tolerance,
                "correct": correct,
                "declined": produced is None and error is None,
                "error": error,
            }
        )

    correct_total = sum(1 for r in results if r["correct"])
    unmemorizable = [r for r in results if not r["memorizable"]]

    return {
        "solver": solver.name,
        "run_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "total": len(results),
        "correct": correct_total,
        "accuracy": round(correct_total / len(results), 4) if results else 0.0,
        "accuracy_unmemorizable": (
            round(sum(1 for r in unmemorizable if r["correct"]) / len(unmemorizable), 4)
            if unmemorizable
            else 0.0
        ),
        "declined": sum(1 for r in results if r["declined"]),
        "by_category": {
            category: {
                "n": len(flags),
                "correct": sum(flags),
                "accuracy": round(sum(flags) / len(flags), 4),
            }
            for category, flags in sorted(by_category.items())
        },
        "results": results,
    }


def main() -> int:
    """Score the tool-ceiling solver and write results.

    Returns:
        Process exit code.
    """
    if not DATASET_PATH.exists():
        print(
            "eval/questions.json missing - run tools/eval/generate_questions.py first",
            file=sys.stderr,
        )
        return 1

    questions = json.loads(DATASET_PATH.read_text(encoding="utf-8"))["questions"]
    report = score(ToolCeilingSolver(), questions)

    RESULTS_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print(f"solver: {report['solver']}")
    print(f"  overall        {report['correct']}/{report['total']}  ({report['accuracy']:.1%})")
    print(f"  unmemorizable  {report['accuracy_unmemorizable']:.1%}")
    print(f"  declined       {report['declined']}")
    print("\n  by category:")
    for category, stats in report["by_category"].items():
        print(f"    {category:22} {stats['correct']:>3}/{stats['n']:<3} {stats['accuracy']:>7.1%}")

    failures = [r for r in report["results"] if not r["correct"]][:8]
    if failures:
        print("\n  first failures:")
        for item in failures:
            print(
                f"    {item['id']:28} expected {item['expected']:>12.4f}  "
                f"got {item['produced']!s:>14}  err {item['abs_error']}"
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())

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

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib import error as urlerror
from urllib import request as urlrequest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from science import catalog, elements, ephemeris, orbit

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

        # Planetary questions have no satellite, so this must come before the catalog lookup
        # below -- which would otherwise raise on a missing norad_id rather than answer.
        if category == "earth_distance":
            when = datetime.fromisoformat(context["epoch_utc"]).replace(tzinfo=UTC)
            apparent = ephemeris.apparent_from_earth(context["body"], when)
            return float(apparent["distance"].value)

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


RATE_LIMIT_ATTEMPTS = 6
DEFAULT_RETRY_WAIT_S = 30.0


def _is_rate_limited(detail: str) -> bool:
    """Detect a provider quota rejection inside an error body."""
    lowered = detail.lower()
    return "quota" in lowered or "rate limit" in lowered or "429" in lowered


def _retry_after_seconds(detail: str) -> float:
    """Extract the provider's own suggested wait, falling back to a fixed pause.

    Providers state how long to wait; honouring that is both faster and politer than a guess.
    """
    match = re.search(r"retry in ([0-9.]+)s", detail, re.IGNORECASE)
    if match:
        # A small margin, since the quota window is measured on their clock, not ours.
        return float(match.group(1)) + 2.0
    return DEFAULT_RETRY_WAIT_S


class AgentSolver:
    """Asks the deployed agent, in either grounded or baseline mode.

    Both modes run the **same model**; only tool access differs. Holding the model fixed is what
    makes the comparison mean anything -- otherwise a difference could be capability rather than
    grounding.

    The agent is asked for a bare number. Parsing a figure out of prose would introduce a second
    failure mode (extraction) on top of the one being measured, and would penalise a verbose but
    correct answer.
    """

    def __init__(
        self,
        base_url: str,
        mode: str,
        provider: str | None = None,
        delay_s: float = 0.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.mode = mode
        self.provider = provider
        self.delay_s = delay_s
        self.name = f"agent-{mode}" + (f"-{provider}" if provider else "")

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST to the agent, waiting out provider rate limits.

        Free-tier quotas are per minute, and a grounded question costs several model calls
        because of the tool loop -- so a naive run saturates the quota within seconds and every
        subsequent question fails. Those failures would be scored as wrong answers, which would
        silently understate the system's accuracy and make the chart a lie.

        The provider states how long to wait; that value is used rather than a guess.
        """
        request = urlrequest.Request(
            f"{self.base_url}/api/agent",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

        for attempt in range(1, RATE_LIMIT_ATTEMPTS + 1):
            try:
                with urlrequest.urlopen(request, timeout=600) as response:
                    return json.loads(response.read().decode("utf-8"))

            except urlerror.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")

                if not _is_rate_limited(detail) or attempt == RATE_LIMIT_ATTEMPTS:
                    raise

                wait = _retry_after_seconds(detail)
                print(f"    rate limited; waiting {wait:.0f}s", flush=True)
                time.sleep(wait)

        raise RuntimeError("unreachable")

    def answer(self, question: dict[str, Any]) -> float | None:
        """Ask the agent and parse a numeric answer, or None if it declined."""
        unit = question["answer"]["unit"]
        prompt = (
            f"{question['question']}\n\n"
            f"Respond with ONLY the numeric value in {unit}. No units, no words, no explanation. "
            f"If you cannot determine it, respond with exactly: UNKNOWN"
        )

        payload: dict[str, Any] = {"question": prompt, "mode": self.mode}
        if self.provider:
            payload["provider"] = self.provider

        if self.delay_s:
            time.sleep(self.delay_s)

        body = self._post(payload)

        text = (body.get("answer") or "").strip()
        if "UNKNOWN" in text.upper():
            return None

        # Take the first number in the reply. The prompt asks for a bare value, so anything more
        # is the model ignoring instructions -- but scoring the first figure is fairer than
        # scoring nothing.
        match = re.search(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", text.replace(",", ""))
        return float(match.group()) if match else None


def _summarise(solver_name: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the results document from scored answers.

    Args:
        solver_name: Name of the system under test.
        results: One entry per scored question.

    Returns:
        A results document with per-category and overall accuracy.
    """
    by_category: dict[str, list[bool]] = defaultdict(list)
    for item in results:
        by_category[item["category"]].append(item["correct"])

    correct_total = sum(1 for r in results if r["correct"])
    unmemorizable = [r for r in results if not r["memorizable"]]

    return {
        "solver": solver_name,
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


def score(
    solver: Solver,
    questions: list[dict[str, Any]],
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Run a solver over the question set and score it.

    Longitude is compared with wraparound: -179.9 and 180.1 differ by 0.2 degrees, not 360.

    Results are checkpointed after **every** question and already-scored questions are skipped on
    a rerun. A full grounded pass takes tens of minutes against a rate-limited free tier, and
    losing all of it to an interruption -- a killed process, a dropped connection, a laptop lid --
    means the measurement never gets made. Resumability is what makes a long evaluation
    practical rather than a thing you keep almost finishing.

    Args:
        solver: The system under test.
        questions: The evaluation questions.
        output_path: Where to checkpoint. Also the source of prior answers to resume from.

    Returns:
        A results document with per-category and overall accuracy.
    """
    results: list[dict[str, Any]] = []
    answered: set[str] = set()

    if output_path and output_path.exists():
        previous = json.loads(output_path.read_text(encoding="utf-8"))
        # Only carry forward answers that actually landed. A prior error is worth retrying: it
        # was usually a transient rate limit, and keeping it would bake a transport fault into
        # the accuracy figure.
        results = [item for item in previous.get("results", []) if item.get("error") is None]
        answered = {item["id"] for item in results}
        if answered:
            print(f"resuming: {len(answered)} already scored", flush=True)

    remaining = [q for q in questions if q["id"] not in answered]

    for index, question in enumerate(remaining, start=1):
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

        # Checkpoint after every question: an interruption should cost one answer, not the run.
        if output_path:
            output_path.write_text(
                json.dumps(_summarise(solver.name, results), indent=2) + "\n",
                encoding="utf-8",
            )
            if index % 10 == 0 or index == len(remaining):
                print(f"  {index}/{len(remaining)} scored", flush=True)

    return _summarise(solver.name, results)


def main() -> int:
    """Score a solver and write results.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(description="Score a solver against the evaluation set.")
    parser.add_argument(
        "--solver",
        choices=["ceiling", "grounded", "baseline"],
        default="ceiling",
        help=(
            "ceiling: tools with perfect selection (no model). "
            "grounded: the agent with tools. "
            "baseline: the same model with no tools."
        ),
    )
    parser.add_argument("--provider", default=None, help="Model provider for agent solvers.")
    parser.add_argument("--base-url", default="http://localhost:3002", help="Where the app runs.")
    parser.add_argument("--limit", type=int, default=None, help="Score only the first N questions.")
    parser.add_argument(
        "--fresh",
        action="store_true",
        help=(
            "Ignore any checkpoint and score every question again. Required after changing "
            "tool code, since resuming would silently keep answers from the old version."
        ),
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="Seconds to pause between questions, to stay inside provider rate limits.",
    )
    args = parser.parse_args()

    if not DATASET_PATH.exists():
        print(
            "eval/questions.json missing - run tools/eval/generate_questions.py first",
            file=sys.stderr,
        )
        return 1

    questions = json.loads(DATASET_PATH.read_text(encoding="utf-8"))["questions"]
    if args.limit:
        questions = questions[: args.limit]

    solver: Solver
    if args.solver == "ceiling":
        solver = ToolCeilingSolver()
    else:
        solver = AgentSolver(args.base_url, args.solver, args.provider, args.delay)

    output = (
        RESULTS_PATH
        if args.solver == "ceiling"
        else RESULTS_PATH.with_name(f"results-{solver.name}.json")
    )

    if args.fresh and output.exists():
        output.unlink()

    # Passing the path is what enables checkpointing and resume; without it a long run that dies
    # partway loses everything.
    report = score(solver, questions, output)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

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

"""Render the accuracy chart from evaluation results.

This produces the project's headline artifact: how a tool-grounded system compares with the same
model given no tools, on questions whose answers were computed by an independent implementation.

Emits **two SVGs**, light and dark. GitHub's markdown renderer does not reliably honour
`prefers-color-scheme` inside an embedded SVG, but it does honour a `<picture>` element with two
sources -- so two files is the approach that actually works rather than the one that should.

Dependency-free: hand-written SVG rather than a plotting library, because the chart is simple and
adding matplotlib to ship one figure would be a heavy dependency for the deployed service to
carry.

Colours are the validated categorical slots 1 and 2, checked with the dataviz validator in both
modes (worst adjacent CVD delta-E 24.7 light / 26.8 dark, against a target of 8).

    uv run python tools/eval/make_chart.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = REPO_ROOT / "eval"
OUTPUT_DIR = REPO_ROOT / "docs" / "img"

GROUNDED_PATH = EVAL_DIR / "results-agent-grounded-gemini.json"
BASELINE_PATH = EVAL_DIR / "results-agent-baseline-gemini.json"

# Validated categorical slots 1 (blue) and 2 (orange), stepped per mode.
THEMES = {
    "light": {
        "surface": "#fcfcfb",
        "text": "#1a1a19",
        "muted": "#5c5b55",
        "grid": "#e4e3dd",
        "grounded": "#2a78d6",
        "baseline": "#eb6834",
    },
    "dark": {
        "surface": "#1a1a19",
        "text": "#ffffff",
        "muted": "#c3c2b7",
        "grid": "#33322e",
        "grounded": "#3987e5",
        "baseline": "#d95926",
    },
}

WIDTH = 860
HEIGHT = 460
MARGIN = {"top": 96, "right": 24, "bottom": 96, "left": 60}

# Readable names. Raw category keys are for machines.
LABELS = {
    "subpoint_latitude": "Sub-sat\nlatitude",
    "subpoint_longitude": "Sub-sat\nlongitude",
    "altitude": "Altitude",
    "speed": "Speed",
    "pass_count": "Pass\ncount",
    "max_elevation": "Max\nelevation",
    "orbital_period": "Period*",
    "inclination": "Inclination*",
}


def _escape(text: str) -> str:
    """Escape text for inclusion in SVG."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _restrict(document: dict[str, Any], ids: set[str]) -> dict[str, Any]:
    """Recompute a results document over only the given question ids.

    Runs can end up covering slightly different question sets -- a rate-limited run may stop a
    few short. Comparing 153 answers against 157 would silently mix a coverage difference into
    what is presented as an accuracy difference, so every series is restricted to the questions
    that *all* of them answered.

    Args:
        document: A results document.
        ids: The question ids to keep.

    Returns:
        A results document recomputed over the intersection.
    """
    results = [item for item in document["results"] if item["id"] in ids]

    by_category: dict[str, list[bool]] = {}
    for item in results:
        by_category.setdefault(item["category"], []).append(item["correct"])

    unmemorizable = [item for item in results if not item["memorizable"]]
    correct = sum(1 for item in results if item["correct"])

    return {
        **document,
        "total": len(results),
        "correct": correct,
        "accuracy": round(correct / len(results), 4) if results else 0.0,
        "accuracy_unmemorizable": (
            round(sum(1 for i in unmemorizable if i["correct"]) / len(unmemorizable), 4)
            if unmemorizable
            else 0.0
        ),
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


def render(
    grounded: dict[str, Any],
    baseline: dict[str, Any],
    theme: dict[str, str],
    ceiling: dict[str, Any] | None = None,
) -> str:
    """Build the SVG for one theme.

    A grouped bar chart: magnitude comparison across a small set of named categories, which is
    exactly the job bars do best. Both series are direct-labelled as well as legended, so identity
    never rests on colour alone.

    Args:
        grounded: Results document for the tool-grounded run.
        baseline: Results document for the same model without tools.
        theme: Colour tokens for this mode.
        ceiling: Optional tool-ceiling results, drawn as a reference tick.

    Returns:
        Complete SVG markup.
    """
    categories = [key for key in LABELS if key in grounded["by_category"]]

    plot_w = WIDTH - MARGIN["left"] - MARGIN["right"]
    plot_h = HEIGHT - MARGIN["top"] - MARGIN["bottom"]
    slot = plot_w / len(categories)
    bar_w = min(28.0, slot / 3.0)

    font_stack = "system-ui,-apple-system,Segoe UI,sans-serif"
    open_tag = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" font-family="{font_stack}">'
    )

    parts: list[str] = [
        open_tag,
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="{theme["surface"]}"/>',
    ]

    # Title and subtitle carry the finding, so the chart is readable without the surrounding prose.
    overall_g = grounded["accuracy"] * 100
    overall_b = baseline["accuracy"] * 100
    unmem_g = grounded["accuracy_unmemorizable"] * 100
    unmem_b = baseline["accuracy_unmemorizable"] * 100

    parts.append(
        f'<text x="{MARGIN["left"]}" y="34" fill="{theme["text"]}" font-size="17" '
        f'font-weight="600">Accuracy against independently computed ground truth</text>'
    )
    parts.append(
        f'<text x="{MARGIN["left"]}" y="56" fill="{theme["muted"]}" font-size="12.5">'
        f'{_escape(grounded["solver"].split("-")[-1])} '
        f'&#183; {grounded["total"]} questions &#183; ground truth from Orekit'
        f'</text>'
    )
    parts.append(
        f'<text x="{MARGIN["left"]}" y="74" fill="{theme["muted"]}" font-size="12.5">'
        f'overall {overall_g:.0f}% with tools vs {overall_b:.0f}% without &#183; '
        f'on questions that cannot be recalled: {unmem_g:.0f}% vs {unmem_b:.0f}%'
        f'</text>'
    )

    # Gridlines, recessive.
    for pct in range(0, 101, 25):
        y = MARGIN["top"] + plot_h - (pct / 100) * plot_h
        parts.append(
            f'<line x1="{MARGIN["left"]}" y1="{y:.1f}" x2="{MARGIN["left"] + plot_w}" '
            f'y2="{y:.1f}" stroke="{theme["grid"]}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{MARGIN["left"] - 10}" y="{y + 4:.1f}" fill="{theme["muted"]}" '
            f'font-size="11" text-anchor="end">{pct}%</text>'
        )

    for index, category in enumerate(categories):
        centre = MARGIN["left"] + slot * (index + 0.5)
        g_stats = grounded["by_category"][category]
        b_stats = baseline["by_category"].get(category, {"accuracy": 0.0, "n": 0})

        for offset, (stats, colour, series) in enumerate(
            ((g_stats, theme["grounded"], "grounded"), (b_stats, theme["baseline"], "baseline"))
        ):
            value = stats["accuracy"]
            height = max(value * plot_h, 1.5)
            # 2px gap between adjacent fills, per the mark spec.
            x = centre - bar_w - 1 + offset * (bar_w + 2)
            y = MARGIN["top"] + plot_h - height

            # <title> must be a CHILD of the mark; as a sibling it renders no tooltip at all.
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{height:.1f}" '
                f'fill="{colour}" rx="4">'
                f'<title>{_escape(series)} &#183; {_escape(category)}: '
                f'{value * 100:.0f}% of {stats.get("n", 0)}</title>'
                f'</rect>'
            )
            # Direct label: identity and value without needing the legend or an axis lookup.
            if value > 0.06:
                parts.append(
                    f'<text x="{x + bar_w / 2:.1f}" y="{y - 5:.1f}" fill="{theme["muted"]}" '
                    f'font-size="10.5" text-anchor="middle">{value * 100:.0f}</text>'
                )

        # Tool ceiling: what the tools achieve with perfect selection. Drawn as a tick rather
        # than a third bar because it is an upper bound, not a competitor -- the gap between it
        # and the grounded bar is the cost of tool selection, isolated from numerical error.
        if ceiling:
            ceiling_value = ceiling["by_category"].get(category, {}).get("accuracy")
            if ceiling_value is not None:
                y = MARGIN["top"] + plot_h - ceiling_value * plot_h
                parts.append(
                    f'<line x1="{centre - bar_w - 3:.1f}" y1="{y:.1f}" '
                    f'x2="{centre + bar_w + 3:.1f}" y2="{y:.1f}" '
                    f'stroke="{theme["text"]}" stroke-width="1.5" stroke-dasharray="3 2">'
                    f'<title>tool ceiling &#183; {_escape(category)}: '
                    f'{ceiling_value * 100:.0f}%</title>'
                    f'</line>'
                )

        for line_index, line in enumerate(LABELS[category].split("\n")):
            parts.append(
                f'<text x="{centre:.1f}" y="{MARGIN["top"] + plot_h + 20 + line_index * 13}" '
                f'fill="{theme["muted"]}" font-size="11" text-anchor="middle">'
                f'{_escape(line)}</text>'
            )

    # Baseline axis line.
    parts.append(
        f'<line x1="{MARGIN["left"]}" y1="{MARGIN["top"] + plot_h}" '
        f'x2="{MARGIN["left"] + plot_w}" y2="{MARGIN["top"] + plot_h}" '
        f'stroke="{theme["muted"]}" stroke-width="1"/>'
    )

    legend_y = HEIGHT - 34
    for offset, (label, colour) in enumerate(
        (("with tools (grounded)", theme["grounded"]), ("same model, no tools", theme["baseline"]))
    ):
        x = MARGIN["left"] + offset * 210
        parts.append(f'<rect x="{x}" y="{legend_y - 9}" width="11" height="11" fill="{colour}" rx="2"/>')
        parts.append(
            f'<text x="{x + 17}" y="{legend_y}" fill="{theme["text"]}" font-size="12">'
            f'{_escape(label)}</text>'
        )

    if ceiling:
        x = MARGIN["left"] + 2 * 210
        parts.append(
            f'<line x1="{x}" y1="{legend_y - 4}" x2="{x + 11}" y2="{legend_y - 4}" '
            f'stroke="{theme["text"]}" stroke-width="1.5" stroke-dasharray="3 2"/>'
        )
        parts.append(
            f'<text x="{x + 17}" y="{legend_y}" fill="{theme["text"]}" font-size="12">'
            f'tool ceiling</text>'
        )

    parts.append(
        f'<text x="{WIDTH - MARGIN["right"]}" y="{legend_y + 18}" fill="{theme["muted"]}" '
        f'font-size="11" text-anchor="end">'
        f'* control: publicly known, answerable from memory</text>'
    )

    parts.append("</svg>")
    return "\n".join(parts)


def main() -> int:
    """Render both themed charts.

    Returns:
        Process exit code.
    """
    if not GROUNDED_PATH.exists() or not BASELINE_PATH.exists():
        print(
            "Need both result files. Run:\n"
            "  uv run python tools/eval/run_eval.py --solver baseline --provider gemini\n"
            "  uv run python tools/eval/run_eval.py --solver grounded --provider gemini",
            file=sys.stderr,
        )
        return 1

    grounded = json.loads(GROUNDED_PATH.read_text(encoding="utf-8"))
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    ceiling_path = EVAL_DIR / "results.json"
    ceiling = json.loads(ceiling_path.read_text(encoding="utf-8")) if ceiling_path.exists() else None

    # Compare every series over the same questions. A run cut short by a rate limit would
    # otherwise contribute a coverage difference disguised as an accuracy difference.
    # A transport failure -- a rate limit, a dropped connection -- is not a wrong answer, and
    # scoring it as one would understate the system by whatever the provider's quota happened to
    # be that day. Only questions every series actually *answered* are compared.
    def answered(document: dict[str, Any]) -> set[str]:
        return {item["id"] for item in document["results"] if not item.get("error")}

    common = answered(grounded) & answered(baseline)
    if ceiling:
        common &= answered(ceiling)

    grounded = _restrict(grounded, common)
    baseline = _restrict(baseline, common)
    if ceiling:
        ceiling = _restrict(ceiling, common)

    print(f"comparing over {len(common)} questions answered by every series\n")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for mode, theme in THEMES.items():
        path = OUTPUT_DIR / f"accuracy-{mode}.svg"
        path.write_text(render(grounded, baseline, theme, ceiling), encoding="utf-8")
        print(f"wrote {path.relative_to(REPO_ROOT)}")

    print(
        f"\n  grounded  {grounded['correct']}/{grounded['total']} "
        f"({grounded['accuracy']:.1%})  unmemorizable {grounded['accuracy_unmemorizable']:.1%}"
    )
    print(
        f"  baseline  {baseline['correct']}/{baseline['total']} "
        f"({baseline['accuracy']:.1%})  unmemorizable {baseline['accuracy_unmemorizable']:.1%}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

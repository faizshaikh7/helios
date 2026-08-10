# Helios

Orbital mechanics answers computed by real tools — with explicit assumptions, uncertainty,
and sources — instead of guessed by a language model.

Helios is the project. **Cosma** is the platform it is building toward; **Atlas** is the
scientific agent that reasons over it.

> **Status: working, not deployed.** Seven tools, each differential-tested against an
> independent implementation; a tool-calling agent; and an evaluation harness with 157
> questions whose ground truth came from Orekit. Runs locally; no public URL yet. Nothing here
> claims a capability that isn't shipped — where a measurement is incomplete, it says so.

---

## The problem

Ask a frontier model when your satellite passes over a ground station and it will give you a
confident, well-formatted, plausible answer. It is frequently wrong — not garbled, just wrong
by a few hundred kilometres, because the model recalled a number instead of computing one.

Existing tools solve this and introduce a different problem. GMAT and STK compute correctly and
are hostile to use; answering one question means learning a scripting environment. Small
satellite teams and university labs end up in spreadsheets.

## The approach

A language model decides *what to compute*. Validated scientific libraries do the computing.
Every number returned carries where it came from.

- **Typed tools, not improvised code.** Reference frames (TEME/GCRS/ITRF), time scales
  (UTC/TT/TAI), and units are fixed in tested code rather than re-derived per question. This is
  where orbital software fails, and it fails silently.
- **Provenance per value, not per answer.** A single sentence can mix a measured TLE epoch, a
  derived orbital period, and a predicted pass time. Each is labelled separately as observed,
  derived, predicted, or speculative — and a derived value inherits the weakest tier of its
  inputs.
- **Measured, not asserted.** Correctness is checked against an independent implementation
  (Orekit/GMAT), closed-form analytics, and real-world observation — never against the same
  library that produced the answer.

## Accuracy

Measured against **157 questions whose answers were computed by Orekit** — an independent
implementation, never by the code being graded. Seven satellites spanning low Earth orbit,
sun-synchronous, geostationary, medium Earth orbit, and a highly eccentric orbit.

**143 of the 157 cannot be answered from memory.** That constraint is what makes the comparison
mean anything: *"what is the ISS's altitude"* sits in every model's training data, so a set of
publicly-known facts would let a bare model score well without computing anything. Sub-satellite
longitude at `2026-08-13T14:45Z` has to be calculated. The other 14 are a deliberate control —
values a model should get right from recall alone.

| | Overall | On questions that cannot be recalled |
|---|---|---|
| **Tool ceiling** — perfect tool selection | **100%** (157/157) | **100%** |
| **Bare model** — same model, no tools | 12.1% | 9.1% |
| **Grounded agent** | *in progress* | *in progress* |

The tool ceiling is what the tools achieve when every question reaches the right one. It is the
upper bound on anything the agent can reach, so the gap between it and the grounded run is the
cost of tool selection, isolated from numerical error. Reaching 100% also means skyfield and
Orekit — two independent implementations — agree on every question, in every regime.

The bare model scoring 9.1% rather than 0% is expected, not a flaw: it can guess ISS altitude
within 10 km and orbital speed within 0.05 km/s, because those barely vary. It fails everything
genuinely time-dependent.

Spot checks of the grounded agent, each answered by a real tool chain:

| Question | Agent | Ground truth |
|---|---|---|
| ISS sub-satellite latitude at 2026-08-12T00:00Z | 46.870296° | 46.8704° |
| ISS altitude at 2026-08-12T00:00Z | 419.030951 km | 419.031 km |

The full grounded run is paused on a provider free-tier daily quota; it resumes from its
checkpoint rather than restarting. The chart is published here when it completes — including the
categories where the agent loses to its own tools.

### Reproducing it

```bash
# Ground truth, from the independent implementation
uv run python tools/eval/generate_questions.py

# Pin the science service to the same element sets the ground truth used
EVAL_FIXTURES=eval/tle_fixtures.json uv run uvicorn api.main:app --port 8787

uv run python tools/eval/run_eval.py --solver ceiling
uv run python tools/eval/run_eval.py --solver baseline --provider gemini
uv run python tools/eval/run_eval.py --solver grounded --provider gemini
uv run python tools/eval/make_chart.py
```

## Stack

| Layer | Choice |
|---|---|
| Web | Next.js 16, React 19, TypeScript, Tailwind 4 |
| Science | Python 3.13 — astropy, skyfield, sgp4 |
| Orbital data | Celestrak |
| Hosting | Vercel (one project, both runtimes) |

Python owns the science because the validated ecosystem lives there and there is no JavaScript
binding to SPICE. The rule throughout is to integrate existing, flight-proven implementations
rather than write competing ones.

## Running locally

Requires Node 24+ and [uv](https://docs.astral.sh/uv/).

```bash
npm install          # web dependencies
uv sync --extra dev  # science dependencies (uv provides Python 3.13)
```

Two processes, in separate terminals — the science service and the web app are separate
runtimes, and the web app proxies `/api` to the service in development:

```bash
npm run dev:api      # FastAPI science service on 127.0.0.1:8787
npm run dev:web      # Next.js on http://localhost:3002
```

The tools work with no configuration. To use the agent, add a provider key to `.env.local`
(see `.env.example`) or run a local Ollama.

Checks:

```bash
npm run typecheck
npm run lint
uv run pytest -q
uv run ruff check .
```

## Current scope

Seven tools, each verified against Orekit before counting as done:

| Tool | Agreement with the independent implementation |
|---|---|
| Satellite lookup | Live Celestrak, cached, with an offline twin for tests |
| SGP4 propagation | 0.06 mm position, 0.0005 mm/s velocity, out to a full day |
| Frame conversion | GCRF→ITRF 2.5 cm; TEME→ITRF 1.12 m, against a 44 km frame-confusion error |
| Orbital elements | a to 1 m, e to 1e-9, angles to 1e-6° |
| State at an instant | Sub-satellite point, altitude, speed |
| Ground-station access | 16/16 pass counts, 15/15 peak elevations |
| Eclipse and beta angle | Conical shadow, umbra and penumbra separated, boundaries to 2 s |

Underneath those, time scales agree with Orekit to under a nanosecond across the 2012 and 2017
leap seconds — because a 69-second confusion between UTC and TT moves a low-orbit satellite
about 500 km along-track, and nothing raises when it happens.

**159 tests**, run on every push.

Not built yet: a public deployment, the Cesium globe, and the sandboxed code path for questions
no fixed tool covers.

## Not for operational use

Helios is a research and educational tool. **Do not use its output for mission operations,
collision avoidance, launch decisions, or any purpose where an error carries physical, legal,
or financial consequence.** Answers depend on third-party orbital data of varying freshness and
accuracy, on models with stated and unstated assumptions, and on a language model's choice of
what to compute. Verify independently before acting on anything here.

## Licence

[Apache License 2.0](LICENSE). Copyright © 2026 Faiz Shaikh.

Provided "as is", without warranty of any kind, express or implied — see sections 7 and 8 of
the licence.

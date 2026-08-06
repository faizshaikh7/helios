# Helios

Orbital mechanics answers computed by real tools — with explicit assumptions, uncertainty,
and sources — instead of guessed by a language model.

Helios is the project. **Cosma** is the platform it is building toward; **Atlas** is the
scientific agent that reasons over it.

> **Status: early. Milestone M0 (skeleton).** The tool layer, the agent, and the accuracy
> evaluation described below are not built yet. This README states what exists today and what
> is planned; nothing here claims a capability that isn't shipped.

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

**Not yet measured.** The evaluation — this system against a bare frontier model over ~150
questions with independently sourced ground truth — is milestone M4. The chart will be
published here, including the cases where this system loses.

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

npm run dev          # http://localhost:3000
```

Checks:

```bash
npm run typecheck
npm run lint
npm run test:py
```

## Current scope

Milestone M0 delivers the skeleton: both runtimes deployed together, CI running, and a health
endpoint that verifies the scientific stack imported and converts time scales correctly in the
deployed environment.

Next: an independent ground-truth source (M1), then six tools — satellite lookup, propagation,
frame and element conversion, ground-station access windows, ground track, eclipse and beta
angle — each differential-tested before it counts as done (M2).

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

# Track-Progress Reconstruction (Increment #3) — Design

**Date:** 2026-06-08
**Status:** Approved for planning (standing approval; completion-% definition confirmed by the user).
**Builds on:** Increments #1–#2. Adds the "average completion before reset" broadcast stat.

## 1. Goal

Estimate **how far through a course's route** a player got before resetting — a "race-position %" (0–100%) — and expose its average per course / player / period.

**Product decision (confirmed):** it's a *position along the actual race route*, **not** an assumption of N identical laps (MKW courses vary — intro sections, non-repeating layouts). So the route comes from a real finished run's trail, and a reset's completed-lap count places it on the correct portion of that route using the reference run's own per-lap timing (no "3 equal laps" assumption).

## 2. Scope

**In:** a per-course reference-path builder, a projection that turns a reset's last trail point into a race-position fraction, and one metric `avg_completion_before_reset` exposed through `/v1/stats/value|breakdown`.

**Out:** rendering, per-run exposure beyond the average (can add later), and any use outside resets. No schema changes (uses `run_points` + `run_laps`).

## 3. Algorithm

### 3.1 Reference path (per course, cc)
- Pick the **reference run**: the `finished` run for that `(course, cc)` with the **most trail points** (densest, most complete route). Live-only in practice (imports have no points).
- Order its `run_points` by `t_ms`. Compute cumulative Euclidean arc length over `(cx, cy)`; normalise to `s ∈ [0, 1]`. Keep each point's `t_ms`. Result: `RefPt[] = {cx, cy, s, t}`.
- Compute **lap-boundary fractions**: from the reference run's `run_laps` (ordered by `lap_index`), cumulative lap end-times `T_1 … T_refLaps`; for each `T_k`, the boundary `S_k` = `s` of the reference point nearest `t = T_k`. `S_0 = 0`. These mark where each lap ends *along the route* — correct even for unequal laps.

### 3.2 Completion of one reset
- Inputs: the reset's completed-lap count `L = COUNT(run_laps)` and its **last** trail point `P = (cx, cy)` (max `t_ms`).
- `lowerS = S_L` (route fraction already guaranteed by the `L` full laps; `S_0 = 0` when `L = 0`).
- `completion = s` of the reference vertex **nearest** to `P` among vertices with `s ≥ lowerS`. (Searching only at/after the completed-lap boundary disambiguates which lap of a looping route the reset was on.)
- Resets with **no trail points** are **unevaluable** (excluded; reported as a count). Resets where the course has **no reference** (no finished run with points yet) are also unevaluable.

### 3.3 Aggregate
`avg_completion_before_reset` = `AVG(completion)` over the in-scope `status='reset'` runs (filtered by player/course/cc and the period on `ended_at`), grouped by the requested dimension. Value is a fraction `0–1` (the client renders %). `null` when no evaluable resets.

## 4. Code

New `pi/src/stats/completion.ts`:
- `buildReference(points: {cx,cy,t_ms}[]): RefPt[]` — arc-length normalisation (pure).
- `lapBoundaries(ref: RefPt[], cumulativeLapMs: number[]): number[]` — `S_k` per lap (pure).
- `completionFraction(ref: RefPt[], lowerS: number, p: {cx,cy}): number` — nearest vertex at/after `lowerS` (pure).
- `resolveCompletion(db, { metric, filters, groupBy, period, seasonId })` — wires DB reads to the pure functions, caches the reference per course within the call, aggregates, returns the standard `StatResult` (with `unevaluable`).

Registry (`metrics.ts`): a new metric kind `completion` with `{ id: 'avg_completion_before_reset', kind: 'completion', dimensions: ['player','course','cc'] }`. `allowsDimension` → player/course/cc. Route dispatch (`stats.ts`): `kind === 'completion'` → `resolveCompletion`.

## 5. Testing (`pi/src/stats/completion.test.ts`)

Pure-function units with synthetic trails:
- `buildReference`: a straight 0→10 line of points → `s` evenly 0…1; arc length correct.
- `lapBoundaries`: a 2-lap reference (points with t_ms across two laps) → `S_1 ≈ 0.5` for equal laps, and a skewed lap → boundary follows the *time*, not an even split.
- `completionFraction`: nearest vertex respects `lowerS` (a point matching both lap 1 and lap 2 positions returns the lap-2 fraction when `lowerS` is past lap 1).

DB-level (`resolveCompletion`):
- One course: a finished reference run (trail over 2 laps) + a reset that completed 1 lap and stopped halfway through lap 2 → completion ≈ 0.75; average reflects it.
- A reset with no trail → counted in `unevaluable`, excluded from the average.
- No finished reference → `avg_completion_before_reset` is `null`, resets all `unevaluable`.

Route test (`stats.test.ts`, append): `/v1/stats/value?metric=avg_completion_before_reset&course=…` returns a fraction; `/v1/stats/metrics` lists it with `dimensions:['player','course','cc']`.

## 6. Caveats (documented in code)

- Forward-looking: needs at least one finished run **with a trail** per course for a reference; pre-cutover history has no points.
- Nearest-**vertex** (not nearest-point-on-segment) projection — adequate for a broadcast estimate at trail density (~thousands of points/run); can refine to segment projection later.
- A reset that self-crosses heavily within a lap could mis-snap; the `lowerS` lap gating bounds the error to within one lap.

## 7. Roadmap position

Increment #3 of 5. Remaining: #4 screen-time telemetry (needs engine-side capture), #5 race×body analytics.

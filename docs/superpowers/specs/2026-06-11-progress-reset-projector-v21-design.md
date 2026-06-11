# Progress reset + projector v2.1

**Date:** 2026-06-11
**Status:** approved (user: wipe ALL previous runs, proceed with projector
refinements + measured tuning), building
**Context:** the v2 progress system (per-lap distance models + windowed
projection) is architecturally sound, but its stored models were built from
pre-upgrade trails carrying three input defects fixed earlier today: a
cross-clock skew in in-lap fractions (old trail timebase vs race-clock
`run_laps`), pre-badge position jitter + retroactive-filter interpolation,
and near-uniform scores used as centroid weights. All existing runs are test
data; the user chose to delete them rather than salvage.

## A. Wipe all recorded runs (user decision)

`pi/src/scripts/wipeRuns.ts` (`npm run wipe-runs -- --confirm`):
`DELETE FROM runs` (run_laps/run_points cascade), `DELETE FROM
course_models`, `DELETE FROM player_alignment`. Preserves players, seasons,
rosters, courses, world_records, screen_intervals. Prints per-table counts
before/after. Refuses without `--confirm`. Executed once on `pi/mkw.db` as
part of this work. (Side effect: closes the historical 8->1-damaged-totals
audit by deletion; PBs/leaderboards restart clean on the new digit reader.)

## B. Models rebuild themselves from now on

Manual `build-course-model` stays, but uploads should heal the models
without operator action:

- Extract the build-from-db logic of `scripts/buildCourseModel.ts` into
  `rebuildCourseModel(db, courseId, cc, window=40)` in `db/courseModels.ts`
  (loads latest <=window finished runs with points, builds, saves model +
  alignments; returns a small summary or null).
- `POST /v1/runs` (api/runs.ts): after a **finished** upload that carries
  points, call `rebuildCourseModel` for that (course, cc), then invalidate
  the presence hub's cached model so the very next frame projects on the
  fresh model. Build cost is negligible at this scale and uploads are
  ~once-per-race events.
- Cache invalidation: `makeLiveCompletion` currently caches models forever.
  Its return gains `invalidate(courseId)`; `server.ts` passes it to
  `runsRoutes`.

## C. Projector v2.1: self-paced reach + bounded glide (the timer's gift)

`progress/project.ts`, behind the same `projectStep` seam:

- **Pace estimate**: `ProjState` gains `rate` (within-course completion per
  ms), an EMA (alpha 0.2) updated on each confident step with `dt > 0`,
  clamped to >= 0. No DB dependency - the player's own live pace.
- **Forward reach: unchanged.** (The planned time term was dropped during
  implementation: nearest-point projection moves at most as far as the
  position does, and `movedPx` is measured from the last *confirmed* fix, so
  the movement window already self-widens across gaps. A time term could
  never admit a match the movement term excludes.)
- **Bounded glide instead of freeze, with a monotonic display floor**: on
  the hold paths (stale, no-match) with a known rate, completion = held
  progress + `rate * min(dt, GLIDE_MAX_MS=2000)`. The anchor
  (`state.progress`) is not advanced, so re-acquisition still projects from
  the last confirmed fix - and the new `pub` field (last published
  completion) floors every subsequent publish, so the bar never snaps
  backward when reality (the re-acquired projection) is behind the glide;
  it holds until the kart catches up. This matches the projector's existing
  no-backward semantics (EPS_BACK) and also smooths the lap-seam re-seed.
  Its real payoff is off-model excursions (shortcuts beyond MATCH_DIST):
  the bar glides through them at pace instead of freezing for seconds.
  Rate unknown -> freeze exactly as today. The `!pos` path in
  presence/completion.ts (state deleted) is unchanged.

Rejected again for the record: time-only completion (`elapsed/expected`) -
smoother but positionally dishonest; distance stays the backbone, time is a
prior.

## D. Measured tuning (clip-derived trails, cross-validated)

- `temp/trail_lab.py` (engine side): replays bootest.mp4 + koops.mp4 through
  the production stack (badge tracker -> race-clock recorder + LapTracker +
  RaceTimer + FinishLatch) and writes one `RunInput` JSON per clip - exactly
  what the builder consumes, from today's pipeline. (short.mp4 starts
  mid-race - unusable for folding, skipped.)
- `pi/src/scripts/projectorLab.ts` (`npm run projector-lab`): loads the
  JSONs, cross-validates on KTB (model from one run, project the other),
  replays at 15Hz, reports per-config: monotonicity violations, dCompletion
  p50/p99, held-frame %, final completion. Sweeps `MATCH_DIST`
  {60, 40, 30} x `K_T` {1.5, 2.5, 4.0}.
- Constants are locked from the sweep (committed with the numbers in the
  message); defaults stay if the sweep shows no clear win.

## Acceptance

- Wipe executed; counts reported; pi suite green on an empty-runs DB.
- Upload of a finished run rebuilds the model and the hub projects on it
  without restart (test: upload -> model row exists -> completion non-null).
- Projector: vitest covers rate-EMA, widened reach, glide cap, freeze
  fallback; cross-validated KTB replay shows no monotonicity regressions and
  fewer/equal held frames vs v2 constants.
- Suites: pi vitest + engine pytest green (engine untouched except
  trail_lab in temp/).

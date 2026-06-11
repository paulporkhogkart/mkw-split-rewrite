# Race clock unification: trails on race time, fast finish latch, near-real-time cards

**Date:** 2026-06-11
**Status:** awaiting review
**Builds on:** the badge tracker (2026-06-11 spec) and the friend-card live
timer (RaceTimer -> presence `elapsed_ms` -> `raceTimerBuffer.js`).

## Problem

Four coupled gaps, surfaced after the badge tracker made positions trustworthy:

1. **Trails lose ring_only stretches.** `MinimapRecorder.update` records every
   published point, but `retroactive_filter()` (recorder.py) runs at
   calibration time and replaces every point scoring below the *confident*
   threshold with straight-line interpolation. That guard existed because the
   old scorer's low-score positions were garbage. Badge positions are accurate
   across the whole accepted band (koops: pixel-stable positions in
   `ring_only`), so the filter now destroys good data.
2. **Trail time is not race time.** The recorder stamps points with its own
   stopwatch started on RACING entry (countdown start), so trail `t=0` is
   ~3.5s before GO, and the trail clock is unrelated to the timer the cards
   show. `RaceTimer` already produces the right clock (HUD-digit-anchored,
   never-backward through the lap flash, pause-aware).
3. **Finish detection waits for pixel stillness.** `FinishStillDetector`
   latches after the timer digits stay still STILL_SECONDS (2.5 committed;
   0.6 in the working tree). The freeze VALUE is never read, only its
   stillness.
4. **The friend-card timer/bar runs DELAY_MS behind** (2500 committed / 600 in
   tree) purely so the displayed time lands on the final total instead of
   overshooting; the delay must equal finish-detection latency, so it shrinks
   only if (3) does.

The uncommitted working-tree tweaks (STILL_SECONDS 2.5->0.6, DELAY_MS
2500->600) are superseded and absorbed by this design.

## Design

### A. Delete the retroactive trail filter

Remove `MinimapRecorder.retroactive_filter` and its three call sites (two in
`main.py` calibration blocks, one in `lifecycle/race.py:finalize`). Points are
already gated upstream by the badge accept floor (0.45): if a point was
published, its position is trustworthy. Ring_only stretches stay in trails.
(Decision: delete outright, not re-gate at 0.45.)

### B. Stamp trail points with the race clock

`MinimapRecorder` stops keeping its own stopwatch and pause bookkeeping.
`main.py` passes the current `race_elapsed` (RaceTimer output, already
computed every frame) into `mm_rec.update(mm_state, lap, race_ms)`:

- `race_ms is None` (timer not yet anchored - countdown + ~0.5s after GO):
  buffer the point as `(perf_now, cx, cy, score, lap)` in a small pending list
  instead of recording.
- On the first non-None `race_ms` (received at `now_perf`): **back-stamp** the
  pending points (`t = race_ms - (now_perf - point_perf) * 1000`), keep those
  with `t >= 0`, drop the countdown remainder. The trail's first point lands at
  ~0:00.000 on the start line. (Decision: backfill to GO.)
- Afterwards: record `(race_ms, cx, cy, score, lap)` directly, with a
  monotonic guard (skip if `race_ms <= last_t`, which also dedupes the frozen
  clock during pauses - `RaceTimer` freezes on non-RACING, so pause handling
  collapses into the timestamp itself; `pause()`/`resume()` on the recorder
  become no-ops and are removed along with `_race_start`/`_paused_total`).

`RaceLifecycle` keeps calling `start()`/`stop()`; `start()` just clears state.

**Consumer note:** stored server trails use the old since-countdown timebase
(~3.5s ahead of race time, varying by countdown length). The monitor renders
trails statically (not time-synced), so mixed timebases have no visible
effect today; any future time-synced ghost playback should treat pre-change
runs as approximate. No migration.

### C. Value-based finish latch (with the pixel detector as fallback)

New `FinishValueLatch` in `race/finish.py`, primary finish signal on the
final lap; the existing `FinishStillDetector` stays as a fallback (covers
footage where the digits fail to template-read, e.g. extreme washout), with
STILL_SECONDS at 0.6. `finish_just_detected = latch OR still`.

Latch rule - on the final lap, read the 6-digit timer (`read_timer_ms`, same
templates as RaceTimer) every 0.05s; latch when ALL of:

1. **N_CONFIRM = 3 consecutive reads are identical** (a running timer's ms
   digit changes every frame; only a frozen timer repeats) -> ~0.10-0.15s
   worst-case latency. Three confirms (not two) because two coincidentally
   identical *misreads* of a running timer is the residual false-positive and
   a false finish finalises the race; the third read costs 50ms.
2. **no lap increment occurred during the streak** (`lap_inc` resets it) -
   covers the first ~300ms after the final-lap crossing, where the frozen
   split can still numerically resemble the cumulative estimate;
3. **the frozen value matches the RaceTimer estimate within +/-300ms**
   (`RaceTimer.tolerance_ms`). This is the main flash-killer: the lap-split
   flash stays frozen on screen for ~6-7s (long after `lap_inc`), but a lap
   duration falls behind the climbing cumulative estimate within a fraction
   of a second and stays rejected. The true finish freeze IS the cumulative
   time and passes.

On latch, the frozen value is the authoritative final total in ms - exposed as
`latch.final_ms`. The existing TimestampTracker burst flow stays (it still
collects splits and the formatted total); `final_ms` is a cross-check/log for
now (YAGNI: don't rewire ts plumbing until something needs it).

Wiring in `main.py`: `finish_just_detected = finish_latch.update(frame,
screen, on_final_lap, lap_inc=lap_inc, estimate_ms=race_elapsed) or
finish_still.update(frame, screen, on_final_lap)` (short-circuit order:
latch first).

### D. Card delay = 100ms

`raceTimerBuffer.js`: `DELAY_MS = 100` (user decision: minimal delay; latch
worst-case latency is ~150ms, so the displayed timer may overshoot the final
total by up to ~50ms for a blink before landing - imperceptible on a spinning
millisecond wheel). Comment updated to cite the latch. Known edge: if the
value latch misses and the 0.6s pixel fallback fires (digit reads failing
during the freeze), the overshoot is up to ~500ms before correcting.

No Rust / pi / server changes: `elapsed_ms` and `completion` semantics through
presence are unchanged; only their display delay shrinks.

## Validation

- **Unit (engine):** latch streak/reset logic on synthetic read sequences
  (running, identical-but-mismatching-estimate [lap flash], identical+matching
  [finish], lap_inc reset, None reads); recorder back-stamping (pending buffer
  -> t>=0 kept, countdown dropped, monotonic guard); filter call sites gone.
- **Offline (real footage):** both validation clips contain real finishes with
  known totals - bootest 1:36.713, koops 1:38.185. A probe script runs the
  latch over the final-lap segment and reports (a) latched value == known
  total, (b) latch latency vs the first frozen frame, (c) no false latch at
  the final-lap crossing flash. Acceptance: exact value match on both clips,
  latency <= 0.2s, zero false latches.
- **Frontend:** existing raceTimerBuffer tests keep passing with DELAY_MS=100.
- **Live:** user race - trail starts on the line at 0:00.000ish, card timer
  ~300ms behind reality, finish lands without snap.

## Out of scope

Time-synced ghost playback of trails; rewiring TimestampTracker to consume
`final_ms`; any server-side trail timebase migration.

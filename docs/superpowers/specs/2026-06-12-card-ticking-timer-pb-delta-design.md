# Player-card ticking timer + live PB delta — design

**Date:** 2026-06-12 · **Branch:** `card-timer-pb-delta`

Two card refinements requested after the race-clock/progress work landed:

1. The card timer only advances when a presence update arrives (~4 Hz), so it
   visibly steps. It should tick like a real timer, using updates only to sync.
2. With live completion % and the race clock both available, show a live
   ahead/behind-PB delta next to the PB readout while racing.

## 1. Ticking timer (frontend only)

**Root cause.** `PlayerPanel` already drives cards at 30 fps, but
`raceTimerBuffer.interpolateAt` *holds at the newest sample* once the render
target (`now − DELAY_MS`) passes it. Presence samples arrive every ~250 ms
(outbound `THROTTLE_MS`), so the display target is almost always past the
newest sample and the timer steps at update cadence.

**Fix: extrapolate past the newest sample.** A race clock advances at exactly
1 ms/ms, so for `target > newest.t` return
`elapsed_ms = newest.elapsed_ms + (target − newest.t)`, capped at
`EXTRAPOLATE_CAP_MS = 4000` past the newest sample (a stalled feed freezes the
timer instead of ticking forever; the cap is ~16 missed frames — the server
sweeps dead sockets offline at 15 s anyway). `completion` keeps hold-at-newest
semantics (the bar's server-side glide + CSS transition already smooth it; the
complaint is only about the timer).

**Monotonic display floor.** New anchors can land slightly *behind* the
extrapolated value (network jitter). A timer that ticks backward across a
second boundary looks broken, so `sampleAt` keeps a per-player floor:
`shown = max(estimate, floor)` — a ≤ jitter-sized stall instead of a backward
tick. If the estimate drops more than `RESET_BACKWARD_MS = 1500` below the
floor, that's a new race/restart (the engine's RaceTimer never regresses
within a race): accept it and reset the floor. `clearBuffer` clears the floor.

**DELAY_MS 100 → 200.** The 100 ms display delay was chosen under
hold-at-newest, where the *displayed sample itself* averaged ~225 ms old — so
the finish-latch window (~150 ms of engine overshoot + network) was absorbed
for free. Extrapolation removes that slack: at 100 ms the timer would overshoot
the latched `final_time` by ~50–130 ms and visibly snap back at the finish.
200 ms restores the slack, keeps the display *fresher on average than today*
(constant 200 ms vs ~225 ms mean), and the finish lands without a jump — the
same outcome the 2026-06-11 tuning aimed for.

Touched: `src/lib/raceTimerBuffer.js` (+ tests). `PlayerCard`/`viewModel`
need no change — they already re-render from `now` at 30 fps.

## 2. Live PB delta (server computes, card displays)

**Semantics.** Standard ghost-delta: `pb_delta_ms = elapsed_ms −
pb_time_at(completion)` where `pb_time_at(c)` is the *earliest* race-clock time
the PB run reached completion `c`. Positive = behind PB (orange `slow`),
negative = ahead (green `fast`). Converges to the exact `final − pb` value at
the line, where the existing finished-state delta (exact, from `final_time`)
takes over.

**Why server-side.** The hub already enriches every ~4 Hz frame with projector
`completion` and `pb_ms`; the course models, PB flags (`runs.is_pb`), trails
(`run_points` on the race clock, t=0=GO, per-point lap stamps) and the
model-rebuild invalidation hook all live in pi. A delta label doesn't need
60 fps — between updates it drifts only by the *pace difference* over 250 ms.
Client-side would duplicate the projector in JS plus a new fetch path for no
accuracy gain. Friends' cards get deltas for free (each vs their own PB).

**Pace curve** (`pi/src/presence/pace.ts`, `makePaceDelta(db, cc=150)`):

- Resolve the player's PB run per call (`pbRunFor`, new in `db/pb.ts`:
  is_pb=1 row → `{id, total_time_ms}`; indexed, trivial at 4 Hz).
- Build once per (course, player) and cache keyed by PB run id: replay the PB
  trail through `projectStep` exactly like the live path — per-point lap stamp
  (fallback: derive from `run_laps` cumulative times, as `stats/completion.ts`
  does), seed lap-ups at progress 0, `stale: false`, **current player
  alignment applied** (the live hub aligns live positions; the curve must live
  in the same frame). Keep only strictly-increasing completion knots — that
  collapses holds to their first timestamp, giving earliest-time inversion.
  Reject curves with < 2 knots or final completion < 0.5 (pathological trail)
  and cache the rejection until the PB run changes.
- Lookup: clamp completion to the curve's range, binary-search, lerp.
- `invalidateCourse(courseId)` drops that course's model + curves; `server.ts`
  wires it into the existing `invalidateModel` callback alongside
  `live.invalidate` (model rebuilds also refresh alignments, so curves must
  re-project). A PB change without a rebuild is caught by the per-call run-id
  check.

**Hub.** `PresenceHub` gains a `pace` collaborator (3rd ctor param, default
`() => null`; existing `now` callers updated). In `update()`:
racing (`screen === 'RACING' && !final_time`) and `completion != null` and
`elapsed_ms != null` → attach `pb_delta_ms`; else `null`. `PresenceEntry` and
`offlineEntry` gain the field. Additive — the only presence consumer is the
Svelte client.

**Card.** `viewModel`: when racing, `delta = liveDelta(e.pb_delta_ms)` —
signed seconds at **full timer precision** (`+0.432` / `-1.260`, matching the
m:ss.SSS timer; user decision 2026-06-12, revised from a one-decimal first
cut), reusing the existing `slow`/`fast` classes and the existing delta slot
next to PB (markup unchanged). The finished delta uses the same 3-decimal
format (was 2-decimal).

**Gates** (delta hidden): no PB, PB run without a usable trail (e.g.
manual-fill uploads), no course model ("calibrating"), no live
completion/elapsed, not racing.

**Accuracy.** Error ≈ completion error ÷ pace, plus glide lag through minimap
dead zones — expect a few tenths of a second transient, exact at the finish
(curve endpoints are pinned to real timestamps). Good enough for an up/down
pace readout.

## Tests

- `raceTimerBuffer.test.js`: extrapolation past newest, cap, floor (no
  backward tick on a late anchor), reset detection, in-window lerp unchanged.
- `pi pace.test.ts`: self-replay delta ≈ 0 across the run; constant-offset
  live run ≈ that offset; earliest-time inversion at a hold; gates (no PB / no
  trail / no model) → null; PB-change + invalidateCourse refresh the curve.
- `hub.test.ts`: `pb_delta_ms` attached when racing with completion+elapsed;
  null when `final_time` set / not racing; pace receives the broadcast
  completion.
- `playerCard.test.js`: racing + `pb_delta_ms` → full-precision delta with
  correct class; finished delta same format; null gates.

Suites: root vitest, `pi` vitest, svelte-check. Engine/Rust untouched.

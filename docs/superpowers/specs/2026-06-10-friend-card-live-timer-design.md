# Live friend-card race timer — design

**Date:** 2026-06-10
**Status:** Approved (design)

## Goal

Show a live, ticking `m:ss.SSS` (full milliseconds) race timer on each friend's
player card during RACING, where the card currently shows `"—"`. Each friend
runs their own app+engine, so each friend's timer is detected locally and shipped
over the existing presence pipeline; the monitor card renders whatever arrives.
Added CPU must be negligible — no per-frame timer reads.

## Background — current state

- The engine's `TimestampTracker` (`mkw_tracker/race/timestamp.py`) reads the
  six-digit timer (`TIMESTAMP_ROIS`, top-right, `A:BC.DEF` = `m:ss.mmm`) by
  **digit template-matching** (`read_digit_roi` slides `images/timestamps/cropped/0.png`–`9.png`
  with `TM_CCOEFF_NORMED`). It only does this in **bursts** triggered by a lap
  crossing or the finish (`capture_now=lap_inc or finish_just_detected` in
  `main.py`). There is **no continuous live timer value**.
- Data flow: engine (Python) → IPC events → frontend `race` store (`src/lib/stores.js`)
  → `presence.js frame()` snapshots stores into a presence frame at ~4 Hz →
  server `/v1/presence` → `PresenceHub` (`pi/src/presence/hub.ts`) builds a
  `PresenceEntry` and broadcasts → monitor `presence` store → `PlayerCard.svelte`
  via `viewModel` (`src/lib/playerCard.js`).
- During RACING the card shows `primary = { kind: "time", text: "—" }`; once
  finished it shows `final_time`. A progress **bar** (`vm.bar.fill` from
  `entry.completion`, computed server-side in `pi/src/presence/completion.ts` from
  the minimap `pos`) is also shown during the race.
- `src/lib/clock.js` exposes `nowTick` (30 s tick) used for offline "last seen"
  recompute — too coarse for a ticking timer, but establishes the shared-tick
  pattern.

## Core idea

Decouple **smoothness** (card-side ticking) from **accuracy** (sparse reads):

1. The engine produces an accurate, continuously-updating *real* elapsed time
   using a free wall-clock counter, anchored and periodically corrected by the
   *existing* digit read — **not** a per-frame read.
2. The value rides the presence pipeline as a new `elapsed_ms` field
   (pass-through, like `coins`/`mushrooms`). Engine and server stay
   authoritative and **un-lagged** (the broadcast graphics and Discord bot read
   the same data and must not inherit a display lag).
3. The monitor card renders the live indicators (timer **and** progress bar)
   delayed by `STILL_SECONDS` (2.5 s) via a small per-player sample buffer, so
   the display "lines up" cleanly at the finish (see below). Screen, selection,
   and `final_time` render immediately.

## Component 1 — Engine: `RaceTimer` (`mkw_tracker/race/timer.py`, new)

A small, isolated, unit-testable class. Produces the engine's best estimate of
the **real** in-game elapsed ms. Reuses the existing digit read machinery
(`load_digit_templates`, `read_digit_roi`, `TIMESTAMP_ROIS`,
`timestamp_digit_threshold`) via a shared helper so nothing is duplicated.

State: `anchor_perf` (perf_counter at last accepted read), `anchor_ms` (elapsed
at that read), `running` (bool), plus a small forward-confirmation buffer and a
`paused` flag.

Per call `update(frame, screen) -> Optional[int]` (returns current elapsed ms, or
`None` before start):

- **Not RACING:** set `paused = True`, freeze (do not advance), return last
  estimate. (`RaceLifecycle._clear_race_state` resets the whole timer on a new
  run / reset; see Component 2.)
- **RACING after a pause/gap:** re-baseline `anchor_perf = now` (keep `anchor_ms`)
  and clear `paused`, so the wall-clock gap accrued while paused is not counted;
  the next due read re-syncs to ground truth.
- **Resync read**, at most every `race_timer_resync_interval` (default **0.5 s**):
  do one digit read of the timer ROIs. Apply the **anchor rule** below. Between
  reads, `elapsed = anchor_ms + (now − anchor_perf)·1000` when `running`.

### Anchor rule (locked)

The cumulative race time only ever rises during a continuous run, so a backward
read is never legitimate — it is either the **~7 s lap-split flash** (on a lap
crossing the timer shows the just-completed lap time, a constant *lower* number,
for ~7 s while colour-flashing gold↔white, then jumps back to cumulative) or a
misread. Therefore:

- **Start:** the first *clean* read (all six digits matched, parses) with
  `ms > 0` sets the anchor and `running = True`. (Self-handles any countdown — we
  anchor whenever the first valid reading lands, and it carries the exact ms.)
- **Backward read** (`read < estimate − TOL`): **ignore.** The local counter is
  authoritative downward and carries the true cumulative time straight through
  the ~7 s flash. (No lap-flash suppression window and no `lap_inc` coupling are
  needed — the flash is simply a long run of ignored backward reads, and the
  read when it ends lands within tolerance and re-anchors.)
- **Within tolerance** (`|read − estimate| ≤ TOL`, `TOL` = `race_timer_tolerance_ms`,
  default **300 ms**): re-anchor to the read (drift correction).
- **Forward read** (`read > estimate + TOL`): ignore unless
  `race_timer_forward_confirm` (default **3**) consecutive reads agree on a
  consistent higher value → then snap forward (recovers a process stall only). A
  1–2 read transient never qualifies.

## Component 2 — IPC + app store + presence frame

- **IPC** (`mkw_tracker/ipc/protocol.py`): add
  `emit_race_time(elapsed_ms: Optional[int]) -> str` →
  `_emit("race_time", elapsed_ms=elapsed_ms)`.
- **Main loop** (`mkw_tracker/main.py`): construct `RaceTimer` alongside the other
  trackers (~L800); in the RACING tracker block (the `not _race_complete`
  branch) call `elapsed = timer.update(frame, screen)` and emit
  `emit_race_time(elapsed)` gated to ~10 Hz (a simple `perf_counter` gate) to
  avoid IPC spam.
- **Lifecycle** (`mkw_tracker/lifecycle/race.py`): accept the `RaceTimer` in the
  constructor and call `self._timer.reset()` in `_clear_race_state()` (alongside
  `self._ts.reset()` etc.). Wire the instance through `main.py`.
- **App** (`src/App.svelte`): handle `case "race_time": elapsedMs = msg.elapsed_ms;`
  and include `elapsedMs` in the `raceStore.set({...})` reactive (~L1396).
- **Store** (`src/lib/stores.js`): add `elapsedMs: null` to the `race` writable
  default.
- **Presence frame** (`src/lib/presence.js` `frame()`): add
  `elapsed_ms: r.elapsedMs`.

## Component 3 — Server: pass-through (`pi`)

Pure pass-through, mirroring `coins`/`mushrooms` — no server-side computation.

- `pi/src/presence/hub.ts`: add `elapsed_ms?: number | null` to `PresenceFrame`;
  add `elapsed_ms: number | null` to `PresenceEntry`; copy
  `frame.elapsed_ms ?? null` into the entry in `update()`; set `elapsed_ms: null`
  in `offlineEntry()`.

## Component 4 — Monitor card: delay buffer + display

### The finish-lineup rationale (the 2.5 s delay)

At the actual finish the real timer freezes on the total, and the finish is only
**confirmed** `STILL_SECONDS` (2.5 s) later (that is exactly what
`FinishStillDetector.STILL_SECONDS` measures — the digits-frozen duration).
During that gap the engine keeps emitting a *climbing* real elapsed (screen is
still RACING), so a naive live timer would tick ~2.5 s past the true total, then
snap down to `final_time` when it arrives.

Fix: render the live display **2.5 s behind real time**. Then the displayed timer
climbs to the total *exactly as* the finished result arrives (which is naturally
~2.5 s after the freeze) — they meet, with no overshoot and no snap. The progress
bar is delayed by the same 2.5 s so it stays locked to the timer and reaches 100%
at the same instant. At the **start**, the display naturally holds for 2.5 s then
begins ("delay the start"), falling out of the same buffer for free.

### Implementation

A single delay mechanism on the card, applied to **only the two live
indicators**:

- New pure module (e.g. `src/lib/raceTimerBuffer.js`), unit-testable: per player,
  keep a rolling buffer of `(rxAt, elapsed_ms, completion)` samples (trim to
  ~`DELAY + 0.5 s` of history; ~12 samples at 4 Hz). Provide
  `sampleAt(buffer, target) -> { elapsed_ms, completion }` that linear-interpolates
  between the two samples bracketing `target`. If `target` is past the newest
  sample (frames stalled / player dropped), hold at the newest (freeze).
- `src/lib/presence.js` (monitor receive path): stamp `_rxAt = Date.now()` on each
  entry as it lands in the store (the *monitor's* local receive time — immune to
  cross-machine clock skew), and append the `(rxAt, elapsed_ms, completion)`
  sample to that player's buffer.
- `DELAY_MS = 2500`, defined on the card side with a comment tying it to
  `FinishStillDetector.STILL_SECONDS` (finish-detection latency). The only
  residual mismatch is network/finalize jitter (tens of ms), tunable later in
  live testing.
- `src/lib/playerCard.js` `viewModel`: for the racing state, read the delayed
  sample at `now − DELAY_MS`; show `fmtTimeMs(delayed.elapsed_ms)` (already full
  ms) instead of `"—"`; drive `bar.fill` from `delayed.completion`. **Screen,
  selection, and `final_time` render immediately** from the current entry (not
  delayed) — that is what makes the finish line up. The `dividers` are static and
  remain immediate.
- **Fast tick:** `src/components/PlayerPanel.svelte` owns one ~30 fps loop
  (`requestAnimationFrame` or `setInterval(~33ms)`) active **only while at least
  one card is racing**; it drives the `now` passed to the cards so the ms digits
  move. Cards keep `nowTick` for offline "last seen". This replaces the earlier
  forward-extrapolation + staleness-freeze design — interpolating *within* known
  samples is simpler and cannot overshoot.

## Edge cases

- **Lap-split flash (~7 s constant lower number, colour-flashing):** ignored as a
  backward read; the local counter carries true cumulative time through it.
- **Finish:** delayed display reaches the total exactly as `final_time` arrives →
  seamless. `final_time` then drives the finished display (existing behaviour).
- **Pause/menu:** engine freezes (non-RACING); card shows the menu state
  immediately; on resume the engine re-baselines and the next read re-syncs.
- **Misread:** within-tolerance reads re-anchor; out-of-tolerance single reads are
  ignored (backward) or unconfirmed (forward).
- **Process stall:** confirmed forward reads snap the anchor forward.
- **Frame stall / player drop:** the buffer holds at the newest sample (freeze);
  the server sweep flips the player offline, which renders immediately.
- **New run / reset / restart:** `RaceLifecycle._clear_race_state()` resets the
  engine timer; a course change / fresh RACING re-anchors from scratch. The card
  buffer is naturally superseded by new samples (and may be cleared on a course
  change).

## Config constants

Add to `mkw_tracker/config/defaults.py` (`Defaults` dataclass; also surfaced in
the `config` table per project convention):

- `race_timer_resync_interval: float = 0.5`
- `race_timer_tolerance_ms: int = 300`
- `race_timer_forward_confirm: int = 3`

`DELAY_MS = 2500` lives on the **card side** (display concern), not Python config,
documented as tied to `FinishStillDetector.STILL_SECONDS`.

## Out of scope (YAGNI)

- No server-side timer math; no DB persistence (purely ephemeral presence).
- No change to the recorded splits / PB / finalize path (authoritative times still
  come from the existing burst captures + `total_time`).
- No per-frame reads. No downward-snap / lap-flash suppression window /
  `lap_inc` coupling (removed — "never go backward" subsumes them).

## Testing

- **Engine** (`tests/`, pytest): `RaceTimer` unit tests with a fake clock +
  scripted reads — start detection (first clean read > 0), local counting between
  reads, backward read ignored (lap-flash), within-tolerance re-anchor, forward
  snap only after confirmation, pause freeze + resume re-baseline.
- **Server** (`pi`, vitest): `hub` pass-through of `elapsed_ms` (present →
  entry; absent → null; offline → null).
- **Frontend** (vitest): `raceTimerBuffer` interpolation (bracketed sample,
  past-newest hold, empty buffer) and `playerCard` `viewModel` racing timer +
  delayed bar (with a fake `now`), finished state still shows `final_time`.

## File-by-file change list

| File | Change |
|------|--------|
| `mkw_tracker/race/timer.py` | **new** `RaceTimer` + shared digit-read helper |
| `mkw_tracker/race/timestamp.py` | (optional) factor the digit read into the shared helper |
| `mkw_tracker/config/defaults.py` | add 3 `race_timer_*` constants |
| `mkw_tracker/ipc/protocol.py` | add `emit_race_time` |
| `mkw_tracker/lifecycle/race.py` | accept + reset `RaceTimer` |
| `mkw_tracker/main.py` | construct `RaceTimer`, update in RACING block, emit at ~10 Hz |
| `src/lib/stores.js` | `race.elapsedMs` default |
| `src/App.svelte` | handle `race_time`, include `elapsedMs` in store set |
| `src/lib/presence.js` | send `elapsed_ms`; stamp `_rxAt` + buffer on receive |
| `src/lib/raceTimerBuffer.js` | **new** pure buffer + interpolation |
| `src/lib/playerCard.js` | delayed timer + bar in `viewModel` |
| `src/components/PlayerPanel.svelte` | one ~30 fps tick while any card racing |
| `pi/src/presence/hub.ts` | `elapsed_ms` on `PresenceFrame`/`PresenceEntry` + pass-through |

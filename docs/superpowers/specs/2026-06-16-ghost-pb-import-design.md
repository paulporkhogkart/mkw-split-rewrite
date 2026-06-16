# Ghost PB Import — Design

Date: 2026-06-16
Status: approved (pending spec review)

## Problem

Since the move off the legacy Google Sheet, there is no way to add a PB manually
when pbenguin was not running at the time the PB was set. Mario Kart World saves a
**ghost** of the player's best run per course — a replay with the same HUD (coins,
mushrooms, lap splits, total time, minimap) as a live race. We can re-derive a run
from a ghost by treating the ghost replay as a custom "racing" state and recording
it exactly as we record a live race, then submitting it as one of the player's runs.

This feature is **destructive and hard to undo server-side**, so it is gated behind
an explicit arm step with a warning, captures **one** ghost per arm, and disarms on
success.

## Constraints / facts established

- `Screen.GHOST` already exists and is detected (aliased to RACING's coin/flag
  tells). `GHOST_RESET` and `REPLAY_MENU` exist. Reachability:
  `START_REPLAY → GHOST`, `REPLAY_MENU → GHOST`, `GHOST_RESET → GHOST`,
  `GHOST → REPLAY_MENU | HOME`.
- **Restart vs. resume screen signatures** (load device for "caught the start"):
  - **First watch**: `COURSE_SELECT → START_REPLAY → GHOST`. `START_REPLAY` reaches
    only `{GHOST, REPLAY_MENU, COURSE_SELECT, HOME}` — no RESET is reachable — so the
    reload is held as `START_REPLAY`; `old == START_REPLAY` at the GHOST entry.
  - **Restart**: `GHOST → REPLAY_MENU → GHOST_RESET → GHOST`. From `REPLAY_MENU`,
    `GHOST_RESET` (the dark `dark_loading` reload) **is** a candidate, so the reload
    is detected; `old == GHOST_RESET` at the GHOST entry.
  - **Resume** ("replay menu closed, ghost continues"): `REPLAY_MENU → GHOST`
    directly — no reload, no dark frame, no `GHOST_RESET`; `old == REPLAY_MENU`.
  So `old ∈ {GHOST_RESET, START_REPLAY}` ⇒ fresh start; `old == REPLAY_MENU` (no
  reset between) ⇒ resume.
- Every race tracker (`laps`, `coins`, `mush`, `timer`, `minimap`, `finish`, `ts`)
  hard-gates on `screen == Screen.RACING`. The minimap recorder + minimap live emit
  are explicitly `if screen == Screen.RACING`.
- `FinishLatch` = `FinishValueLatch` (primary) + `FinishStillDetector` (fallback).
  The value latch needs `N_CONFIRM=3 × READ_INTERVAL=0.05s ≈ 150ms` of a frozen
  timer value to lock the total — comfortably inside the ghost's ~2-3s total-time
  freeze. Both gate on `screen == Screen.RACING`.
- `RaceLifecycle.finalize_on_finish()` already emits the finished run the instant
  `ts.total_time` locks (the timer-freeze), not at the results screen — so a short
  ghost window is fine.
- `RaceLifecycle._finalize_recording` emits `run_finalized` for **every** ended
  attempt; the Rust side (`sync.rs:route_line`) decides ready-vs-held. Incomplete
  runs (missing course/character/kart) → `pending_review` → `run_needs_review` →
  the existing `RunReviewModal` (same sound, two-step Discard, live PB lookup).
- The minimap per-`(course,character,costume)` **confident-score** threshold is
  still used by badge tracking (`tracker.py:270` gates `TRACKING` vs `RING_ONLY`).
- Server `runs` rows have `provenance ∈ ('live','legacy_import','carryover')`.
  Carryover (Season-0 → Season-1 seed) PBs are `finished` with `total_time_ms` but
  **no** character/kart/costume, **no** `run_laps`, **no** `run_points` (trail).
- Presence frames (`src/lib/presence.js:frame()`) are built from the `screen` /
  `race` / `minimap` stores. If the engine does not emit live race events during a
  ghost, the frame carries `screen: "GHOST"` with null race data → friends see only
  the activity label.

## Decisions (from brainstorming)

1. **Course is auto-detected** from the Course Select screen on the way to the ghost
   (Course Select → Start Replay → Ghost), so it is pre-filled and used to seed the
   minimap. Only **character/kart/costume** are unreliable (current loadout may
   differ from the recorded ghost), so they are nulled and entered manually.
2. **Dedup match key** = same **player + course + identical `total_time_ms`** in the
   active season.
3. **Quiet background capture**: no live rail/timer/minimap updates during a ghost
   import, and friends see no fake race via presence. The ghost screens display a
   friendly activity label "Watching a ghost…" instead of "In the menus".
4. **Server log** = a durable, queryable record: mark the run as ghost-sourced AND
   write an audit row (who/when/course/time/enriched-vs-new).

## User-facing flow

1. **Arm.** A title-bar button "Import PB from ghost" (monitor view, by Edit/
   Settings). Off = neutral outline. Click when off → warning modal:

   > When this is turned on, the next ghost you watch will be added as one of your
   > runs. This is very hard to undo on the database end, so please don't misuse it.

   with **"OK, enable"** / **"Cancel"**. "OK, enable" arms; "Cancel" closes. Armed =
   filled accent + a small "armed" dot, so state is obvious from the button itself.
   Click when armed → disarm directly (no modal).
2. **Capture.** The next ghost watched **from its start** is recorded like a race
   (laps, splits, coins, mushrooms, minimap trail, total time). A small "Importing
   ghost…" indicator shows it is working. Nothing is broadcast (quiet capture).
   Resetting/leaving the ghost before it finishes records nothing and waits for the
   next start.
3. **Finalize + review.** On a full finish, the button **disarms**, the run is held,
   and the `RunReviewModal` pops (same sound) with **course pre-filled** and
   **character/kart/costume required**. The total time is pre-filled (auto-captured)
   and editable.
4. **Submit.** A **two-step "Are you sure?"** confirm (ghost runs only) before the
   run is submitted to the outbox → uploaded with `source: "ghost"`.
5. **Server.** Dedup-or-insert (below); PB notification only for a genuinely new run;
   audit row either way.

## Engine design (`mkw_tracker`)

### Effective-screen capture

A single remap is the whole trick. In the main loop:

```python
screen, perf = detector.update(frame)               # emits real screens (GHOST stays GHOST)
eff_screen   = lifecycle.effective_screen(screen)    # GHOST → RACING while recording a ghost
```

- **Trackers** receive `eff_screen`: `laps`, `coins`, `mush`, `timer`, `minimap`,
  `finish`, `ts`, plus `mm_rec.update(...)` and the per-character calibration writes.
  They run identically to a live race and capture everything.
- **Live emits stay on the REAL screen.** `lap_update`, `race_time`, `coin_update`,
  `mush_update`, `minimap_update`, and the `minimap_update` dedup block stay gated on
  `screen == Screen.RACING` (real, not `eff_screen`). During a ghost, `screen` is
  `GHOST`, so none of these fire → presence/rail stay clean.
- `_FULL_RATE_SCREENS` already contains `GHOST`, so frame rate is unaffected.

Rejected alternative: relax every tracker's gate to `{RACING, GHOST}`. That would
make the trackers run on every ghost the user ever watches and entangle the
capture/broadcast split. The single remap keeps the decision in one place and only
active while a ghost import is in progress.

### `GhostImport` state machine (`lifecycle/ghost.py`)

A small, cv2-free, unit-testable state machine owned by `RaceLifecycle`. States:

- `IDLE` — disarmed.
- `ARMED` — waiting for a ghost to start from the beginning.
- `RECORDING` — capturing (with a `validating` sub-flag for the first ~0.5s).

Inputs it observes (fed from the lifecycle / main loop): screen transitions, the
`RaceTimer` estimate (or a raw timer read), and the finish-lock.

Transitions:

- `arm()` → `ARMED`; `disarm()` → `IDLE`. Both clear any in-progress capture.
- **Catch the start.** On a transition **into** `GHOST` (`old != GHOST`) while
  `ARMED`: begin a **provisional** capture (`RECORDING`, `validating=True`),
  `effective_screen` starts returning `RACING`, and `_start_race(old)` runs (seeds
  minimap course-only — see below — and starts the recorder). The **origin screen**
  is the fast-path discriminator: `old ∈ {GHOST_RESET, START_REPLAY}` (a reload
  happened) ⇒ fresh start; `old == REPLAY_MENU` (no reset between) ⇒ likely a resume.
- **Validate the start (restart vs. resume).** Confirm with the race clock as ground
  truth during the validation window (~0.5s / N frames after entry): a fresh start
  is **observed at/near zero** (we witness the countdown/GO) — a `RaceTimer` read ≤
  `START_ZERO_MS` (tunable; tuned against `temp/ghostsample.mp4`). Combined rule:
  - origin ∈ {`GHOST_RESET`, `START_REPLAY`} **or** clock witnessed at ~0 ⇒ keep
    recording (a real restart whose brief `GHOST_RESET` was missed is still rescued
    by its countdown);
  - origin == `REPLAY_MENU` **and** the clock is already advanced and counting ⇒
    **resume**: discard the provisional capture (`_clear_race_state`, no emit),
    return to `ARMED`. A `lap > 1` reading is a secondary reject.
- **Finish.** When `ts.total_time` locks during a validated `RECORDING`, the lifecycle
  finalizes with `source: "ghost"` (below), the controller `disarm()`s → `IDLE`, and
  the engine emits `ghost_import_state {armed: false}`.
- **Abort.** Leaving `GHOST` before the finish (`GHOST → GHOST_RESET | REPLAY_MENU |
  HOME` with `ts.total_time` still `None`) → **discard** (`_clear_race_state`, no
  `run_finalized`), return to `ARMED`.

`effective_screen(real)` returns `RACING` iff `state == RECORDING` and
`real == GHOST`; otherwise returns `real` unchanged (so a real RACING is always a
normal race, even while ghost-import is armed).

### Finalize payload

`RaceLifecycle._finalize_recording` and `finalize_on_finish` learn a `ghost: bool`
(read from the controller). For a ghost finalize the emitted `run_finalized` is the
normal payload plus/minus:

- `source: "ghost"`.
- `character: null`, `kart: null`, `costume: null` (force manual entry — the
  detected loadout may not match the ghost).
- `course`: kept (detected, reliable).
- `total_time`, `laps[]`, `points[]` (trail), coin/mushroom totals: captured normally.
- Skip the minimap threshold calibration write (`calibrate_from_race` /
  `set_minimap_threshold`) for ghost finalizes.

### Minimap seeding for ghosts

In `_start_race`, when the controller is recording a ghost: seed the minimap with the
**course only** — do not pass `character`/`costume` to `get_minimap_threshold(...)`,
and do not set `_calibrated`. The badge still locks from the live frame; only the
persisted per-character calibration is skipped.

### IPC

- Inbound: `set_ghost_import { enabled: bool }` → `lifecycle.arm_ghost(enabled)`.
- Outbound: `emit_ghost_import_state(armed: bool, recording: bool)` — echoed on
  arm/disarm, on recording start/stop, and on auto-disarm at finish, so the button
  reflects engine truth (not optimistic frontend state).

## Rust / outbox (`src-tauri/src/sync.rs`)

Largely free:

- `build_upload_body` already forwards all fields except `type`, so `source: "ghost"`
  rides through to `POST /v1/runs`.
- A ghost run is always incomplete (missing character/kart) → `route_line` →
  `pending_review` → `run_needs_review` with the full `run` embedded, so `source`
  reaches the frontend (drives the popup's ghost behaviour).
- Client PB-cache logic needs no change: a dedup (time == cached best) is not
  `< best`, so it self-consistently fires no client PB; a genuinely faster ghost
  fires one and lowers the cache, matching the server.

## Server (`pi/` + `server/schema.sql`)

`POST /v1/runs` branches when `source === 'ghost'` (active season, `cc`):

- **Match found** — a finished run with the same `player_id`, `course_id`,
  `cc`, and identical `total_time_ms` (e.g. a `carryover` seed):
  **enrich the gaps in place, never overwrite** — fill `character`/`kart`/`costume`
  only where null; insert `run_laps` + `run_points` (the trail) **only if the run has
  none**; set `coins_gained`/`coins_lost`/`mushrooms_used` only where null; set
  `runs.source = 'ghost'`. Rebuild the course model **only if a trail was added**.
  **Do not** publish `pb_achieved`/`run_finished`. Return
  `{ deduped: true, is_pb: false, ... }`.
- **No match** — insert a finished run dated at submission, `source: 'ghost'`,
  `provenance: 'live'`, `ended_at = now`. Recompute `is_pb`/`was_pb`. Publish on the
  normal path (`pb_achieved` if `isPb` → Discord announces).
- **Either way** — write a `ghost_imports` audit row.

Marking the run **never** touches `provenance` (its CHECK is
`('live','legacy_import','carryover')` and SQLite can't `ALTER` a CHECK without a
table rebuild). Instead a nullable `source` column carries the ghost mark — an
additive `ALTER TABLE runs ADD COLUMN source TEXT` alongside the existing migrations
in `pi/src/db/connect.ts`. A new ghost run keeps its real `provenance` (`'live'`) and
sets `source='ghost'`; an enriched carryover keeps `provenance='carryover'` and gains
`source='ghost'` (i.e. "this row was enriched via a ghost import").

Schema (`server/schema.sql` + the additive migrations in `pi/src/db/connect.ts`):

- `runs.source TEXT` (nullable) — additive `ALTER TABLE` migration.
- New table:

  ```sql
  CREATE TABLE IF NOT EXISTS ghost_imports (
      id           INTEGER PRIMARY KEY,
      run_id       INTEGER REFERENCES runs(id),
      player_id    INTEGER NOT NULL REFERENCES players(id),
      course_id    INTEGER NOT NULL REFERENCES courses(id),
      cc           INTEGER NOT NULL,
      total_time_ms INTEGER,
      action       TEXT NOT NULL CHECK (action IN ('enriched','new')),
      created_at   TEXT NOT NULL DEFAULT (datetime('now'))
  );
  ```

Ingest lives in `db/ingest.ts` (a new `enrichRunFromGhost` + `findGhostMatch`)
keeping `runs.ts` thin.

## Frontend (`src/`)

- **Title bar** (`TitleBar.svelte` + `App.svelte` slot): an "Import PB from ghost"
  button in `.tb-actions` (monitor view). Off = outline; **armed = filled accent +
  armed dot**. State driven by the `ghost_import_state` event (a `ghostArmed`
  store/var). Click-off opens the warning modal; click-armed disarms via
  `send({ type: "set_ghost_import", enabled: false })`.
- **Warning modal**: small OBS-idiom modal (same tokens as `RunReviewModal`) with the
  copy above and "OK, enable" / "Cancel". `frontend-design` skill guides the button /
  modal / armed-state visuals at build time.
- **`RunReviewModal.svelte`**: pass `isGhost = run.source === "ghost"`. When true:
  (a) a **two-step submit confirm** mirroring the existing two-step Discard; (b) a
  subtle "From ghost" marker in the header. Character/kart stay required; course is
  pre-filled but editable.
- **`src/lib/playerCard.js`**: add `GHOST` (and `START_REPLAY`/`REPLAY_MENU`/
  `GHOST_RESET` as appropriate) to the activity-label map → "Watching a ghost…",
  replacing the generic "In the menus". Applies to the own card and friends' cards.

## Edge cases (walked against `temp/ghostsample.mp4`)

The clip: replay menu → course select → watch ghost (start) → replay menu (abort) →
restart (start) → replay menu (abort) → resume ("ghost continues") → replay menu →
"race against ghost" → reset → **real race** → race menu → character select (real
race aborted) → change char/kart → course select → watch ghost → **full
playthrough** → total held → ghost reset → **second full playthrough** → total held →
ghost reset → ghost menu → selection screen (end). The captured ghost matches Paul's
Choco Mountain carryover.

Expected `GhostImport` behaviour (armed before the clip):

- Watch #1 (`START_REPLAY → GHOST`, start, then aborted by replay menu): origin is a
  fresh start, validated, then **discard** on `GHOST → REPLAY_MENU`. Stay armed. No run.
- Restart (`REPLAY_MENU → GHOST_RESET → GHOST`, then aborted): `GHOST_RESET` origin ⇒
  fresh start; same — **discard**, stay armed.
- Resume (`REPLAY_MENU → GHOST` direct, "ghost continues"): `REPLAY_MENU` origin +
  clock already advanced → **rejected**, no capture. Stay armed.
- Real race (after "race against ghost" → reset → RACING): `effective_screen`
  returns RACING for a real RACING regardless of arm state → recorded as a **normal**
  run (not ghost). Ghost-import stays armed.
- Full playthrough #1: start validated → record → finish latches in the ~2-3s freeze
  → finalize `source: "ghost"`, **disarm**, popup. **One** ghost run emitted.
- Full playthrough #2: `IDLE` now → not recorded.
- Server: the submitted run's time matches the Choco Mountain carryover → **enrich,
  no announce**.

Other edge cases:

- **Armed mid-ghost** (user arms while already watching): `old == GHOST` on the next
  frame, no fresh `into-GHOST` transition → never starts; waits for the next start.
- **Finish missed on #1** (freeze evaded the latch): `GHOST → GHOST_RESET` with no
  total → discard, stay armed → **#2 catches it** (resilience). Acceptable.
- **Offline**: identical — the run holds in the outbox and uploads when the link
  returns.

## Testing

- **Unit (engine)** `tests/test_ghost_import.py`: the `GhostImport` state machine —
  start/validate/abort/finish/disarm; restart vs. resume via synthetic timer reads;
  `effective_screen` truth table; armed-mid-ghost; real-race-while-armed stays
  non-ghost.
- **Integration (engine)**: run headless against `temp/ghostsample.mp4`
  (`python -m mkw_tracker --video temp/ghostsample.mp4 --video-fps 0 --no-ipc` with a
  test harness capturing emitted `run_finalized`/`ghost_import_state`): assert exactly
  one `source: "ghost"` finalize, course = Choco Mountain, total time = the carryover
  value, plus one normal (non-ghost) finalize for the real race. Tune `START_ZERO_MS`
  here.
- **Unit (server)** `pi/src/db/ingest.test.ts` + `pi/src/api/runs.test.ts`: dedup
  match (enrich, no announce, audit `enriched`); no-match (insert, announce when PB,
  audit `new`); carryover enrich fills char/kart/costume + laps + trail.
- **Unit (Rust)** `sync.rs`: `source` passes through `build_upload_body`; a ghost run
  routes to `pending_review` with `source` in the embedded run.
- **Unit (frontend)**: `RunReviewModal` two-step submit when `isGhost`; `playerCard.js`
  "Watching a ghost" label for `GHOST`.
- **Manual**: live arm → watch a real ghost → review → submit → confirm server
  enrich/announce + audit row; button disarms on success.

## Risks / validation points

- The total-time auto-capture assumes the ghost's final timer truly **freezes** for
  the ~2-3s. The value latch (150ms) + pixel-still fallback should catch it; the
  popup's editable total is the ultimate safety net. Validated on the clip.
- `START_ZERO_MS` (restart-vs-resume) is empirical — tuned against the clip, which
  contains both cases.
- `GHOST` detection latency at the countdown: if `GHOST` is only detected a beat
  after GO, the "saw zero" window must tolerate a small starting offset. Tuned on the
  clip.

## Phasing (for the implementation plan)

1. **Engine capture** — `GhostImport` + `effective_screen` + finalize `source:ghost`
   + course-only seed + IPC. Validate against the clip.
2. **Finalize → popup** — `isGhost` two-step submit + "Watching a ghost" label.
3. **Server** — dedup-or-insert + `provenance:'ghost'` + `ghost_imports` audit.
4. **Frontend button** — title-bar button + warning modal + armed state
   (`frontend-design`).

Each phase is independently testable.

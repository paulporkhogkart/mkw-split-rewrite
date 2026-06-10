# Friend-Card Live Timer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a live, ticking `m:ss.SSS` race timer on each friend's player card during RACING (today it shows `"—"`), rendered 2.5 s behind real time so the finish lines up with no overshoot.

**Architecture:** The engine produces an accurate *real* elapsed-ms estimate cheaply (wall-clock counter anchored on the existing digit timer read, corrected by sparse re-reads, "never go backward"). It rides the presence pipeline as a new `elapsed_ms` field (pass-through). The monitor card buffers per-player samples and renders the live timer + progress bar delayed by 2.5 s (== `FinishStillDetector.STILL_SECONDS`); screen/selection/`final_time` render immediately.

**Tech Stack:** Python (engine, pytest), Svelte 4 + Vitest (frontend), TypeScript + Vitest (pi server, `node:sqlite`).

**Spec:** `docs/superpowers/specs/2026-06-10-friend-card-live-timer-design.md`

---

### Task 1: Engine — `RaceTimer` core + anchor logic

**Files:**
- Create: `mkw_tracker/race/timer.py`
- Test: `tests/test_race_timer.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_race_timer.py`:

```python
from mkw_tracker.race.timer import RaceTimer


def _t():
    # templates={} skips PNG loading; these tests drive step() with scripted reads.
    return RaceTimer(templates={}, resync_interval=0.5, tolerance_ms=300, forward_confirm=3)


def test_starts_on_first_nonzero_read():
    t = _t()
    assert t.step(None, 0.0, racing=True) is None     # nothing read yet
    assert t.step(0, 0.1, racing=True) is None         # still 0:00.000 -> not started
    assert t.step(1234, 0.2, racing=True) == 1234       # first >0 read anchors
    assert t.running is True


def test_counts_locally_between_reads():
    t = _t()
    t.step(1000, 10.0, racing=True)                     # anchor 1000ms @ 10.0s
    assert t.step(None, 10.5, racing=True) == 1500      # +0.5s
    assert t.step(None, 11.0, racing=True) == 2000


def test_backward_read_is_ignored_lap_flash():
    t = _t()
    t.step(1000, 10.0, racing=True)
    # 0.5s later the local estimate is ~1500; a lap-split flash reads far lower
    assert t.step(420, 10.5, racing=True) == 1500       # ignored, keeps counting
    assert t.step(None, 11.0, racing=True) == 2000


def test_within_tolerance_reanchors_drift():
    t = _t()
    t.step(1000, 10.0, racing=True)
    # estimate ~1500 @10.5; a read 1700 is within 300ms -> re-anchor up to 1700
    assert t.step(1700, 10.5, racing=True) == 1700
    assert t.step(None, 10.6, racing=True) == 1800      # counts from the new anchor


def test_forward_jump_needs_confirmation():
    t = _t()
    t.step(1000, 10.0, racing=True)                     # estimate stays 1000 @10.0
    assert t.step(9000, 10.0, racing=True) == 1000      # 1st big forward read ignored
    assert t.step(9000, 10.0, racing=True) == 1000      # 2nd, still ignored
    assert t.step(9000, 10.0, racing=True) == 9000      # 3rd confirms -> snap


def test_pause_freezes_and_resume_rebaselines():
    t = _t()
    t.step(1000, 10.0, racing=True)
    assert t.step(None, 12.0, racing=True) == 3000      # counting: +2s
    assert t.step(None, 12.0, racing=False) == 3000     # pause -> freeze
    assert t.step(None, 20.0, racing=False) == 3000     # 8s paused, no advance
    assert t.step(None, 20.0, racing=True) == 3000      # resume -> re-baseline, no jump
    assert t.step(None, 20.5, racing=True) == 3500      # counts from frozen value


def test_reset_clears_state():
    t = _t()
    t.step(1234, 10.0, racing=True)
    t.reset()
    assert t.running is False
    assert t.step(None, 11.0, racing=True) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_race_timer.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mkw_tracker.race.timer'`

- [ ] **Step 3: Write the implementation**

Create `mkw_tracker/race/timer.py`:

```python
"""RaceTimer - live in-race elapsed estimate for the friend-card timer.

Produces the engine's best estimate of the *real* in-game elapsed ms during
RACING, cheaply: a wall-clock counter anchored on the existing digit timer read
(reused from the timestamp tracker) and corrected by sparse re-reads. The card
applies any display delay; this value is un-lagged ground truth.

Anchor rule (cumulative race time only ever rises during a continuous run):
  * start            - first clean read with ms > 0 sets the anchor
  * backward read    - ignored (the ~7s lap-split flash + misreads); the local
                       counter carries the true cumulative time through it
  * within tolerance - re-anchor (drift correction)
  * forward read     - ignored unless `forward_confirm` consecutive reads agree
                       (process-stall recovery only)
Frozen on non-RACING; re-baselined on resume so a paused gap is not counted.
"""
import time
from typing import Optional

import numpy as np

from ..detection.screen import Screen
from .laps import load_digit_templates, read_digit_roi
from .timestamp import TIMESTAMP_ROIS


def read_timer_ms(frame, templates, threshold: float) -> Optional[int]:
    """Read the six-digit A:BC.DEF timer -> total ms, or None if any digit is unread."""
    vals = []
    for slot in ('A', 'B', 'C', 'D', 'E', 'F'):
        digit, _ = read_digit_roi(frame, TIMESTAMP_ROIS[slot], templates,
                                  threshold=threshold, reconfirm_digit=None)
        if digit is None:
            return None
        vals.append(digit)
    a, b, c, d, e, f = vals
    return a * 60_000 + (b * 10 + c) * 1000 + (d * 100 + e * 10 + f)


class RaceTimer:
    def __init__(self, digit_dir: str = 'images/timestamps/cropped', digit_h: int = 42,
                 digit_threshold: float = 0.50, resync_interval: float = 0.5,
                 tolerance_ms: int = 300, forward_confirm: int = 3, templates=None):
        self._templates = templates if templates is not None else load_digit_templates(digit_dir, digit_h)
        self.digit_threshold = digit_threshold
        self.resync_interval = resync_interval
        self.tolerance_ms = tolerance_ms
        self.forward_confirm = forward_confirm
        self.reset()

    def reset(self):
        self.running = False
        self.anchor_ms = 0
        self.anchor_perf = 0.0
        self._paused = False
        self._fwd = 0
        self._last_read = 0.0

    def _estimate(self, now: float) -> int:
        return int(round(self.anchor_ms + (now - self.anchor_perf) * 1000.0))

    def step(self, read_ms: Optional[int], now: float, racing: bool) -> Optional[int]:
        if not racing:
            if self.running and not self._paused:        # freeze at current
                self.anchor_ms = self._estimate(now)
                self.anchor_perf = now
            self._paused = True
            return self.anchor_ms if self.running else None

        if self._paused:                                 # resumed
            self.anchor_perf = now                        # drop the paused gap
            self._paused = False
            self._fwd = 0

        if read_ms is not None:
            if not self.running:
                if read_ms > 0:                          # start on first clean read > 0
                    self.running = True
                    self.anchor_ms = read_ms
                    self.anchor_perf = now
                    self._fwd = 0
            else:
                diff = read_ms - self._estimate(now)
                if abs(diff) <= self.tolerance_ms:        # drift correction
                    self.anchor_ms = read_ms
                    self.anchor_perf = now
                    self._fwd = 0
                elif diff > self.tolerance_ms:            # forward: confirm before snap
                    self._fwd += 1
                    if self._fwd >= self.forward_confirm:
                        self.anchor_ms = read_ms
                        self.anchor_perf = now
                        self._fwd = 0
                else:                                     # backward: ignore (lap flash)
                    self._fwd = 0

        return self._estimate(now) if self.running else None

    def update(self, frame: np.ndarray, screen: Screen, now: Optional[float] = None) -> Optional[int]:
        if now is None:
            now = time.perf_counter()
        racing = screen == Screen.RACING
        read_ms = None
        if racing and (now - self._last_read) >= self.resync_interval:
            self._last_read = now
            read_ms = read_timer_ms(frame, self._templates, self.digit_threshold)
        return self.step(read_ms, now, racing)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_race_timer.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add mkw_tracker/race/timer.py tests/test_race_timer.py
git commit -m "feat(timer): RaceTimer - live in-race elapsed estimate with never-go-backward anchor"
```

---

### Task 2: Engine — config, IPC emit, main-loop wiring, lifecycle reset

**Files:**
- Modify: `mkw_tracker/config/defaults.py` (after the Timestamp tracker block, ~line 44)
- Modify: `mkw_tracker/ipc/protocol.py` (after `emit_mush_update`, ~line 103)
- Modify: `mkw_tracker/lifecycle/race.py` (constructor ~line 27-49; `_clear_race_state` ~line 130)
- Modify: `mkw_tracker/main.py` (imports ~line 37 & 50-60; construct ~line 807; lifecycle ~line 836; loop ~line 849 & 1056)
- Test: `tests/test_race_timer.py` (append), `tests/test_race_lifecycle_finish.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_race_timer.py`:

```python
import json
from mkw_tracker.ipc.protocol import emit_race_time


def test_emit_race_time_json():
    assert json.loads(emit_race_time(5000)) == {"type": "race_time", "elapsed_ms": 5000}
    assert json.loads(emit_race_time(None)) == {"type": "race_time", "elapsed_ms": None}
```

Append to `tests/test_race_lifecycle_finish.py`:

```python
def test_clear_race_state_resets_the_race_timer():
    ts = MagicMock(); ts.total_time = None
    minimap = MagicMock(); minimap._calibrated = True
    timer = MagicMock()
    lc = RaceLifecycle(
        selection=MagicMock(), laps=MagicMock(), coins=MagicMock(), ts=ts,
        finish=FinishStillDetector(), mush=MagicMock(), minimap=minimap,
        mm_rec=MagicMock(), timer=timer,
    )
    lc.on_screen_change(Screen.RACING, Screen.MAIN_MENU)   # finalize + clear
    timer.reset.assert_called()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_race_timer.py::test_emit_race_time_json tests/test_race_lifecycle_finish.py::test_clear_race_state_resets_the_race_timer -q`
Expected: FAIL — `ImportError: cannot import name 'emit_race_time'` and `TypeError: __init__() got an unexpected keyword argument 'timer'`

- [ ] **Step 3a: Add config constants**

In `mkw_tracker/config/defaults.py`, after the `timestamp_digit_dir` line (~line 44), add:

```python

    # ── Race timer (live friend-card elapsed) ─────────────────────────────────
    race_timer_resync_interval: float = 0.5
    race_timer_tolerance_ms: int = 300
    race_timer_forward_confirm: int = 3
```

- [ ] **Step 3b: Add the IPC emit**

In `mkw_tracker/ipc/protocol.py`, after `emit_mush_update` (~line 103), add:

```python
def emit_race_time(elapsed_ms: Optional[int]) -> str:
    return _emit("race_time", elapsed_ms=elapsed_ms)
```

- [ ] **Step 3c: Wire the lifecycle reset**

In `mkw_tracker/lifecycle/race.py`, add a `timer=None` parameter to `__init__` (after `mm_rec`):

```python
        mm_rec:     MinimapRecorder,
        timer=None,
        lapstats=None,
```

Store it (after `self._mm_rec = mm_rec`):

```python
        self._mm_rec     = mm_rec
        self._timer      = timer
```

In `_clear_race_state` (after `self._minimap.reset()`):

```python
        self._minimap.reset()
        if self._timer is not None:
            self._timer.reset()
```

- [ ] **Step 3d: Wire main.py**

In `mkw_tracker/main.py`, add the import next to the other race imports (~line 37, after the `TimestampTracker` import):

```python
from .race.timer import RaceTimer
```

Add `emit_race_time` to the protocol import block (~line 50-52, alongside `emit_lap_update, emit_coin_update, emit_mush_update`):

```python
                            emit_lap_update, emit_coin_update, emit_mush_update, emit_race_time, emit_finish, emit_split_recorded,
```

Construct the timer after `lapstats = LapStatsTracker()` (~line 807):

```python
    lapstats  = LapStatsTracker()
    timer     = RaceTimer()
```

Pass it into the lifecycle (~line 845, inside the `RaceLifecycle(...)` call):

```python
        minimap=minimap,
        mm_rec=mm_rec,
        timer=timer,
        transition_count=transition_count,
```

Add the emit-throttle state right after `detector.on_screen_change = lifecycle.on_screen_change` (~line 849):

```python
    detector.on_screen_change = lifecycle.on_screen_change
    _last_rt_emit = 0.0
```

In the RACING tracker block (~line 1063, inside `if not _race_complete:`, after `mm_rec.update(...)`):

```python
            mm_rec.update(mm_state, lap_state.current_lap)
            race_elapsed = timer.update(frame, screen)
            if race_elapsed is not None:
                _now_rt = time.perf_counter()
                if _now_rt - _last_rt_emit >= 0.1:       # ~10 Hz cap on outbound
                    _last_rt_emit = _now_rt
                    ipc.emit(emit_race_time(race_elapsed))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_race_timer.py tests/test_race_lifecycle_finish.py -q`
Expected: PASS

Then run the full engine suite to confirm nothing broke:
Run: `python -m pytest -q`
Expected: PASS (all green)

- [ ] **Step 5: Commit**

```bash
git add mkw_tracker/config/defaults.py mkw_tracker/ipc/protocol.py mkw_tracker/lifecycle/race.py mkw_tracker/main.py tests/test_race_timer.py tests/test_race_lifecycle_finish.py
git commit -m "feat(timer): emit race_time at ~10Hz during RACING; reset RaceTimer on race clear"
```

---

### Task 3: App — store field, `race_time` handler, presence send

**Files:**
- Modify: `src/lib/stores.js:13-14` (`race` default)
- Modify: `src/App.svelte` (race var ~line 82; `case` ~line 816; store set ~line 1396)
- Modify: `src/lib/presence.js:13-22` (`frame()`)
- Test: `src/lib/presence.test.js` (update the `frame()` expectation)

- [ ] **Step 1: Update the failing test**

In `src/lib/presence.test.js`, update the first test to include `elapsedMs` in the store and `elapsed_ms` in the expected frame:

```js
    race.set({ curLap: 2, totLap: 3, coins: 7, mushrooms: 1, splits: {}, finishTime: null, elapsedMs: 5000 });
    minimap.set({ cx: 12, cy: 34, radius: 5, trackState: "tracking", roi: [0, 0, 1, 1] });
    resets.set(4);
    expect(frame()).toEqual({
      screen: "RACING", course: "Bowsers Castle", character: "Mario", kart: "Std", costume: "Base",
      cur_lap: 2, tot_lap: 3, coins: 7, mushrooms: 1, pos: [12, 34], final_time: null, resets: 4,
      track_state: "tracking", elapsed_ms: 5000,
    });
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx vitest run src/lib/presence.test.js`
Expected: FAIL — frame() result is missing `elapsed_ms`

- [ ] **Step 3a: Add the store field**

In `src/lib/stores.js`, update the `race` writable default (line 13-14):

```js
export const race       = writable({ curLap: null, totLap: null, coins: null, mushrooms: 0,
                                     splits: {}, finishTime: null, elapsedMs: null });
```

- [ ] **Step 3b: Send it in the presence frame**

In `src/lib/presence.js`, in `frame()` (the returned object, ~line 19-20), add `elapsed_ms`:

```js
    pos: mm ? [mm.cx, mm.cy] : null, final_time: r.finishTime, resets: get(resets),
    track_state: mm ? mm.trackState : null, elapsed_ms: r.elapsedMs ?? null,
```

- [ ] **Step 3c: Handle the engine event**

In `src/App.svelte`, add a state variable next to the other race vars (~line 82, near `let coins = null;`):

```js
  let elapsedMs = null;
```

Add a case in the `tracker-event` switch (~line 816, near `case "coin_update"`):

```js
      case "race_time": elapsedMs = msg.elapsed_ms; break;
```

Include it in the `raceStore.set` reactive (~line 1396):

```js
  $: raceStore.set({ curLap, totLap, coins, mushrooms,
                     splits: raceSplits, finishTime: raceFinishTime, elapsedMs });
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `npx vitest run src/lib/presence.test.js`
Expected: PASS

Run: `npm run check`
Expected: 0 errors, 0 warnings

- [ ] **Step 5: Commit**

```bash
git add src/lib/stores.js src/lib/presence.js src/App.svelte src/lib/presence.test.js
git commit -m "feat(timer): carry elapsed_ms through the store + presence frame"
```

---

### Task 4: Server (pi) — presence pass-through of `elapsed_ms`

**Files:**
- Modify: `pi/src/presence/hub.ts` (`PresenceFrame` ~line 12-20; `PresenceEntry` ~line 23-29; `offlineEntry` ~line 33-37; `update` ~line 75-83)
- Test: `pi/src/presence/hub.test.ts` (append)

- [ ] **Step 1: Write the failing test**

Append inside the `describe('PresenceHub', ...)` block in `pi/src/presence/hub.test.ts`:

```js
  it('passes elapsed_ms through (present -> value, absent -> null)', () => {
    const hub = new PresenceHub(db(), noCompletion, () => 2000);
    const got: any[] = [];
    hub.addSink((m) => got.push(m));
    hub.update(1, { screen: 'RACING', elapsed_ms: 12345 });
    expect(got.at(-1).player).toMatchObject({ elapsed_ms: 12345 });
    hub.update(2, { screen: 'RACING' });
    expect(got.at(-1).player).toMatchObject({ elapsed_ms: null });
  });
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd pi && npx vitest run src/presence/hub.test.ts`
Expected: FAIL — `elapsed_ms` is `undefined`, not `12345` / `null` (and a TS error on the unknown frame field)

- [ ] **Step 3: Implement the pass-through**

In `pi/src/presence/hub.ts`:

Add to `PresenceFrame` (after the `resets?` line):

```ts
  resets?: number | null;
  elapsed_ms?: number | null;
```

Add to `PresenceEntry` (after the `resets: ... ` line):

```ts
  resets: number | null; pb_ms: number | null;
  elapsed_ms: number | null;
```

Add to `offlineEntry` (in the returned object, after `resets: null,`):

```ts
           pb_ms: null, completion: null, dividers: [], final_time: null, updated_at: now, elapsed_ms: null };
```

Add to the entry built in `update()` (after the `coins/mushrooms/resets` line):

```ts
      coins: frame.coins ?? null, mushrooms: frame.mushrooms ?? null, resets: frame.resets ?? null,
      elapsed_ms: frame.elapsed_ms ?? null,
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd pi && npx vitest run src/presence/hub.test.ts`
Expected: PASS

Run (full pi suite, from `pi/`): `npx vitest run`
Expected: PASS (all green)

- [ ] **Step 5: Commit**

```bash
git add pi/src/presence/hub.ts pi/src/presence/hub.test.ts
git commit -m "feat(timer): pass elapsed_ms through the presence hub"
```

---

### Task 5: Frontend — per-player delay buffer

**Files:**
- Create: `src/lib/raceTimerBuffer.js`
- Test: `src/lib/raceTimerBuffer.test.js`

- [ ] **Step 1: Write the failing test**

Create `src/lib/raceTimerBuffer.test.js`:

```js
import { describe, it, expect } from "vitest";
import { interpolateAt } from "./raceTimerBuffer.js";

const S = [
  { t: 1000, elapsed_ms: 0, completion: 0 },
  { t: 2000, elapsed_ms: 1000, completion: 0.5 },
  { t: 3000, elapsed_ms: 2000, completion: 1 },
];

describe("interpolateAt", () => {
  it("interpolates between bracketing samples", () => {
    expect(interpolateAt(S, 2500)).toEqual({ elapsed_ms: 1500, completion: 0.75 });
  });
  it("holds the newest sample past the end", () => {
    expect(interpolateAt(S, 9999)).toEqual({ elapsed_ms: 2000, completion: 1 });
  });
  it("is null before the oldest sample and for empty", () => {
    expect(interpolateAt(S, 500)).toBeNull();
    expect(interpolateAt([], 2500)).toBeNull();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx vitest run src/lib/raceTimerBuffer.test.js`
Expected: FAIL — cannot resolve `./raceTimerBuffer.js`

- [ ] **Step 3: Write the implementation**

Create `src/lib/raceTimerBuffer.js`:

```js
// Per-player rolling sample buffer for the live friend-card timer + bar.
// The card renders these two live indicators DELAYED by DELAY_MS so the finish
// lines up: the real finish is only confirmed STILL_SECONDS (2.5 s) after the
// timer freezes, so the engine keeps emitting a climbing time for ~2.5 s; running
// the display 2.5 s behind makes it reach the total exactly as the finished
// result arrives. Engine/server stay un-lagged; the delay is purely display-side.
export const DELAY_MS = 2500;          // == FinishStillDetector.STILL_SECONDS
const MAX_AGE_MS = DELAY_MS + 1000;

const buffers = new Map();             // player_id -> [{ t, elapsed_ms, completion }] ascending t

export function pushSample(playerId, sample) {
  let buf = buffers.get(playerId);
  if (!buf) { buf = []; buffers.set(playerId, buf); }
  buf.push(sample);
  const cutoff = sample.t - MAX_AGE_MS;
  while (buf.length > 1 && buf[0].t < cutoff) buf.shift();
}

export function clearBuffer(playerId) { buffers.delete(playerId); }

function lerp(a, b, f) {
  if (a == null || b == null) return b == null ? a : b;
  return a + (b - a) * f;
}

/** Linear-interpolate { elapsed_ms, completion } at `target` within `samples`
 *  (ascending t). Past the newest -> hold newest. Before the oldest or empty ->
 *  null. Pure (exported for tests). */
export function interpolateAt(samples, target) {
  if (!samples || samples.length === 0) return null;
  const newest = samples[samples.length - 1];
  if (target >= newest.t) return { elapsed_ms: newest.elapsed_ms, completion: newest.completion };
  if (target <= samples[0].t) return null;
  let lo = samples[0];
  for (let i = 1; i < samples.length; i++) {
    const hi = samples[i];
    if (hi.t >= target) {
      const f = (target - lo.t) / (hi.t - lo.t || 1);
      return { elapsed_ms: lerp(lo.elapsed_ms, hi.elapsed_ms, f),
               completion: lerp(lo.completion, hi.completion, f) };
    }
    lo = hi;
  }
  return { elapsed_ms: newest.elapsed_ms, completion: newest.completion };
}

export function sampleAt(playerId, target) {
  return interpolateAt(buffers.get(playerId), target);
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx vitest run src/lib/raceTimerBuffer.test.js`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/lib/raceTimerBuffer.js src/lib/raceTimerBuffer.test.js
git commit -m "feat(timer): per-player delay buffer with linear interpolation"
```

---

### Task 6: Frontend — delayed timer + bar in `viewModel`

**Files:**
- Modify: `src/lib/playerCard.js:34-60` (`viewModel`)
- Test: `src/lib/playerCard.test.js` (update racing-bar test, add delayed-timer test)

- [ ] **Step 1: Update / add the failing tests**

In `src/lib/playerCard.test.js`, **replace** the test `"exposes a continuous bar fill + live dividers while racing"` with:

```js
  it("racing bar fill + timer come from the delayed sample; dividers immediate", () => {
    const e = { online: true, screen: "RACING", course: "Bowsers Castle", cur_lap: 2, tot_lap: 3,
      completion: 0.9, dividers: [0.31], updated_at: 1, name: "P", color: "#888" };
    const vm = viewModel(e, () => 2, { elapsed_ms: 1234, completion: 0.42 });
    expect(vm.bar).toEqual({ fill: 0.42, dividers: [0.31] });   // delayed completion, not e.completion
    expect(vm.primary).toEqual({ kind: "time", text: "0:01.234" });
  });
  it("racing with no delayed sample yet: timer is a dash, bar fill 0", () => {
    const e = { online: true, screen: "RACING", course: "Bowsers Castle", cur_lap: 1, tot_lap: 3,
      completion: 0.5, dividers: [0.31], updated_at: 1, name: "P", color: "#888" };
    const vm = viewModel(e, () => 2, null);
    expect(vm.primary).toEqual({ kind: "time", text: "—" });
    expect(vm.bar).toEqual({ fill: 0, dividers: [0.31] });
  });
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npx vitest run src/lib/playerCard.test.js`
Expected: FAIL — racing `bar.fill` is `0.9` (from `e.completion`) and `primary.text` is `"—"`, not the delayed values

- [ ] **Step 3: Update `viewModel`**

In `src/lib/playerCard.js`, change the signature and the racing/bar logic. Replace the function body from the `racing`/`finished` block through the return:

```js
/** A presence entry -> the card view model. `now` is a fn (Date.now) or a number
 *  for testability. `delayed` is the interpolated { elapsed_ms, completion } from
 *  the delay buffer (or null) - the racing timer + bar render from it so the
 *  display lags real time and lines up at the finish. */
export function viewModel(e, now = Date.now, delayed = null) {
  const t = typeof now === "function" ? now() : now;
  const color = e.color || "#888";
  if (!e.online) {
    const seen = e.updated_at > 0 ? lastSeen(t - e.updated_at) : null;
    return { state: "offline", name: e.name, color, online: false, char: null, kart: null, trk: null,
      primary: { kind: "seen", text: seen ? `last seen ${seen}` : "offline" },
      resets: null, pbStr: null, delta: null, bar: null };
  }
  const racing = e.screen === "RACING" && !e.final_time;
  const finished = (e.screen === "RACING" && e.final_time) || e.screen === "POST_TIME_TRIAL";
  let state, primary;
  if (SETUP[e.screen]) { state = "setup"; primary = { kind: "activity", text: SETUP[e.screen] }; }
  else if (racing) {
    state = "racing";
    const ms = delayed && delayed.elapsed_ms != null ? delayed.elapsed_ms : null;
    primary = { kind: "time", text: ms != null ? fmtTimeMs(Math.round(ms)) : "—" };
  }
  else if (finished) { state = "finished"; primary = { kind: "time", text: e.final_time }; }
  else { state = "menus"; primary = { kind: "activity", text: "In the menus" }; }
  const race = state === "racing" || state === "finished";
  const clamp01 = (x) => Math.max(0, Math.min(1, x));
  let fill = 0;
  if (state === "finished") fill = e.completion == null ? 1 : clamp01(e.completion);
  else if (state === "racing") fill = delayed && delayed.completion != null ? clamp01(delayed.completion) : 0;
  return {
    state, name: e.name, color, online: true,
    char: e.character || null, kart: e.kart || null, trk: e.course || null, primary,
    resets: race ? (e.resets ?? 0) : null,
    pbStr: race && e.pb_ms != null ? fmtTimeMs(e.pb_ms) : null,
    delta: state === "finished" ? pbDelta(e.final_time, e.pb_ms) : null,
    bar: race ? { fill, dividers: Array.isArray(e.dividers) ? e.dividers : [] } : null,
  };
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `npx vitest run src/lib/playerCard.test.js`
Expected: PASS (all viewModel tests, including the untouched setup/finished/offline ones)

- [ ] **Step 5: Commit**

```bash
git add src/lib/playerCard.js src/lib/playerCard.test.js
git commit -m "feat(timer): render racing timer + bar from the delayed sample in viewModel"
```

---

### Task 7: Frontend — card wiring + remove debug %

**Files:**
- Modify: `src/lib/presence.js` (message handler ~line 49-59: stamp `_rxAt`, push samples)
- Modify: `src/components/PlayerCard.svelte` (fast `now`, delayed sample; remove debug %)
- Modify: `src/components/PlayerPanel.svelte` (one shared clock)

This task is component wiring with no new unit tests; it is verified by `svelte-check`, the existing vitest suite, and a production build.

- [ ] **Step 1: Stamp arrival time + buffer samples on receive**

In `src/lib/presence.js`, add the import near the top (after the existing imports):

```js
import { pushSample } from "./raceTimerBuffer.js";
```

Replace the `message` handler body (the `presence_snapshot` / `presence_update` branches, ~line 51-57):

```js
      if (msg.type === "presence_snapshot") {
        const map = {}, t = Date.now();
        for (const p of msg.players) {
          p._rxAt = t; map[p.player_id] = p;
          pushSample(p.player_id, { t, elapsed_ms: p.elapsed_ms, completion: p.completion });
        }
        presence.set(map);
      } else if (msg.type === "presence_update") {
        const t = Date.now(), p = { ...msg.player, _rxAt: t };
        pushSample(p.player_id, { t, elapsed_ms: p.elapsed_ms, completion: p.completion });
        presence.update((m) => ({ ...m, [p.player_id]: p }));
      }
```

- [ ] **Step 2: Wire the card to the fast clock + delayed sample; drop the debug %**

Replace the `<script>` block of `src/components/PlayerCard.svelte`:

```svelte
<script>
  import { viewModel } from "../lib/playerCard.js";
  import { figureFor } from "../lib/playerFigures.js";
  import { sampleAt, DELAY_MS } from "../lib/raceTimerBuffer.js";
  export let entry;
  export let now = Date.now();            // driven by PlayerPanel (fast while racing)
  $: delayed = entry ? sampleAt(entry.player_id, now - DELAY_MS) : null;
  $: vm = viewModel(entry, now, delayed);
  $: fig = figureFor(vm.name, vm.online);
</script>
```

Remove the debug markup (the two lines near the end of the `.data` div):

```svelte
    <!-- TEMP debug: raw % progression. Remove when validated. -->
    {#if dbgPct != null}<div class="dbg">{dbgPct}%</div>{/if}
```

Remove the debug style block:

```svelte
  /* TEMP debug: raw % progression. Remove when validated. */
  .dbg { margin-top: 3px; font-family: ui-monospace, "Cascadia Code", monospace; font-size: 9px;
         font-variant-numeric: tabular-nums; color: #ffd23f; letter-spacing: .03em; }
```

- [ ] **Step 3: Add the shared clock in PlayerPanel**

Replace the `<script>` block of `src/components/PlayerPanel.svelte`:

```svelte
<script>
  import { onDestroy } from "svelte";
  import { presence } from "../lib/stores.js";
  import PlayerCard from "./PlayerCard.svelte";
  // presence is { [player_id]: entry }; render in stable ascending player_id order.
  $: players = Object.values($presence).sort((a, b) => a.player_id - b.player_id);
  $: anyRacing = players.some((p) => p.online && p.screen === "RACING" && !p.final_time);

  // One clock for all cards: ~30 fps while someone races (so the ms timer ticks),
  // else a cheap 1 s tick (offline "last seen"). Avoids a per-card animation loop.
  let now = Date.now();
  let fast = 0, slow = 0;
  function setClock(racing) {
    clearInterval(fast); clearInterval(slow); fast = 0; slow = 0;
    now = Date.now();
    if (racing) fast = setInterval(() => (now = Date.now()), 33);
    else slow = setInterval(() => (now = Date.now()), 1000);
  }
  $: setClock(anyRacing);
  onDestroy(() => { clearInterval(fast); clearInterval(slow); });
</script>
```

Pass `now` to each card (in the `{#each}`):

```svelte
    {#each players as p (p.player_id)}<PlayerCard entry={p} {now} />{/each}
```

- [ ] **Step 4: Verify**

Run: `npm run check`
Expected: 0 errors, 0 warnings

Run: `npm run test:js`
Expected: PASS (full frontend suite)

Run: `npm run build`
Expected: build succeeds (no errors)

- [ ] **Step 5: Commit**

```bash
git add src/lib/presence.js src/components/PlayerCard.svelte src/components/PlayerPanel.svelte
git commit -m "feat(timer): card renders the 2.5s-delayed live timer + bar; remove debug % readout"
```

---

## Self-Review notes

- **Spec coverage:** Engine `RaceTimer` + anchor rule (T1), config + IPC + main-loop emit + lifecycle reset (T2), store/app/presence send (T3), server pass-through (T4), delay buffer (T5), delayed timer+bar viewModel (T6), card wiring + debug-% removal (T7). All spec sections mapped.
- **Type/name consistency:** `read_timer_ms`, `RaceTimer.step/update/reset`, `emit_race_time`, `elapsed_ms` (snake, engine/server/frame) vs `elapsedMs` (camel, JS store), `sampleAt`/`interpolateAt`/`pushSample`/`DELAY_MS`, `viewModel(e, now, delayed)` — consistent across tasks.
- **Manual verification (post-merge, live):** confirm the timer ticks in full ms during RACING, holds ~2.5 s at the start, stays smooth through a lap crossing (the ~7 s split flash), and lands on the exact `final_time` with no visible jump at the finish; confirm the bar stays locked to the timer. Tune `DELAY_MS` only if a residual jump shows at the finish.
```

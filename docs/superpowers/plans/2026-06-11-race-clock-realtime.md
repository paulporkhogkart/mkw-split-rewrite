# Race Clock Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trails stamped on the race clock (back-filled to GO), retroactive trail filter deleted, finish latched from the frozen timer value in ~150ms, friend-card delay cut to 100ms.

**Architecture:** `FinishValueLatch` (value-streak + estimate guard) and a `FinishLatch` combo (value primary, pixel-still fallback) in `race/finish.py` — drop-in at the existing `finish` seam (lifecycle only touches `.detected`/`.reset()`). `MinimapRecorder` loses its stopwatch/pause logic and stamps points with the `RaceTimer` value passed in from the main loop. Spec: `docs/superpowers/specs/2026-06-11-race-clock-realtime-design.md`.

**Tech Stack:** Python/OpenCV/pytest (engine), one JS constant + vitest (frontend).

**Repo rules:** stage files explicitly; the working-tree edits to `race/finish.py` (STILL_SECONDS 0.6) and `src/lib/raceTimerBuffer.js` (DELAY_MS 600) are absorbed by these tasks. `src-tauri/Cargo.toml` is line-endings-only dirty — leave it alone. Branch `race-clock-realtime`, ff-merge to main at the end.

---

### Task 1: Branch + commit the spec and this plan

- [ ] **Step 1: Branch and commit**

```bash
git checkout -b race-clock-realtime
git add docs/superpowers/specs/2026-06-11-race-clock-realtime-design.md docs/superpowers/plans/2026-06-11-race-clock-realtime.md
git commit -m "docs(race-clock): spec + implementation plan

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: `FinishValueLatch` + `FinishLatch` combo

**Files:**
- Modify: `mkw_tracker/race/finish.py` (add two classes; keep `FinishStillDetector` with STILL_SECONDS = 0.6)
- Test: `tests/test_finish_latch.py`

- [ ] **Step 1: Write the failing tests**

```python
"""FinishValueLatch streak/guard logic + FinishLatch combo seam."""
from mkw_tracker.race.finish import FinishValueLatch, FinishLatch


def make_latch():
    # templates aren't needed for feed()-level tests
    return FinishValueLatch(templates={})


def test_running_timer_never_latches():
    l = make_latch()
    for ms in range(50_000, 50_500, 50):       # advancing reads
        assert not l.feed(ms, lap_inc=False, estimate_ms=ms)
    assert not l.detected


def test_frozen_value_matching_estimate_latches_on_third_read():
    l = make_latch()
    assert not l.feed(96_713, lap_inc=False, estimate_ms=96_750)
    assert not l.feed(96_713, lap_inc=False, estimate_ms=96_800)
    assert l.feed(96_713, lap_inc=False, estimate_ms=96_850)
    assert l.detected and l.final_ms == 96_713


def test_lap_flash_rejected_by_estimate_guard():
    """Frozen lap split (32s) vs climbing cumulative estimate (~65s): never latches."""
    l = make_latch()
    est = 65_000
    for _ in range(10):
        assert not l.feed(32_456, lap_inc=False, estimate_ms=est)
        est += 50
    assert not l.detected


def test_lap_inc_resets_streak():
    l = make_latch()
    l.feed(96_713, lap_inc=False, estimate_ms=96_713)
    l.feed(96_713, lap_inc=True, estimate_ms=96_763)    # increment mid-streak
    assert not l.feed(96_713, lap_inc=False, estimate_ms=96_813)
    assert not l.detected                                # streak restarted at 1... then 2


def test_none_read_resets_streak():
    l = make_latch()
    l.feed(96_713, lap_inc=False, estimate_ms=96_713)
    l.feed(None, lap_inc=False, estimate_ms=96_763)
    l.feed(96_713, lap_inc=False, estimate_ms=96_813)
    assert not l.feed(None, lap_inc=False, estimate_ms=96_863)
    assert not l.detected


def test_missing_estimate_never_latches():
    l = make_latch()
    for _ in range(5):
        assert not l.feed(96_713, lap_inc=False, estimate_ms=None)
    assert not l.detected


def test_combo_exposes_detected_and_reset():
    c = FinishLatch(templates={})
    assert not c.detected
    c.value.feed(10_000, lap_inc=False, estimate_ms=10_000)
    c.value.feed(10_000, lap_inc=False, estimate_ms=10_050)
    c.value.feed(10_000, lap_inc=False, estimate_ms=10_100)
    assert c.detected and c.final_ms == 10_000
    c.reset()
    assert not c.detected and c.final_ms is None
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_finish_latch.py -v`
Expected: ImportError (`FinishValueLatch` not defined)

- [ ] **Step 3: Implement** (append to `race/finish.py`, after `FinishStillDetector`)

```python
class FinishValueLatch:
    """Latch the final-lap finish by reading the frozen timer VALUE.

    On the final lap the timer freezes on the total with no flash. A running
    timer's ms digit changes every frame, so N_CONFIRM identical reads at
    READ_INTERVAL means frozen (~150ms worst-case latency). Guards:
      * per-read |value - RaceTimer estimate| <= TOLERANCE_MS - rejects the
        lap-split flash, whose frozen value falls behind the climbing
        cumulative estimate within a fraction of a second;
      * lap_inc resets the streak - covers the first instants after the
        final-lap crossing where split ~ cumulative on short races;
      * a failed read resets the streak (conservative).
    The latched value IS the final total (final_ms).
    """
    N_CONFIRM     = 3
    READ_INTERVAL = 0.05
    TOLERANCE_MS  = 300

    def __init__(self, templates=None, digit_dir: str = 'images/timestamps/cropped',
                 digit_h: int = 42, digit_threshold: float = 0.50):
        from .timer import read_timer_ms
        from .laps import load_digit_templates
        self._read_timer_ms = read_timer_ms
        self._templates = templates if templates is not None \
            else load_digit_templates(digit_dir, digit_h)
        self.digit_threshold = digit_threshold
        self.reset()

    def reset(self):
        self.detected    = False
        self.final_ms    = None
        self._streak_val = None
        self._streak_n   = 0
        self._last_read  = 0.0

    def feed(self, read_ms: Optional[int], lap_inc: bool,
             estimate_ms: Optional[int]) -> bool:
        """Streak logic only (no frame I/O) - unit-testable."""
        if self.detected:
            return True
        if lap_inc or read_ms is None or estimate_ms is None \
                or abs(read_ms - estimate_ms) > self.TOLERANCE_MS:
            self._streak_val, self._streak_n = None, 0
            return False
        if read_ms == self._streak_val:
            self._streak_n += 1
        else:
            self._streak_val, self._streak_n = read_ms, 1
        if self._streak_n >= self.N_CONFIRM:
            self.detected = True
            self.final_ms = read_ms
            print(f"  [finish] timer value frozen x{self.N_CONFIRM} on final lap "
                  f"-> final time {read_ms}ms")
        return self.detected

    def update(self, frame: np.ndarray, screen: Screen, on_final_lap: bool,
               lap_inc: bool = False, estimate_ms: Optional[int] = None,
               now: Optional[float] = None) -> bool:
        if self.detected:
            return True
        if screen != Screen.RACING or not on_final_lap:
            self._streak_val, self._streak_n = None, 0
            return False
        if now is None:
            now = time.perf_counter()
        if (now - self._last_read) < self.READ_INTERVAL:
            # a lap increment must reset the streak even between reads
            if lap_inc:
                self._streak_val, self._streak_n = None, 0
            return False
        self._last_read = now
        read_ms = self._read_timer_ms(frame, self._templates, self.digit_threshold)
        return self.feed(read_ms, lap_inc, estimate_ms)


class FinishLatch:
    """Final-finish seam: value latch primary, pixel-still fallback.

    Drop-in where FinishStillDetector was used - lifecycle only touches
    .detected / .reset(). final_ms is set only when the value path fired.
    """

    def __init__(self, templates=None):
        self.value = FinishValueLatch(templates=templates)
        self.still = FinishStillDetector()

    @property
    def detected(self) -> bool:
        return self.value.detected or self.still.detected

    @property
    def final_ms(self) -> Optional[int]:
        return self.value.final_ms

    def reset(self):
        self.value.reset()
        self.still.reset()

    def update(self, frame: np.ndarray, screen: Screen, on_final_lap: bool,
               lap_inc: bool = False, estimate_ms: Optional[int] = None) -> bool:
        v = self.value.update(frame, screen, on_final_lap, lap_inc, estimate_ms)
        s = self.still.update(frame, screen, on_final_lap)
        return v or s
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_finish_latch.py -v`
Expected: 7 passed. (Note `test_lap_inc_resets_streak`: after the reset the two
follow-up feeds rebuild a streak of 2 < 3 - assert holds.)

- [ ] **Step 5: Commit**

```bash
git add mkw_tracker/race/finish.py tests/test_finish_latch.py
git commit -m "feat(race): value-based finish latch with estimate guard + still fallback

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Recorder on the race clock

**Files:**
- Modify: `mkw_tracker/minimap/recorder.py` (rewrite)
- Test: `tests/test_minimap_recorder.py`
- Check: `tests/test_run_finalized.py` (update `mm_rec.update` calls to pass `race_ms` if it asserts recorded points)

- [ ] **Step 1: Write the failing tests**

```python
"""MinimapRecorder: race-clock stamping, GO backfill, monotonic guard."""
from mkw_tracker.minimap.recorder import MinimapRecorder
from mkw_tracker.minimap.tracker import MinimapState


def mm(cx=1700.0, cy=600.0, score=0.9, tracking=True):
    st = MinimapState(cx=int(cx), cy=int(cy), cx_smooth=cx, cy_smooth=cy,
                      tracking=tracking, last_score=score)
    return st


def test_pending_points_backfill_to_go():
    r = MinimapRecorder()
    r.start()
    # countdown + pre-anchor: race_ms None, perf clock advancing
    r.update(mm(cx=100), lap=1, race_ms=None, now=10.00)   # will be t=-200 -> dropped
    r.update(mm(cx=101), lap=1, race_ms=None, now=10.15)   # t=-50  -> dropped
    r.update(mm(cx=102), lap=1, race_ms=None, now=10.30)   # t=100  -> kept
    r.update(mm(cx=103), lap=1, race_ms=500, now=10.70)    # first anchor
    ts = [p[0] for p in r.points]
    assert ts == [100, 500]
    assert r.points[0][1] == 102.0


def test_monotonic_guard_skips_frozen_clock():
    r = MinimapRecorder()
    r.start()
    r.update(mm(), lap=1, race_ms=1000, now=1.0)
    r.update(mm(), lap=1, race_ms=1000, now=1.016)   # frozen (pause) -> skipped
    r.update(mm(), lap=1, race_ms=1016, now=1.032)
    assert [p[0] for p in r.points] == [1000, 1016]


def test_not_tracking_or_stopped_records_nothing():
    r = MinimapRecorder()
    r.update(mm(), lap=1, race_ms=100, now=1.0)          # not started
    r.start()
    r.update(mm(tracking=False), lap=1, race_ms=200, now=1.1)
    assert r.points == []
    r.stop()
    r.update(mm(), lap=1, race_ms=300, now=1.2)
    assert r.points == []


def test_ring_only_band_points_are_kept():
    """No score-based filtering exists anymore (retroactive_filter deleted)."""
    r = MinimapRecorder()
    r.start()
    r.update(mm(score=0.50), lap=1, race_ms=100, now=1.0)   # sub-confident score
    assert len(r.points) == 1
    assert not hasattr(r, "retroactive_filter")
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_minimap_recorder.py -v`
Expected: TypeError (`update` has no `race_ms`/`now` kwargs)

- [ ] **Step 3: Rewrite `recorder.py`**

```python
"""MinimapRecorder - records minimap trail points on the race clock."""
import time
from typing import Optional

from .tracker import MinimapState


class MinimapRecorder:
    """Records (race_ms, cx, cy, score, lap) during a race.

    Timestamps are the RaceTimer race clock (passed in by the main loop), so
    trails share the clock shown on the cards and t=0 is GO. Points seen
    before the timer's first anchor (countdown + ~0.5s) are buffered with
    their perf time and back-stamped when the anchor arrives; the countdown
    remainder (t < 0) is dropped. RaceTimer freezes during pauses, so the
    monotonic guard drops paused frames - no pause bookkeeping needed here.
    """

    def __init__(self):
        self._points:    list = []
        self._pending:   list = []   # (perf_now, cx, cy, score, lap) pre-anchor
        self._recording: bool = False
        self._last_t:    int  = -1

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def points(self) -> list:
        """Read-only copy of recorded points (list of (t_ms, cx, cy, score, lap))."""
        return list(self._points)

    def start(self):
        """Call when RACING begins (new race instance)."""
        self._points    = []
        self._pending   = []
        self._recording = True
        self._last_t    = -1

    def stop(self):
        self._recording = False
        self._points    = []
        self._pending   = []
        self._last_t    = -1

    def update(self, mm: MinimapState, lap: Optional[int] = None,
               race_ms: Optional[int] = None, now: Optional[float] = None):
        """Append a position point stamped with the race clock."""
        if not self._recording or not mm.tracking or mm.cx is None:
            return
        if now is None:
            now = time.perf_counter()
        if race_ms is None:
            self._pending.append((now, mm.cx_smooth, mm.cy_smooth,
                                  mm.last_score, lap))
            return
        if self._pending:
            for p_now, cx, cy, sc, lp in self._pending:
                t = int(round(race_ms - (now - p_now) * 1000.0))
                if 0 <= t and t > self._last_t:
                    self._points.append((t, cx, cy, sc, lp))
                    self._last_t = t
            self._pending = []
        t = int(race_ms)
        if t <= self._last_t:
            return
        self._points.append((t, mm.cx_smooth, mm.cy_smooth, mm.last_score, lap))
        self._last_t = t
```

- [ ] **Step 4: Fix consumers of the old API, run engine suite**

`grep -rn "retroactive_filter\|mm_rec\.pause\|mm_rec\.resume\|is_paused" mkw_tracker/ tests/ --include=*.py`
- `lifecycle/race.py`: delete `self._mm_rec.pause()` / `self._mm_rec.resume()`
  lines (keep `_paused_from_racing` bookkeeping and the prints) and the
  `self._mm_rec.retroactive_filter(new_threshold)` line in `finalize`.
- `main.py`: delete both `mm_rec.retroactive_filter(new_thr)` lines (keep the
  surrounding calibrate + `set_minimap_threshold` logic). Wiring of the new
  `update` signature happens in Task 4.
- `tests/test_run_finalized.py`: if it drives `mm_rec.update(state, lap)`,
  add `race_ms=<advancing ints>` so points still record.

Run: `python -m pytest tests/ -q` -> all pass.

- [ ] **Step 5: Commit**

```bash
git add mkw_tracker/minimap/recorder.py mkw_tracker/lifecycle/race.py mkw_tracker/main.py tests/test_minimap_recorder.py tests/test_run_finalized.py
git commit -m "feat(replay): trail points on the race clock; retroactive filter deleted

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Main-loop + lifecycle wiring

**Files:**
- Modify: `mkw_tracker/main.py`

- [ ] **Step 1: Construction (main.py ~795-805)** - reorder so the timer exists
first and the latch shares its digit templates; replace the still-detector:

```python
    timer     = RaceTimer()
    finish    = FinishLatch(templates=timer._templates)   # value latch + still fallback
    mm_rec    = MinimapRecorder()
```

(`timer = RaceTimer()` moves up from its old line; delete the old
`finish = FinishStillDetector()` line; import `FinishLatch` from `.race.finish`.)

- [ ] **Step 2: Per-frame order (the `if not _race_complete:` block)** - compute
the race clock before recording, gate recording on RACING:

```python
            mm_state           = minimap.update(frame, screen)
            race_elapsed = timer.update(frame, screen)
            if screen == Screen.RACING:
                mm_rec.update(mm_state, lap_state.current_lap, race_elapsed)
```

and in the `else:` branch (race complete) add `race_elapsed = None` so the
finish call below never sees an unbound name.

- [ ] **Step 3: Finish call** - pass the new guards:

```python
        finish_just_detected  = (finish.update(frame, screen, bool(_on_final_lap),
                                               lap_inc=lap_inc,
                                               estimate_ms=race_elapsed)
                                 and ts.total_time is None)
```

- [ ] **Step 4: Full suite + grep**

Run: `python -m pytest tests/ -q` -> all pass.
`grep -rn "FinishStillDetector()" mkw_tracker/` -> no constructor call sites
left outside `finish.py`.

- [ ] **Step 5: Commit**

```bash
git add mkw_tracker/main.py
git commit -m "feat(race): wire value latch + race-clock recording into the main loop

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Frontend delay

**Files:**
- Modify: `src/lib/raceTimerBuffer.js`

- [ ] **Step 1: Set the constant and rewrite the stale comment**

```js
// Per-player rolling sample buffer for the live friend-card timer + bar.
// The card renders these two live indicators DELAYED by DELAY_MS so the finish
// lands cleanly: the engine confirms the finish ~150ms after the timer freezes
// (FinishValueLatch: 3 identical digit reads at 50ms cadence), and keeps
// emitting a climbing time until then. 100ms of display delay absorbs most of
// that window; any residual overshoot is <~50ms - invisible on a spinning
// millisecond wheel. Engine/server stay un-lagged; the delay is display-only.
export const DELAY_MS = 100;           // ~= FinishValueLatch worst-case latency
```

- [ ] **Step 2: Frontend tests**

Run: `npm test -- --run` (or the project's vitest invocation)
Expected: all pass (raceTimerBuffer tests don't couple to the constant).

- [ ] **Step 3: Commit**

```bash
git add src/lib/raceTimerBuffer.js
git commit -m "feat(cards): cut live timer display delay to 100ms (value latch)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Offline latch validation on real finishes

**Files:**
- Create: `temp/finish_lab.py` (temp/ is gitignored - validation evidence goes in the commit message / spec)

- [ ] **Step 1: Write the probe**

```python
"""Validate FinishValueLatch on real race footage with known totals.

Feeds every frame from GO to finish+5s through RaceTimer + FinishValueLatch
with WORST-CASE gating: on_final_lap=True for the whole race and lap_inc=False
always (both production guards disabled). The estimate check alone must reject
every lap-split flash; the latch must fire on the true freeze with the exact
known total. Run: python temp/finish_lab.py
"""
import os
import sys
import time

import cv2

sys.path.insert(0, os.path.abspath("."))
from mkw_tracker.detection.screen import Screen
from mkw_tracker.race.timer import RaceTimer, read_timer_ms
from mkw_tracker.race.finish import FinishValueLatch

CLIPS = [
    # video, GO (s), expected final total (ms)
    (os.path.join("temp", "bootest.mp4"), 39.5, 96_713),
    (os.path.join("temp", "koops.mp4"),   15.8, 98_185),
]

for video, go_s, expect_ms in CLIPS:
    cap = cv2.VideoCapture(video)
    fps = cap.get(cv2.CAP_PROP_FPS)
    timer = RaceTimer()
    latch = FinishValueLatch(templates=timer._templates)
    end_frame = int((go_s + expect_ms / 1000.0 + 5.0) * fps)
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(go_s * fps))
    freeze_wall = go_s + expect_ms / 1000.0
    latched_at, false_latches = None, 0
    for idx in range(int(go_s * fps), end_frame):
        ok, frame = cap.read()
        if not ok:
            break
        now = idx / fps                       # video time as the clock
        est = timer.update(frame, Screen.RACING, now=now)
        fired = latch.update(frame, Screen.RACING, on_final_lap=True,
                             lap_inc=False, estimate_ms=est, now=now)
        if fired and latched_at is None:
            latched_at = now
            if now < freeze_wall - 0.5:
                false_latches += 1
            break
    cap.release()
    name = os.path.basename(video)
    if latched_at is None:
        print(f"{name}: NO LATCH (expected {expect_ms}ms ~ {freeze_wall:.2f}s)")
        continue
    print(f"{name}: latched {latch.final_ms}ms at {latched_at:.3f}s "
          f"(freeze ~{freeze_wall:.3f}s, latency {latched_at - freeze_wall:+.3f}s) "
          f"value_ok={latch.final_ms == expect_ms} false_latches={false_latches}")
```

- [ ] **Step 2: Run and judge**

Run: `python temp/finish_lab.py`
Acceptance: both clips `value_ok=True`, latency <= 0.2s, `false_latches=0`,
and no latch fires mid-race. If a lap flash latches with the guards disabled,
report it - production has two more guards, but the estimate check was
supposed to carry this alone.

- [ ] **Step 3: Full suite, merge, clean up**

```bash
python -m pytest tests/ -q
git checkout main
git merge race-clock-realtime
python -m pytest tests/ -q
git branch -d race-clock-realtime
```

---

## Self-review notes

- Spec coverage: A (filter deletion - Task 3), B (race-clock stamping +
  backfill - Tasks 3/4), C (latch + fallback - Tasks 2/4, validated Task 6),
  D (DELAY_MS - Task 5). Out-of-scope items untouched.
- Signatures consistent: `FinishValueLatch.feed(read_ms, lap_inc, estimate_ms)`,
  `.update(frame, screen, on_final_lap, lap_inc=, estimate_ms=, now=)`,
  `FinishLatch.update(frame, screen, on_final_lap, lap_inc=, estimate_ms=)`,
  `MinimapRecorder.update(mm, lap=, race_ms=, now=)`.
- `FinishValueLatch.__init__` accepts `templates={}` for feed-only tests
  (no file I/O when a dict is passed).

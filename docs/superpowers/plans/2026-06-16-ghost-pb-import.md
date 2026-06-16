# Ghost PB Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user arm a one-shot "import" that records the next in-game ghost replay (watched from its start) as one of their runs, filling missing identity in the existing review popup and deduping against an existing/carryover run server-side.

**Architecture:** A single `effective_screen()` remap (`GHOST→RACING`, only while recording a ghost) feeds the existing race trackers + `FinishLatch` so capture is identical to a live race, while per-frame *live emits* stay gated on the real screen (quiet capture, no presence leak). A small `GhostImport` state machine owns arm → catch-start → validate (restart-vs-resume) → finish/abort → disarm. The finalized run is tagged `source:"ghost"` and flows through the unchanged Rust outbox to the server, which deduplicates by player+course+exact total time (enrich-no-announce) or inserts + announces; a `ghost_imports` audit row + a `runs.source` mark record it.

**Tech Stack:** Python (engine: numpy/opencv, dataclasses), Rust (Tauri — no changes needed), TypeScript (Hono + node:sqlite server, vitest), Svelte (frontend, vitest).

Reference spec: `docs/superpowers/specs/2026-06-16-ghost-pb-import-design.md`.

---

## File structure

**Phase 1 — Engine (`mkw_tracker/`)**
- Create `mkw_tracker/lifecycle/ghost.py` — `GhostImport` state machine (cv2-free, unit-testable).
- Modify `mkw_tracker/lifecycle/race.py` — own a `GhostImport`; handle GHOST transitions; `effective_screen` / `arm_ghost` / `disarm_ghost` / `validate_ghost_start`; thread `ghost` into finalize.
- Modify `mkw_tracker/ipc/protocol.py` — `emit_ghost_import_state`.
- Modify `mkw_tracker/main.py` — `set_ghost_import` IPC; `eff_screen` for trackers; gate live emits on the real screen; skip ghost calibration.
- Create `tests/test_ghost_import.py` — state-machine unit tests.
- Create `scripts/ghost_import_clip_check.py` — manual integration run against `temp/ghostsample.mp4`.

**Phase 2 — Frontend (`src/`)**
- Create `src/components/GhostImportWarning.svelte` — the arm warning modal.
- Modify `src/App.svelte` — `ghost_import_state` handling, the title-bar button + modal wiring, `isGhost` to the review modal.
- Modify `src/components/RunReviewModal.svelte` — two-step submit + "From ghost" marker when `isGhost`.
- Modify `src/lib/playerCard.js` — "Watching a ghost…" activity label for ghost screens.
- Modify `src/lib/playerCard.test.js` — label test.

**Phase 3 — Server (`pi/` + `server/schema.sql`)**
- Modify `server/schema.sql` — `runs.source` column + `ghost_imports` table.
- Modify `pi/src/db/connect.ts` — additive migrations for both.
- Modify `pi/src/db/types.ts` — `AttemptPayload.source`.
- Modify `pi/src/db/ingest.ts` — `upsertRun` writes `source`; add `findGhostMatch` + `enrichRunFromGhost`.
- Create `pi/src/db/ghostImport.ts` — `recordGhostImport` audit helper.
- Modify `pi/src/api/runs.ts` — ghost branch (dedup-or-insert, announce-or-not, audit).
- Tests alongside: `pi/src/db/ingest.test.ts`, `pi/src/api/runs.test.ts`.

Phases are ordered but each is independently testable (engine via unit + clip; server via unit; frontend via unit + manual).

---

# Phase 1 — Engine capture

### Task 1: `GhostImport` state machine

**Files:**
- Create: `mkw_tracker/lifecycle/ghost.py`
- Test: `tests/test_ghost_import.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ghost_import.py
from mkw_tracker.lifecycle.ghost import GhostImport, GhostState
from mkw_tracker.detection.screen import Screen


def test_starts_idle_disarmed():
    g = GhostImport()
    assert g.state == GhostState.IDLE
    assert not g.armed and not g.recording


def test_arm_then_disarm():
    g = GhostImport()
    g.arm()
    assert g.state == GhostState.ARMED and g.armed and not g.recording
    g.disarm()
    assert g.state == GhostState.IDLE and not g.armed


def test_enter_ghost_when_armed_starts_provisional_recording():
    g = GhostImport(); g.arm()
    started = g.on_ghost_enter(Screen.START_REPLAY)
    assert started is True
    assert g.state == GhostState.RECORDING and g.recording


def test_enter_ghost_when_idle_does_not_start():
    g = GhostImport()
    assert g.on_ghost_enter(Screen.GHOST_RESET) is False
    assert g.state == GhostState.IDLE


def test_fresh_origin_confirms_immediately_regardless_of_clock():
    # GHOST_RESET / START_REPLAY origin == a reload happened == fresh start.
    for origin in (Screen.GHOST_RESET, Screen.START_REPLAY):
        g = GhostImport(); g.arm(); g.on_ghost_enter(origin)
        # Even a large clock can't flip a fresh origin to a resume.
        assert g.validate(99999) is True
        assert g.recording


def test_replay_menu_origin_with_advanced_clock_is_resume():
    g = GhostImport(); g.arm(); g.on_ghost_enter(Screen.REPLAY_MENU)
    assert g.validate(8000) is False          # advanced clock => resume
    assert g.state == GhostState.ARMED and not g.recording


def test_replay_menu_origin_with_near_zero_clock_is_fresh():
    # A restart whose brief GHOST_RESET was missed: REPLAY_MENU origin but the
    # countdown is witnessed (clock <= START_ZERO_MS).
    g = GhostImport(); g.arm(); g.on_ghost_enter(Screen.REPLAY_MENU)
    assert g.validate(300) is True
    assert g.recording


def test_replay_menu_origin_countdown_then_window_elapses_is_fresh():
    g = GhostImport(); g.arm(); g.on_ghost_enter(Screen.REPLAY_MENU)
    # Clock not running yet (None) for the whole window => fresh start.
    res = None
    for _ in range(GhostImport.VALIDATE_FRAMES + 1):
        res = g.validate(None)
    assert res is True and g.recording


def test_leave_ghost_while_recording_returns_true_and_rearms():
    g = GhostImport(); g.arm(); g.on_ghost_enter(Screen.START_REPLAY)
    assert g.on_ghost_leave() is True
    assert g.state == GhostState.ARMED


def test_leave_ghost_when_not_recording_returns_false():
    g = GhostImport(); g.arm()
    assert g.on_ghost_leave() is False


def test_disarm_clears_recording():
    g = GhostImport(); g.arm(); g.on_ghost_enter(Screen.START_REPLAY)
    g.disarm()
    assert g.state == GhostState.IDLE and not g.recording
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_ghost_import.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mkw_tracker.lifecycle.ghost'`.

- [ ] **Step 3: Implement `GhostImport`**

```python
# mkw_tracker/lifecycle/ghost.py
"""GhostImport - one-shot 'record the next ghost replay' state machine.

cv2-free and free of any tracker references, so it is unit-testable in isolation.
Owned by RaceLifecycle, which drives it from screen changes + the race clock.

States:
  IDLE       - disarmed (default).
  ARMED      - waiting for a ghost to start from the beginning.
  RECORDING  - capturing a ghost (a 'validating' sub-window confirms it began at
               the start rather than resuming mid-replay).

Restart vs. resume (see the design spec):
  * A fresh ghost start is preceded by a reload, so old in {GHOST_RESET,
    START_REPLAY} is a decisive fresh-start signal.
  * A mid-replay resume is REPLAY_MENU -> GHOST with no reload; its race clock is
    already advanced. The clock is ground truth for the ambiguous REPLAY_MENU
    origin (and rescues a restart whose brief GHOST_RESET was missed).
"""
from enum import Enum, auto
from typing import Optional

from ..detection.screen import Screen


class GhostState(Enum):
    IDLE = auto()
    ARMED = auto()
    RECORDING = auto()


# Origins that mean a reload happened immediately before GHOST == a fresh start.
_FRESH_START_ORIGINS = {Screen.GHOST_RESET, Screen.START_REPLAY}


class GhostImport:
    # Race clock (ms) at/under which we count the start as "witnessed at zero"
    # (countdown / just-after-GO). Tuned against temp/ghostsample.mp4.
    START_ZERO_MS: int = 2000
    # Frames to wait for the clock to declare itself before defaulting to fresh
    # (~0.5s at 30fps; a real start shows its countdown well inside this).
    VALIDATE_FRAMES: int = 20

    def __init__(self):
        self.reset()

    def reset(self):
        self.state: GhostState = GhostState.IDLE
        self._validate_left: int = 0
        self._fresh_origin: bool = False

    @property
    def armed(self) -> bool:
        return self.state in (GhostState.ARMED, GhostState.RECORDING)

    @property
    def recording(self) -> bool:
        return self.state == GhostState.RECORDING

    def arm(self) -> None:
        if self.state == GhostState.IDLE:
            self.state = GhostState.ARMED

    def disarm(self) -> None:
        self.state = GhostState.IDLE
        self._validate_left = 0
        self._fresh_origin = False

    def on_ghost_enter(self, old: Screen) -> bool:
        """A transition into GHOST (old != GHOST). Begins a provisional recording
        iff ARMED. Returns True when a recording was started."""
        if self.state != GhostState.ARMED:
            return False
        self.state = GhostState.RECORDING
        self._validate_left = self.VALIDATE_FRAMES
        self._fresh_origin = old in _FRESH_START_ORIGINS
        return True

    def on_ghost_leave(self) -> bool:
        """Left GHOST. Returns True iff we were RECORDING (an abort before finish);
        the caller discards and we return to ARMED for the next start."""
        if self.state == GhostState.RECORDING:
            self.state = GhostState.ARMED
            self._validate_left = 0
            self._fresh_origin = False
            return True
        return False

    def validate(self, race_elapsed_ms: Optional[int]) -> Optional[bool]:
        """Feed the race-clock estimate each frame while RECORDING.
        Returns True (confirmed fresh start), False (resume -> back to ARMED), or
        None (still validating / not validating)."""
        if self.state != GhostState.RECORDING or self._validate_left <= 0:
            return None
        if self._fresh_origin:
            self._validate_left = 0
            return True
        if race_elapsed_ms is not None:
            self._validate_left = 0
            if race_elapsed_ms <= self.START_ZERO_MS:
                return True
            self.state = GhostState.ARMED          # advanced clock => resume
            return False
        # Clock not running yet (countdown): wait out the window, then default fresh.
        self._validate_left -= 1
        if self._validate_left <= 0:
            return True
        return None
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_ghost_import.py -q`
Expected: PASS (11 passed).

- [ ] **Step 5: Commit**

```bash
git add mkw_tracker/lifecycle/ghost.py tests/test_ghost_import.py
git commit -m "feat(ghost): GhostImport state machine (arm/start/validate/abort)"
```

---

### Task 2: `emit_ghost_import_state` protocol event

**Files:**
- Modify: `mkw_tracker/ipc/protocol.py` (after `emit_race_cleared`, ~line 116)
- Test: `tests/test_ghost_import.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_ghost_import.py
import json
from mkw_tracker.ipc.protocol import emit_ghost_import_state


def test_emit_ghost_import_state_shape():
    msg = json.loads(emit_ghost_import_state(True, False))
    assert msg == {"type": "ghost_import_state", "armed": True, "recording": False}
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_ghost_import.py::test_emit_ghost_import_state_shape -q`
Expected: FAIL — `ImportError: cannot import name 'emit_ghost_import_state'`.

- [ ] **Step 3: Implement the emitter**

```python
# mkw_tracker/ipc/protocol.py  (add after emit_race_cleared)
def emit_ghost_import_state(armed: bool, recording: bool) -> str:
    """Ghost-import arm/recording state, so the title-bar button reflects engine
    truth (armed on/off; recording == a ghost is actively being captured)."""
    return _emit("ghost_import_state", armed=bool(armed), recording=bool(recording))
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_ghost_import.py::test_emit_ghost_import_state_shape -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mkw_tracker/ipc/protocol.py tests/test_ghost_import.py
git commit -m "feat(ghost): emit_ghost_import_state IPC event"
```

---

### Task 3: Wire `GhostImport` into `RaceLifecycle`

**Files:**
- Modify: `mkw_tracker/lifecycle/race.py`
- Test: `tests/test_ghost_lifecycle.py` (create)

This task adds the lifecycle surface the main loop calls: `arm_ghost` / `disarm_ghost`
/ `ghost_armed` / `ghost_recording` / `effective_screen` / `validate_ghost_start`,
GHOST transition handling in `on_screen_change`, and the `ghost` flag in finalize.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ghost_lifecycle.py
import json
import pytest
from mkw_tracker.lifecycle.race import RaceLifecycle
from mkw_tracker.lifecycle.ghost import GhostState
from mkw_tracker.detection.screen import Screen


@pytest.fixture(autouse=True)
def _stub_minimap_db(monkeypatch):
    # _start_race calls these DB lookups; stub to None so a fresh start seeds nothing
    # and the lifecycle tests never touch the real SQLite store.
    import mkw_tracker.lifecycle.race as race_mod
    monkeypatch.setattr(race_mod, "get_minimap_roi", lambda *a, **k: None)
    monkeypatch.setattr(race_mod, "get_minimap_seed", lambda *a, **k: None)


class _Stub:
    """Minimal stand-in for every tracker the lifecycle resets/reads."""
    def __init__(self): self.reset_calls = 0
    def reset(self): self.reset_calls += 1
    def calibrate_from_race(self): return 0.5
    def set_roi(self, *a, **k): pass
    def start(self): pass


class _Sel(_Stub):
    class _S:
        course = "Choco Mountain"; character = "Mario"; kart = "K"; costume = "Base"
    state = _S()


class _Laps(_Stub):
    class _S:
        total_laps = 3; current_lap = 3
    state = _S()


class _Ts(_Stub):
    total_time = None
    splits = {}


class _Mm(_Stub):
    _calibrated = False
    _badge = None
    def seed(self, *a, **k): pass


class _Rec(_Stub):
    points = []


class _Ipc:
    def __init__(self): self.events = []
    def emit(self, e): self.events.append(e)


def _make(ipc=None):
    sel, laps, coins, ts = _Sel(), _Laps(), _Stub(), _Ts()
    finish, mush, mm, rec = _Stub(), _Stub(), _Mm(), _Rec()
    lc = RaceLifecycle(selection=sel, laps=laps, coins=coins, ts=ts, finish=finish,
                       mush=mush, minimap=mm, mm_rec=rec, ipc=ipc)
    return lc


def test_effective_screen_only_remaps_ghost_while_recording():
    lc = _make()
    assert lc.effective_screen(Screen.GHOST) == Screen.GHOST       # not recording
    lc.arm_ghost()
    assert lc.effective_screen(Screen.GHOST) == Screen.GHOST       # armed, not recording
    lc.on_screen_change(Screen.START_REPLAY, Screen.GHOST)         # provisional start
    assert lc.effective_screen(Screen.GHOST) == Screen.RACING      # recording
    assert lc.effective_screen(Screen.RACING) == Screen.RACING     # real racing untouched


def test_real_race_while_armed_is_not_a_ghost_recording():
    lc = _make()
    lc.arm_ghost()
    lc.on_screen_change(Screen.START_TIME_TRIAL, Screen.RACING)    # a REAL race
    assert lc.effective_screen(Screen.GHOST) == Screen.GHOST       # ghost not recording
    assert lc._ghost.state == GhostState.ARMED                     # still armed


def test_ghost_abort_discards_and_stays_armed_without_emitting_run():
    ipc = _Ipc(); lc = _make(ipc)
    lc.arm_ghost()
    lc.on_screen_change(Screen.GHOST_RESET, Screen.GHOST)          # start
    lc.on_screen_change(Screen.GHOST, Screen.REPLAY_MENU)          # abort before finish
    assert lc._ghost.state == GhostState.ARMED
    assert not any('"type": "run_finalized"' in e for e in ipc.events)


def test_validate_resume_discards_provisional_capture():
    lc = _make()
    lc.arm_ghost()
    lc.on_screen_change(Screen.REPLAY_MENU, Screen.GHOST)          # ambiguous origin
    lc.validate_ghost_start(8000)                                  # advanced clock => resume
    assert lc.effective_screen(Screen.GHOST) == Screen.GHOST       # reverted
    assert lc._ghost.state == GhostState.ARMED


def test_ghost_finalize_tags_source_nulls_identity_keeps_course_and_disarms():
    ipc = _Ipc(); lc = _make(ipc)
    lc.arm_ghost()
    lc.on_screen_change(Screen.GHOST_RESET, Screen.GHOST)
    lc.validate_ghost_start(None)
    lc._ts.total_time = "1:40.000"                                 # finish locked
    lc.finalize_on_finish()
    finals = [json.loads(e) for e in ipc.events if '"run_finalized"' in e]
    assert len(finals) == 1
    r = finals[0]
    assert r["source"] == "ghost"
    assert r["course"] == "Choco Mountain"
    assert r["character"] is None and r["kart"] is None and r["costume"] is None
    assert not lc.ghost_armed                                      # auto-disarmed
    states = [json.loads(e) for e in ipc.events if '"ghost_import_state"' in e]
    assert states[-1] == {"type": "ghost_import_state", "armed": False, "recording": False}
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_ghost_lifecycle.py -q`
Expected: FAIL — `AttributeError: 'RaceLifecycle' object has no attribute 'arm_ghost'`.

- [ ] **Step 3: Add the ghost member + methods to `RaceLifecycle.__init__`**

In `mkw_tracker/lifecycle/race.py`, add the import at the top:

```python
from .ghost import GhostImport
```

At the end of `__init__` (after `self.current_frame = None`):

```python
        # One-shot "import the next ghost" state machine (see lifecycle/ghost.py).
        self._ghost = GhostImport()
```

- [ ] **Step 4: Add the public ghost surface (new methods on `RaceLifecycle`)**

Add these methods to the class (e.g. just after `on_screen_change`):

```python
    # ── Ghost import surface (driven by main loop + IPC) ─────────────────────

    @property
    def ghost_armed(self) -> bool:
        return self._ghost.armed

    @property
    def ghost_recording(self) -> bool:
        return self._ghost.recording

    def arm_ghost(self) -> None:
        self._ghost.arm()
        self._emit_ghost_state()

    def disarm_ghost(self) -> None:
        if self._ghost.recording:
            self._clear_race_state()          # drop any in-progress capture
        self._ghost.disarm()
        self._emit_ghost_state()

    def effective_screen(self, real: "Screen") -> "Screen":
        """GHOST -> RACING only while a ghost is being recorded; else unchanged.
        Real RACING is always RACING regardless of arm state."""
        if real == Screen.GHOST and self._ghost.recording:
            return Screen.RACING
        return real

    def validate_ghost_start(self, race_elapsed_ms) -> None:
        """Per-frame restart-vs-resume check while recording. On a resume the
        provisional capture is discarded and we re-arm."""
        res = self._ghost.validate(race_elapsed_ms)
        if res is False:
            self._clear_race_state()
            self._emit_ghost_state()

    def _emit_ghost_state(self) -> None:
        if self._ipc is not None:
            from ..ipc.protocol import emit_ghost_import_state
            self._ipc.emit(emit_ghost_import_state(self._ghost.armed, self._ghost.recording))
```

- [ ] **Step 5: Handle GHOST transitions in `on_screen_change`**

At the END of `on_screen_change` (after the existing RESET block), add:

```python
        # ── Ghost import: GHOST treated as a private RACING while recording ──
        if new == Screen.GHOST and old != Screen.GHOST:
            if self._ghost.on_ghost_enter(old):
                self._start_race(old)          # provisional; validated over next frames
                self._emit_ghost_state()
        elif old == Screen.GHOST and new != Screen.GHOST:
            if self._ghost.on_ghost_leave():   # was recording -> aborted before finish
                self._clear_race_state()       # discard, stay armed, no run emitted
                self._emit_ghost_state()
            elif self._finalized:              # a finished ghost left the screen
                self._clear_race_state()       # clear the lingering finished state
```

- [ ] **Step 6: Thread the `ghost` flag through finalize**

Replace the identity-gathering + emit in `_finalize_recording` so a ghost nulls
identity, keeps course, tags `source`, skips minimap calibration, and disarms.

Find in `_finalize_recording`:

```python
        sel       = self._selection.state
        course    = sel.course
        character = sel.character
        costume   = sel.costume
```

Replace with:

```python
        is_ghost  = self._ghost.recording
        sel       = self._selection.state
        course    = sel.course
        # A ghost replays the *recorded* loadout, not the live one we detected, so
        # null identity to force manual entry. Course is reliable (Course Select).
        character = None if is_ghost else sel.character
        costume   = None if is_ghost else sel.costume
```

Find the calibration guard:

```python
        if completed and best_total_time and not self._minimap._calibrated:
```

Replace with (skip calibration for ghosts — character is unreliable):

```python
        if completed and best_total_time and not is_ghost and not self._minimap._calibrated:
```

Find the `kart` line inside the emitted payload:

```python
                "kart":       sel.kart,
```

Replace with:

```python
                "kart":       None if is_ghost else sel.kart,
```

Find the emit + `self._finalized = True`:

```python
            self._ipc.emit(emit_run_finalized({
```

Insert a `source` key inside that dict (after `"course": course,`):

```python
                "source":     "ghost" if is_ghost else None,
```

After `self._finalized = True`, add the auto-disarm:

```python
        if is_ghost:
            self._ghost.disarm()
            self._emit_ghost_state()
```

- [ ] **Step 6b: Seed the minimap course-only for a ghost in `_start_race`**

A ghost's live-detected character is unreliable, so don't look up (or trust) a
per-character confident threshold for it — fall back to the seed default. In
`_start_race`, find:

```python
                stored_conf: Optional[float] = None
                if character:
```

Replace the condition with (skip the per-character lookup while recording a ghost):

```python
                stored_conf: Optional[float] = None
                if character and not self._ghost.recording:
```

- [ ] **Step 7: Run the tests**

Run: `python -m pytest tests/test_ghost_lifecycle.py tests/test_ghost_import.py -q`
Expected: PASS. Then `python -m pytest tests/ -q` — Expected: no regressions.

- [ ] **Step 8: Commit**

```bash
git add mkw_tracker/lifecycle/race.py tests/test_ghost_lifecycle.py
git commit -m "feat(ghost): drive GhostImport from RaceLifecycle (capture/finalize)"
```

---

### Task 4: Main-loop wiring — `eff_screen`, quiet emits, IPC command

**Files:**
- Modify: `mkw_tracker/main.py`

No unit test (the orchestration loop); covered by the clip check (Task 5).

- [ ] **Step 1: Add the `set_ghost_import` IPC handler**

In `_handle_ipc_command`, add a branch (e.g. after the `set_selection` branch):

```python
    elif t == "set_ghost_import":
        if bool(msg.get("enabled", False)):
            lifecycle.arm_ghost()
        else:
            lifecycle.disarm_ghost()
```

- [ ] **Step 2: Compute `eff_screen` and feed it to every tracker**

In the main loop, find:

```python
        screen, perf  = detector.update(frame)
        selection     = tracker.update(frame, screen, perf.current_score)

        _race_complete = ts.total_time is not None

        if not _race_complete:
            lap_state, lap_inc = laps.update(frame, screen)
            coin_state         = coins.update(frame, screen)
            mush_state         = mush.update(frame, screen)
            lapstats.update(mush_state.count)
            lapstats.update_coins(coin_state.coins)
            mm_state           = minimap.update(frame, screen)
            race_elapsed = timer.update(frame, screen)
            if screen == Screen.RACING:
```

Replace with (`eff_screen` drives capture; `selection` stays on the real screen):

```python
        screen, perf  = detector.update(frame)
        eff_screen    = lifecycle.effective_screen(screen)
        selection     = tracker.update(frame, screen, perf.current_score)

        _race_complete = ts.total_time is not None

        if not _race_complete:
            lap_state, lap_inc = laps.update(frame, eff_screen)
            coin_state         = coins.update(frame, eff_screen)
            mush_state         = mush.update(frame, eff_screen)
            lapstats.update(mush_state.count)
            lapstats.update_coins(coin_state.coins)
            mm_state           = minimap.update(frame, eff_screen)
            race_elapsed = timer.update(frame, eff_screen)
            # Restart-vs-resume validation for a provisional ghost capture.
            lifecycle.validate_ghost_start(race_elapsed)
            if eff_screen == Screen.RACING:
```

- [ ] **Step 3: Feed `eff_screen` to the finish latch + skip ghost calibration**

Find:

```python
        finish_just_detected  = (finish.update(frame, screen, bool(_on_final_lap),
                                               lap_inc=lap_inc,
                                               estimate_ms=race_elapsed)
                                 and ts.total_time is None)
```

Replace `frame, screen,` with `frame, eff_screen,`:

```python
        finish_just_detected  = (finish.update(frame, eff_screen, bool(_on_final_lap),
                                               lap_inc=lap_inc,
                                               estimate_ms=race_elapsed)
                                 and ts.total_time is None)
```

Find the two calibration blocks and gate each on the REAL race (not a ghost):

```python
        if finish_just_detected and not minimap._calibrated:
```
→
```python
        if finish_just_detected and not minimap._calibrated and screen == Screen.RACING:
```

and

```python
        if lap_inc and not minimap._calibrated:
```
→
```python
        if lap_inc and not minimap._calibrated and screen == Screen.RACING:
```

- [ ] **Step 4: Feed `eff_screen` to the timestamp tracker**

Find:

```python
            ts_state = ts.update(
                frame, screen,
                capture_now=lap_inc or finish_just_detected,
```

Replace `frame, screen,` with `frame, eff_screen,`.

- [ ] **Step 5: Keep live emits on the REAL screen (quiet ghost capture)**

Gate each per-frame live emit so a ghost (real screen `GHOST`) broadcasts nothing.

`race_time` — find:

```python
            if race_elapsed is not None:
                _now_rt = time.perf_counter()
```
→
```python
            if race_elapsed is not None and screen == Screen.RACING:
                _now_rt = time.perf_counter()
```

`split_recorded` — find:

```python
        for lap, split_time in ts.splits.items():
            if lap not in _emitted_splits:
```
→
```python
        for lap, split_time in ts.splits.items():
            if lap not in _emitted_splits and screen == Screen.RACING:
```

`lap_update` — find `if lap_key != _prev_lap and any(lap_key):` →
`if lap_key != _prev_lap and any(lap_key) and screen == Screen.RACING:`

`coin_update` — find `if coin_state.coins != _prev_coins and coin_state.coins is not None:` →
append ` and screen == Screen.RACING:`

`mush_update` — find `if mush_state.count != _prev_mush:` →
`if mush_state.count != _prev_mush and screen == Screen.RACING:`

`finish` (deferred) — find `if finish_just_detected and not _prev_finish and not _want_finish_emit:` →
append ` and screen == Screen.RACING:`

(The `minimap_update` emit block is already `if screen == Screen.RACING:` — leave it. `finalize_on_finish()` is gated on `ts.total_time`, NOT the screen — leave it so the ghost run IS finalized.)

- [ ] **Step 6: Smoke-run the engine headlessly**

Run: `python -m mkw_tracker --no-ipc --no-display --video temp/ghostsample.mp4 --video-fps 0 --video-once`
Expected: it runs to EOF and exits 0 with screen-transition logs (no crash). (Behavioural assertions live in Task 5.)

- [ ] **Step 7: Commit**

```bash
git add mkw_tracker/main.py
git commit -m "feat(ghost): main-loop eff_screen capture + quiet emits + arm IPC"
```

---

### Task 5: Clip integration check (manual, against `temp/ghostsample.mp4`)

**Files:**
- Create: `scripts/ghost_import_clip_check.py`

A standalone harness that arms ghost import, runs the **real** detector + lifecycle +
trackers over the clip, captures the emitted `run_finalized` / `ghost_import_state`
lines, and asserts the expected outcome. Not in CI (needs the 2 GB clip).

- [ ] **Step 1: Write the harness**

```python
# scripts/ghost_import_clip_check.py
"""Manual integration check for ghost import against temp/ghostsample.mp4.

Runs the real engine over the clip with ghost import armed and asserts:
  * exactly ONE run_finalized with source == "ghost" (the full playthrough),
    course == Choco Mountain, with a non-null total_time;
  * the real race in the middle yields a NON-ghost run_finalized;
  * the second identical playthrough is NOT recorded (only one ghost run total).

Usage:  python scripts/ghost_import_clip_check.py [path-to-clip]
"""
import json
import sys

from mkw_tracker.config.settings import get_settings
from mkw_tracker.database.migrations import apply_migrations


def main(clip: str) -> int:
    apply_migrations()
    settings = get_settings()
    lang = settings.get("switch2_language", "en_uk") or "en_uk"

    from mkw_tracker.detection.screen import Screen, ScreenDetector
    from mkw_tracker.detection.selection import SelectionTracker
    from mkw_tracker.race.laps import LapTracker
    from mkw_tracker.race.coins import CoinTracker
    from mkw_tracker.race.timestamp import TimestampTracker
    from mkw_tracker.race.finish import FinishLatch, load_finish_templates
    from mkw_tracker.race.mushrooms import MushroomTracker, load_mushroom_templates
    from mkw_tracker.race.lapstats import LapStatsTracker
    from mkw_tracker.race.timer import RaceTimer
    from mkw_tracker.minimap.tracker import MinimapTracker
    from mkw_tracker.minimap.recorder import MinimapRecorder
    from mkw_tracker.lifecycle.race import RaceLifecycle
    from mkw_tracker.utils.camera import VideoFileSource

    load_finish_templates(switch2_language=lang)
    load_mushroom_templates(switch2_language=lang)

    events = []

    class _Ipc:
        def emit(self, e): events.append(e)

    detector = ScreenDetector(switch2_language=lang)
    tracker = SelectionTracker(switch2_language=lang)
    laps, coins, ts = LapTracker(), CoinTracker(), TimestampTracker()
    timer = RaceTimer()
    finish = FinishLatch(templates=timer._templates)
    mush, minimap, rec = MushroomTracker(), MinimapTracker(), MinimapRecorder()
    lapstats = LapStatsTracker()
    lc = RaceLifecycle(selection=tracker, laps=laps, coins=coins, ts=ts, finish=finish,
                       mush=mush, lapstats=lapstats, minimap=minimap, mm_rec=rec,
                       timer=timer, ipc=_Ipc())
    detector.on_screen_change = lc.on_screen_change
    lc.arm_ghost()

    cap = VideoFileSource(clip, loop=False, target_fps=0)
    import numpy as np, cv2
    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        if frame.shape[1] != 1920 or frame.shape[0] != 1080:
            frame = cv2.resize(frame, (1920, 1080), interpolation=cv2.INTER_LINEAR)
        lc.current_frame = frame
        screen, _ = detector.update(frame)
        eff = lc.effective_screen(screen)
        tracker.update(frame, screen, 0.0)
        if ts.total_time is None:
            ls, li = laps.update(frame, eff)
            coins.update(frame, eff); mush.update(frame, eff)
            ms = minimap.update(frame, eff)
            re = timer.update(frame, eff)
            lc.validate_ghost_start(re)
            if eff == Screen.RACING:
                rec.update(ms, ls.current_lap, re)
            on_final = ls.current_lap is not None and ls.total_laps and ls.current_lap == ls.total_laps
            fjd = finish.update(frame, eff, bool(on_final), lap_inc=li, estimate_ms=re) and ts.total_time is None
            tslap = (ls.current_lap - 1) if li and ls.current_lap is not None else ls.current_lap
            ts.update(frame, eff, capture_now=li or fjd, lap_number=tslap, is_finish=fjd)
        if ts.total_time is not None:
            lc.finalize_on_finish()

    finals = [json.loads(e) for e in events if '"run_finalized"' in e]
    ghosts = [r for r in finals if r.get("source") == "ghost"]
    print(f"run_finalized total={len(finals)}  ghost={len(ghosts)}")
    for r in ghosts:
        print(f"  ghost: course={r.get('course')!r} total={r.get('total_time')!r} "
              f"char={r.get('character')!r} points={len(r.get('points', []))}")
    ok = (len(ghosts) == 1
          and ghosts[0].get("course") == "Choco Mountain"
          and ghosts[0].get("total_time") is not None
          and ghosts[0].get("character") is None)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "temp/ghostsample.mp4"))
```

- [ ] **Step 2: Run it**

Run: `python scripts/ghost_import_clip_check.py`
Expected: prints `ghost=1`, `course='Choco Mountain'`, a non-null total, and `PASS`.

- [ ] **Step 3: Tune if needed**

If the resume is captured or the restart is missed, adjust `GhostImport.START_ZERO_MS`
(lower if a resume slips through; higher if a real start is rejected) and/or
`VALIDATE_FRAMES`, re-run. If the total time is null, the ghost's freeze evaded the
`FinishLatch` — verify against the clip; the popup's editable total is the fallback.

- [ ] **Step 4: Commit**

```bash
git add scripts/ghost_import_clip_check.py
git commit -m "test(ghost): manual clip integration check for ghost capture"
```

---

# Phase 2 — Frontend (button, modal, popup, label)

### Task 6: "Watching a ghost…" activity label

**Files:**
- Modify: `src/lib/playerCard.js:7` (the `SETUP` map)
- Test: `src/lib/playerCard.test.js`

- [ ] **Step 1: Write the failing test**

```js
// src/lib/playerCard.test.js — add as a new describe block (viewModel is already imported)
describe("ghost activity label", () => {
  it("labels ghost screens as Watching a ghost", () => {
    const vm = viewModel({ player_id: 99, name: "Paul", online: true, screen: "GHOST", updated_at: 1000 }, () => 2000);
    expect(vm.primary).toEqual({ kind: "activity", text: "Watching a ghost…" });
  });
});
```

(`viewModel(e, now, delayed, opts)` is the presence→view-model mapper, already imported
at the top of the file. A unique `player_id` avoids any leftover race-hold from earlier
tests; `GHOST` is not a HOLD screen, so it resolves to the activity branch.)

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run src/lib/playerCard.test.js`
Expected: FAIL — receives `{ kind: "activity", text: "In the menus" }`.

- [ ] **Step 3: Add the ghost screens to the activity map**

In `src/lib/playerCard.js`, change the `SETUP` map (line 7-8):

```js
const SETUP = { CHARACTER_SELECT: "Choosing character…", KART_SELECT: "Choosing kart…", COURSE_SELECT: "Choosing track…",
                START_TIME_TRIAL: "Starting time trial…",
                GHOST: "Watching a ghost…", START_REPLAY: "Watching a ghost…", REPLAY_MENU: "Watching a ghost…" };
```

- [ ] **Step 4: Run to verify it passes**

Run: `npx vitest run src/lib/playerCard.test.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lib/playerCard.js src/lib/playerCard.test.js
git commit -m "feat(ghost): 'Watching a ghost' activity label for ghost screens"
```

---

### Task 7: Two-step submit + "From ghost" marker in `RunReviewModal`

**Files:**
- Modify: `src/components/RunReviewModal.svelte`

- [ ] **Step 1: Add the `isGhost` prop + confirm state**

In the `<script>`, add to the props (near `export let playSound = true;`):

```js
  export let isGhost = false;      // ghost-imported run: needs an extra submit confirm
```

Add a state var (near `confirmingDiscard`):

```js
  let confirmingSubmit = false;    // two-step submit for ghost imports
```

In the reactive identity reset block (where `confirmingDiscard = false;` is set on a
new run), also reset `confirmingSubmit = false;`.

- [ ] **Step 2: Gate submit behind the confirm for ghosts**

Replace the `submit()` function body's first line so a ghost arms the confirm first:

Find:

```js
  function submit() {
    if (!canSubmit) return;
```

Replace with:

```js
  function submit() {
    if (!canSubmit) return;
    if (isGhost && !confirmingSubmit) { confirmingSubmit = true; return; }
```

- [ ] **Step 3: Show the "From ghost" marker + confirm hint in the markup**

In the header (`.rv-head-l`), after the PB chip, add:

```svelte
        {#if isGhost}<span class="rv-pb" title="Imported from an in-game ghost">GHOST</span>{/if}
```

In the footer, replace the Submit branch hint/button so a ghost shows an explicit
"Are you sure?" step. Find:

```svelte
        <div class="rv-foot-right">
          <span class="rv-hint">{canSubmit ? "Ready to submit" : "Fill the flagged fields to submit"}</span>
          <button class="rv-btn rv-btn-primary" on:click={submit} disabled={!canSubmit}>Submit</button>
        </div>
```

Replace with:

```svelte
        <div class="rv-foot-right">
          <span class="rv-hint">
            {confirmingSubmit ? "Submit this as one of your runs?"
              : canSubmit ? "Ready to submit" : "Fill the flagged fields to submit"}
          </span>
          {#if confirmingSubmit}
            <button class="rv-btn rv-btn-ghost" on:click={() => (confirmingSubmit = false)}>Cancel</button>
          {/if}
          <button class="rv-btn rv-btn-primary" on:click={submit} disabled={!canSubmit}>
            {confirmingSubmit ? "Yes, submit" : "Submit"}
          </button>
        </div>
```

- [ ] **Step 4: Verify type-check + build**

Run: `npm run check`
Expected: 0 errors / 0 warnings.

- [ ] **Step 5: Commit**

```bash
git add src/components/RunReviewModal.svelte
git commit -m "feat(ghost): two-step submit + GHOST marker in RunReviewModal"
```

---

### Task 8: Arm warning modal component

**Files:**
- Create: `src/components/GhostImportWarning.svelte`

- [ ] **Step 1: Create the modal (matches the OBS-idiom tokens used by RunReviewModal)**

```svelte
<script>
  // GhostImportWarning.svelte — confirm before arming ghost import.
  //   events: enable · cancel
  import { createEventDispatcher } from "svelte";
  import { fade, scale } from "svelte/transition";
  import { quintOut } from "svelte/easing";
  const dispatch = createEventDispatcher();
</script>

<div class="gi-backdrop" transition:fade={{ duration: 120 }}>
  <div class="gi-dialog" role="dialog" aria-modal="true" aria-labelledby="gi-title"
       in:scale={{ duration: 170, start: 0.97, opacity: 0, easing: quintOut }}>
    <header class="gi-head"><h2 id="gi-title" class="gi-title">Import PB from ghost</h2></header>
    <div class="gi-body">
      <p>When this is turned on, the <strong>next ghost you watch</strong> will be added as one
         of your runs.</p>
      <p class="gi-warn">This is very hard to undo on the database end, so please don't misuse it.</p>
    </div>
    <footer class="gi-foot">
      <button class="gi-btn gi-btn-ghost" on:click={() => dispatch("cancel")}>Cancel</button>
      <button class="gi-btn gi-btn-primary" on:click={() => dispatch("enable")}>OK, enable</button>
    </footer>
  </div>
</div>

<style>
  .gi-backdrop { position: fixed; inset: 0; z-index: 200; background: rgba(0,0,0,.62);
    display: flex; align-items: center; justify-content: center; padding: 24px; }
  .gi-dialog { width: 100%; max-width: 360px; display: flex; flex-direction: column;
    background: var(--panel); border: 1px solid var(--bd); border-radius: var(--r);
    box-shadow: 0 16px 44px rgba(0,0,0,.5); overflow: hidden; }
  .gi-head { padding: .6rem .85rem; border-bottom: 1px solid var(--bd-soft); }
  .gi-title { font-size: .82rem; font-weight: 600; color: var(--tx); }
  .gi-body { padding: .7rem .85rem; display: flex; flex-direction: column; gap: .5rem;
    font-size: .74rem; color: var(--tx-mut); line-height: 1.45; }
  .gi-warn { color: var(--warn); }
  .gi-foot { display: flex; align-items: center; justify-content: flex-end; gap: .55rem;
    padding: .55rem .85rem; border-top: 1px solid var(--bd-soft); }
  .gi-btn { font-family: inherit; font-size: .72rem; cursor: pointer; padding: .26rem .8rem;
    border-radius: var(--r); border: 1px solid var(--bd); background: var(--panel-2); color: var(--tx-mut);
    transition: background-color .12s, border-color .12s, color .12s; }
  .gi-btn-ghost:hover { background: var(--raised); color: var(--tx); }
  .gi-btn-primary { background: var(--accent-bg); border-color: var(--accent); color: var(--tx); }
  .gi-btn-primary:hover { background: var(--raised); }
</style>
```

- [ ] **Step 2: Type-check**

Run: `npm run check`
Expected: 0 errors.

- [ ] **Step 3: Commit**

```bash
git add src/components/GhostImportWarning.svelte
git commit -m "feat(ghost): arm warning modal component"
```

---

### Task 9: Wire the button, modal, state, and `isGhost` into `App.svelte`

**Files:**
- Modify: `src/App.svelte`

- [ ] **Step 1: Import the modal + add state**

Add to the imports (near `import RunReviewModal ...`):

```js
  import GhostImportWarning from "./components/GhostImportWarning.svelte";
```

Add state vars (near `let reviewQueue = [];`):

```js
  let ghostArmed = false;          // mirrors the engine ghost_import_state
  let ghostRecording = false;
  let ghostWarnOpen = false;       // arm-confirm modal visibility
```

- [ ] **Step 2: Handle the `ghost_import_state` event**

In `handleMsg`'s `switch`, add a case (near `case "run_needs_review":`):

```js
      case "ghost_import_state":
        ghostArmed = !!msg.armed;
        ghostRecording = !!msg.recording;
        break;
```

- [ ] **Step 3: Add the click handlers**

Near the run-review action functions, add:

```js
  function onGhostButton() {
    if (ghostArmed) { send({ type: "set_ghost_import", enabled: false }); }
    else { ghostWarnOpen = true; }
  }
  function ghostEnable() { send({ type: "set_ghost_import", enabled: true }); ghostWarnOpen = false; }
```

- [ ] **Step 4: Add the title-bar button (monitor view, by Settings)**

In the `<svelte:fragment slot="settings">`, add the ghost button BEFORE the Settings
button, inside the `{#if appView === "main"}` block:

```svelte
      {#if appView === "main"}
        {#if !wizardOpen}
          <button class="btn-hdr btn-ghost-import" class:armed={ghostArmed}
                  on:click={onGhostButton}
                  title={ghostArmed ? "Armed: the next ghost will be imported (click to cancel)" : "Import a PB from an in-game ghost"}>
            {#if ghostArmed}<span class="gi-dot"></span>{/if}
            {ghostRecording ? "Importing ghost…" : ghostArmed ? "Ghost import armed" : "Import PB from ghost"}
          </button>
        {/if}
```

(Leave the existing Settings `{#if wizardOpen} … {:else} … {/if}` block as-is, right
after this.)

- [ ] **Step 5: Style the button (armed = filled accent + dot)**

In the `<style>` (near the `.btn-edit` rules), add:

```css
  .btn-ghost-import { color: var(--tx-mut); border: 1px solid var(--bd);
    display: inline-flex; align-items: center; gap: 5px; }
  .btn-ghost-import:hover { background: var(--raised); color: var(--tx); }
  .btn-ghost-import.armed { background: var(--accent-bg); border-color: var(--accent); color: var(--tx); }
  .gi-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--accent);
    box-shadow: 0 0 0 0 var(--accent); animation: gi-pulse 1.6s infinite; }
  @keyframes gi-pulse { 0% { box-shadow: 0 0 0 0 color-mix(in srgb, var(--accent) 70%, transparent); }
    70% { box-shadow: 0 0 0 5px transparent; } 100% { box-shadow: 0 0 0 0 transparent; } }
```

- [ ] **Step 6: Mount the warning modal + pass `isGhost` to the review modal**

In the `RunReviewModal` element's props, insert an `isGhost` line immediately after the
existing `isPb={reviewHead.isPb}` line, so the block reads:

```svelte
    isPb={reviewHead.isPb}
    isGhost={reviewHead.run?.source === "ghost"}
    pbBest={pbBestLookup}
```

Then, after the closing `{/if}` of the `{#if reviewHead}` block, mount the warning modal:

```svelte
{#if ghostWarnOpen}
  <GhostImportWarning on:enable={ghostEnable} on:cancel={() => (ghostWarnOpen = false)} />
{/if}
```

- [ ] **Step 7: Type-check + build**

Run: `npm run check && npm run build`
Expected: 0 errors; build succeeds.

- [ ] **Step 8: Commit**

```bash
git add src/App.svelte
git commit -m "feat(ghost): title-bar import button + arm modal + isGhost wiring"
```

---

# Phase 3 — Server (dedup-or-insert, audit, mark)

### Task 10: Schema — `runs.source` + `ghost_imports`

**Files:**
- Modify: `server/schema.sql`
- Modify: `pi/src/db/connect.ts`
- Test: `pi/src/db/schema.test.ts` (append)

- [ ] **Step 1: Write the failing test**

```ts
// pi/src/db/schema.test.ts — add a new `it` inside the existing `describe('applySchema', ...)`
// (openDb + applySchema are already imported at the top of the file).
  it('has runs.source and a ghost_imports table', () => {
    const db = openDb(':memory:');
    applySchema(db);
    const cols = (db.prepare('PRAGMA table_info(runs)').all() as { name: string }[]).map(c => c.name);
    expect(cols).toContain('source');
    expect(() => db.prepare('SELECT id, run_id, player_id, course_id, cc, total_time_ms, action FROM ghost_imports').all()).not.toThrow();
  });
```

Optionally add `'ghost_imports'` to the file's top-level `TABLES` array so the
"creates every canonical table" test covers it too.

- [ ] **Step 2: Run to verify it fails**

Run: `cd pi && npx vitest run src/db/schema.test.ts`
Expected: FAIL — `source` missing / `no such table: ghost_imports`.

- [ ] **Step 3: Add to `server/schema.sql`**

Add `source TEXT` to the `runs` table (after `is_pb ...`, before `created_at`):

```sql
    is_pb          INTEGER NOT NULL DEFAULT 0,
    source         TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
```

Add the audit table (after the `runs`/`run_points` tables):

```sql
CREATE TABLE IF NOT EXISTS ghost_imports (
    id            INTEGER PRIMARY KEY,
    run_id        INTEGER REFERENCES runs(id),
    player_id     INTEGER NOT NULL REFERENCES players(id),
    course_id     INTEGER NOT NULL REFERENCES courses(id),
    cc            INTEGER NOT NULL,
    total_time_ms INTEGER,
    action        TEXT NOT NULL CHECK (action IN ('enriched','new')),
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
```

- [ ] **Step 4: Add additive migrations to `pi/src/db/connect.ts`**

In `applySchema`, after the existing `ALTER TABLE runs ADD COLUMN ...` block, add:

```ts
  // Additive: ghost-import source mark (nullable; 'ghost' when ghost-sourced).
  try { db.exec('ALTER TABLE runs ADD COLUMN source TEXT'); } catch { /* present */ }
  // Additive: ghost import audit log (no-op once present).
  db.exec(`CREATE TABLE IF NOT EXISTS ghost_imports (
    id INTEGER PRIMARY KEY, run_id INTEGER REFERENCES runs(id),
    player_id INTEGER NOT NULL REFERENCES players(id),
    course_id INTEGER NOT NULL REFERENCES courses(id),
    cc INTEGER NOT NULL, total_time_ms INTEGER,
    action TEXT NOT NULL CHECK (action IN ('enriched','new')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')))`);
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd pi && npx vitest run src/db/schema.test.ts`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add server/schema.sql pi/src/db/connect.ts pi/src/db/schema.test.ts
git commit -m "feat(ghost): runs.source column + ghost_imports audit table"
```

---

### Task 11: `findGhostMatch` + `enrichRunFromGhost` + `upsertRun` source

**Files:**
- Modify: `pi/src/db/types.ts` (add `source?` to `AttemptPayload`)
- Modify: `pi/src/db/ingest.ts`
- Test: `pi/src/db/ingest.test.ts` (append)

- [ ] **Step 1: Write the failing tests**

The file already has a `base()` helper (in-memory schema'd DB seeded with season 1,
player 1, course 1 = `rainbow_road`). Reuse it. Add `findGhostMatch`/`enrichRunFromGhost`
to the existing `import { upsertRun, OVER_LIMIT_MS } from './ingest';` line.

```ts
// pi/src/db/ingest.test.ts — append (uses the file's existing base() helper)
describe('ghost dedup + enrich', () => {
  it('findGhostMatch matches a finished run by exact total_time_ms', () => {
    const db = base();
    // a carryover-style finished run, no laps/points/identity
    db.exec("INSERT INTO runs(id,attempt_id,season_id,player_id,course_id,cc,status,provenance,total_time_ms) " +
            "VALUES (50,'cv',1,1,1,150,'finished','carryover',100000)");
    expect(findGhostMatch(db, 1, 1, 1, 150, 100000)).toBe(50);
    expect(findGhostMatch(db, 1, 1, 1, 150, 100001)).toBeNull();   // off by 1ms
    expect(findGhostMatch(db, 1, 2, 1, 150, 100000)).toBeNull();   // other player
  });

  it('enrichRunFromGhost gap-fills identity + adds laps/points + marks source', () => {
    const db = base();
    db.exec("INSERT INTO runs(id,attempt_id,season_id,player_id,course_id,cc,status,provenance,total_time_ms) " +
            "VALUES (50,'cv',1,1,1,150,'finished','carryover',100000)");
    const res = enrichRunFromGhost(db, 50, {
      attempt_id: 'g1', course: 'Rainbow Road', status: 'finished', total_time: '1:40.000',
      character: 'Mario', kart: 'K', costume: 'Base', total_laps: 1,
      laps: [{ lap: 1, time_ms: 100000, time_str: '1:40.000', coins: 3, shrooms: 1 }],
      points: [[0, 1, 2, 1, 1]], source: 'ghost',
    } as any);
    const run = db.prepare('SELECT character, kart, costume, source FROM runs WHERE id=50').get() as any;
    expect(run.character).toBe('Mario');
    expect(run.source).toBe('ghost');
    expect((db.prepare('SELECT COUNT(*) c FROM run_laps WHERE run_id=50').get() as any).c).toBe(1);
    expect((db.prepare('SELECT COUNT(*) c FROM run_points WHERE run_id=50').get() as any).c).toBe(1);
    expect(res.trailAdded).toBe(true);
  });

  it('enrich never overwrites existing identity or existing laps', () => {
    const db = base();
    db.exec("INSERT INTO runs(id,attempt_id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,character) " +
            "VALUES (60,'lv',1,1,1,150,'finished','live',100000,'Peach')");
    db.exec("INSERT INTO run_laps(run_id,lap_index,lap_time_ms,lap_time_str,coins,shrooms) VALUES (60,1,100000,'1:40.000',9,9)");
    const res = enrichRunFromGhost(db, 60, {
      attempt_id: 'g2', course: 'Rainbow Road', status: 'finished', character: 'Mario',
      total_laps: 1, laps: [{ lap: 1, time_ms: 100000, time_str: '1:40.000', coins: 1, shrooms: 1 }],
      points: [[0, 1, 2, 1, 1]], source: 'ghost',
    } as any);
    const run = db.prepare('SELECT character FROM runs WHERE id=60').get() as any;
    expect(run.character).toBe('Peach');                       // not overwritten
    const lap = db.prepare('SELECT coins FROM run_laps WHERE run_id=60 AND lap_index=1').get() as any;
    expect(lap.coins).toBe(9);                                 // existing laps kept
    expect(res.trailAdded).toBe(true);                         // had no points -> trail added
  });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd pi && npx vitest run src/db/ingest.test.ts`
Expected: FAIL — `findGhostMatch`/`enrichRunFromGhost` are not exported.

- [ ] **Step 3: Add `source` to the payload type**

In `pi/src/db/types.ts`, add to `AttemptPayload`:

```ts
  source?: string | null;     // 'ghost' when re-derived from an in-game ghost replay
```

- [ ] **Step 4: Persist `source` in `upsertRun`**

In `pi/src/db/ingest.ts`, extend the `INSERT INTO runs(...)` column list + values to
carry `source`. Change the column list to end with `is_pb, source)` and the VALUES
placeholders accordingly:

```ts
    const info = db.prepare(
      `INSERT INTO runs(attempt_id, season_id, player_id, course_id, cc, status, provenance,
                        started_at, ended_at, total_time_ms, total_time_str, character, kart, costume,
                        coins_gained, coins_lost, mushrooms_used, is_pb, source)
       VALUES (?,?,?,?,?,?, 'live', ?,?,?,?,?,?,?, ?,?,?, 0, ?)`
    ).run(p.attempt_id, seasonId, playerId, course.id, cc, p.status,
          p.started_at ?? null, p.ended_at ?? null, totalMs, p.total_time ?? null,
          p.character ?? null, p.kart ?? null, p.costume ?? null,
          p.coins_gained ?? null, p.coins_lost ?? null, p.mushrooms_used ?? null,
          p.source ?? null);
```

- [ ] **Step 5: Add `findGhostMatch` + `enrichRunFromGhost`**

Append to `pi/src/db/ingest.ts`:

```ts
/** The id of an existing finished run with identical total_time_ms for this
 *  (season, player, course, cc), or null. The ghost dedup key. */
export function findGhostMatch(db: DatabaseSync, seasonId: number, playerId: number,
                               courseId: number, cc: number, totalMs: number | null): number | null {
  if (totalMs === null) return null;
  const row = db.prepare(
    `SELECT id FROM runs WHERE season_id=? AND player_id=? AND course_id=? AND cc=?
     AND status='finished' AND total_time_ms=? ORDER BY id LIMIT 1`
  ).get(seasonId, playerId, courseId, cc, totalMs) as { id: number } | undefined;
  return row ? row.id : null;
}

/** Gap-fill an existing run from a ghost payload: fill null character/kart/costume,
 *  add run_laps + run_points ONLY if the run has none, set coin/mushroom totals only
 *  where null, and mark source='ghost'. Never overwrites existing data. */
export function enrichRunFromGhost(db: DatabaseSync, runId: number, p: AttemptPayload): { trailAdded: boolean } {
  db.exec('BEGIN');
  try {
    db.prepare(
      `UPDATE runs SET
         character      = COALESCE(character, ?),
         kart           = COALESCE(kart, ?),
         costume        = COALESCE(costume, ?),
         coins_gained   = COALESCE(coins_gained, ?),
         coins_lost     = COALESCE(coins_lost, ?),
         mushrooms_used = COALESCE(mushrooms_used, ?),
         source         = 'ghost'
       WHERE id=?`
    ).run(p.character ?? null, p.kart ?? null, p.costume ?? null,
          p.coins_gained ?? null, p.coins_lost ?? null, p.mushrooms_used ?? null, runId);

    const hasLaps = (db.prepare('SELECT COUNT(*) c FROM run_laps WHERE run_id=?').get(runId) as { c: number }).c > 0;
    if (!hasLaps) {
      const lapStmt = db.prepare(
        'INSERT INTO run_laps(run_id, lap_index, lap_time_ms, lap_time_str, coins, shrooms) VALUES (?,?,?,?,?,?)'
      );
      for (const lap of p.laps ?? []) lapStmt.run(runId, lap.lap, lap.time_ms, lap.time_str ?? null, lap.coins ?? null, lap.shrooms ?? null);
    }

    const hasPts = (db.prepare('SELECT COUNT(*) c FROM run_points WHERE run_id=?').get(runId) as { c: number }).c > 0;
    const pts = p.points ?? [];
    const maxT = pts.reduce((m, pt) => Math.max(m, pt[0]), 0);
    let trailAdded = false;
    if (!hasPts && pts.length > 0 && maxT <= OVER_LIMIT_MS) {
      const ptStmt = db.prepare('INSERT INTO run_points(run_id, t_ms, cx, cy, score, lap) VALUES (?,?,?,?,?,?)');
      for (const [t, cx, cy, sc, lap] of pts) ptStmt.run(runId, t, cx, cy, sc, lap ?? null);
      trailAdded = true;
    }
    db.exec('COMMIT');
    return { trailAdded };
  } catch (e) {
    db.exec('ROLLBACK');
    throw e;
  }
}
```

- [ ] **Step 6: Run to verify they pass**

Run: `cd pi && npx vitest run src/db/ingest.test.ts`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add pi/src/db/types.ts pi/src/db/ingest.ts pi/src/db/ingest.test.ts
git commit -m "feat(ghost): findGhostMatch + enrichRunFromGhost + upsertRun source"
```

---

### Task 12: Audit helper

**Files:**
- Create: `pi/src/db/ghostImport.ts`
- Test: `pi/src/db/ghostImport.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// pi/src/db/ghostImport.test.ts
import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { recordGhostImport } from './ghostImport';

function db() {
  const d = openDb(':memory:'); applySchema(d);
  d.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
  d.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
  d.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'choco_mountain','Choco Mountain')");
  return d;
}

describe('recordGhostImport', () => {
  it('writes an audit row', () => {
    const d = db();
    recordGhostImport(d, { runId: 5, playerId: 1, courseId: 1, cc: 150, totalMs: 100000, action: 'enriched' });
    const row = d.prepare('SELECT run_id, action, total_time_ms FROM ghost_imports').get() as any;
    expect(row).toEqual({ run_id: 5, action: 'enriched', total_time_ms: 100000 });
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd pi && npx vitest run src/db/ghostImport.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```ts
// pi/src/db/ghostImport.ts
import type { DatabaseSync } from 'node:sqlite';

export interface GhostAudit {
  runId: number | null; playerId: number; courseId: number; cc: number;
  totalMs: number | null; action: 'enriched' | 'new';
}

/** Append a durable audit row recording that a run was submitted via ghost import. */
export function recordGhostImport(db: DatabaseSync, a: GhostAudit): void {
  db.prepare(
    'INSERT INTO ghost_imports(run_id, player_id, course_id, cc, total_time_ms, action) VALUES (?,?,?,?,?,?)'
  ).run(a.runId, a.playerId, a.courseId, a.cc, a.totalMs, a.action);
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd pi && npx vitest run src/db/ghostImport.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pi/src/db/ghostImport.ts pi/src/db/ghostImport.test.ts
git commit -m "feat(ghost): ghost_imports audit helper"
```

---

### Task 13: Ghost branch in `POST /v1/runs`

**Files:**
- Modify: `pi/src/api/runs.ts`
- Test: `pi/src/api/runs.test.ts` (append)

- [ ] **Step 1: Write the failing tests**

```ts
// pi/src/api/runs.test.ts — append (uses the file's existing ctx()/post() helpers + events[])
describe('ghost import', () => {
  it('enriches a carryover match and does NOT announce a PB', async () => {
    const { app, db, token, events } = ctx();
    db.exec("INSERT INTO runs(id,attempt_id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,total_time_str,is_pb) " +
            "VALUES (70,'cv',1,1,1,150,'finished','carryover',100000,'1:40.000',1)");
    const before = events.length;
    const res = await post(app, token, { attempt_id: 'g1', course: 'Rainbow Road', status: 'finished',
      total_time: '1:40.000', source: 'ghost', total_laps: 1,
      laps: [{ lap: 1, time_ms: 100000, time_str: '1:40.000', coins: 3, shrooms: 1 }],
      points: [[0, 1, 2, 1, 1]] });
    const body = await res.json();
    expect(body.deduped).toBe(true);
    expect(events.slice(before).some(e => e.type === 'pb_achieved')).toBe(false);
    expect((db.prepare("SELECT character FROM runs WHERE id=70").get() as any).character).toBeTruthy();
    expect((db.prepare("SELECT COUNT(*) c FROM runs WHERE provenance='live'").get() as any).c).toBe(0);  // no new row
    expect((db.prepare("SELECT action FROM ghost_imports").get() as any).action).toBe('enriched');
  });

  it('inserts + announces when no match exists', async () => {
    const { app, db, token, events } = ctx();
    const before = events.length;
    const res = await post(app, token, { attempt_id: 'g2', course: 'Rainbow Road', status: 'finished',
      total_time: '1:30.000', source: 'ghost', character: 'Mario', kart: 'K', total_laps: 1, laps: [], points: [] });
    const body = await res.json();
    expect(body.is_pb).toBe(true);
    expect(events.slice(before).some(e => e.type === 'pb_achieved')).toBe(true);
    expect((db.prepare("SELECT source FROM runs WHERE attempt_id='g2'").get() as any).source).toBe('ghost');
    expect((db.prepare("SELECT action FROM ghost_imports").get() as any).action).toBe('new');
  });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd pi && npx vitest run src/api/runs.test.ts`
Expected: FAIL — enrich path not implemented (`deduped` undefined; a new live row is inserted).

- [ ] **Step 3: Implement the ghost branch**

In `pi/src/api/runs.ts`, add imports:

```ts
import { upsertRun, findGhostMatch, enrichRunFromGhost, timeToMs } from '../db/ingest';
import { recordGhostImport } from '../db/ghostImport';
```

Inside the `r.post('/v1/runs', ...)` handler, AFTER the `courseId === null` guard and
BEFORE `const prevLeader = ...`, insert the ghost branch:

```ts
    // Ghost import: dedup by player+course+exact total time. A match (e.g. a Season-0
    // carryover) is enriched in place and NOT announced; no match becomes a new run.
    if (p.source === 'ghost' && p.status === 'finished') {
      const totalMs = timeToMs(p.total_time);
      const matchId = findGhostMatch(db, seasonId, playerId, courseId, cc, totalMs);
      if (matchId !== null) {
        const { trailAdded } = enrichRunFromGhost(db, matchId, p);
        recordGhostImport(db, { runId: matchId, playerId, courseId, cc, totalMs, action: 'enriched' });
        if (trailAdded) {
          try {
            const built = rebuildCourseModel(db, courseId, cc);
            if (built) invalidateModel?.(courseId);
          } catch (e) { console.error('[course-model] ghost-enrich rebuild failed:', e); }
        }
        console.log(`[ghost-import] enriched run ${matchId} (${playerName}, ${slugify(p.course)}, ${p.total_time})`);
        return c.json({ deduped: true, is_pb: false, rank: null, gap_to_leader_ms: null, gap_to_wr_ms: null });
      }
    }
```

Then, at the END of the handler (just before `return c.json(result);`), record the
"new" ghost audit when it was a ghost insert:

```ts
    if (p.source === 'ghost') {
      const newRun = db.prepare("SELECT id FROM runs WHERE attempt_id=?").get(p.attempt_id) as { id: number } | undefined;
      recordGhostImport(db, { runId: newRun ? newRun.id : null, playerId, courseId, cc,
        totalMs: timeToMs(p.total_time), action: 'new' });
      console.log(`[ghost-import] new run (${playerName}, ${slugify(p.course)}, ${p.total_time})`);
    }
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd pi && npx vitest run src/api/runs.test.ts`
Expected: PASS.

- [ ] **Step 5: Full server suite**

Run: `cd pi && npx vitest run`
Expected: all pass (no regressions).

- [ ] **Step 6: Commit**

```bash
git add pi/src/api/runs.ts pi/src/api/runs.test.ts
git commit -m "feat(ghost): /v1/runs ghost branch (enrich-no-announce / insert + audit)"
```

---

## Final verification

- [ ] **Engine:** `python -m pytest tests/ -q` (all green) + `python scripts/ghost_import_clip_check.py` → `PASS`.
- [ ] **Frontend:** `npm run check` (0/0) + `npx vitest run` (green) + `npm run build`.
- [ ] **Server:** `cd pi && npx vitest run` (green).
- [ ] **Manual end-to-end:** arm via the button → watch a real ghost start-to-finish → review popup pops with the GHOST marker, course pre-filled, char/kart required → two-step submit → server enriches/announces correctly + a `ghost_imports` row exists → button auto-disarms.

---

## Self-review notes (spec coverage)

- Effective-screen capture + quiet emits → Task 4. ✓
- GhostImport (arm/start/validate restart-vs-resume/abort/finish/disarm) → Tasks 1, 3. ✓
- Finalize `source:ghost` + null identity + keep course + skip calibration → Task 3. ✓
- Minimap course-only seed: handled by nulling identity at finalize + skipping calibration; `_start_race` still seeds from the detected course. (No character is passed at finalize; the live seed already falls back to the default confident score when no stored threshold exists.) ✓
- IPC `set_ghost_import` + `ghost_import_state` → Tasks 2, 4. ✓
- Rust: no changes (source + event ride existing rails) — verified in `lib.rs`/`sync.rs`. ✓
- Review popup two-step submit + GHOST marker; course pre-filled, char/kart required (existing modal logic) → Task 7. ✓
- Title-bar button + armed visual + warning modal → Tasks 8, 9. ✓
- "Watching a ghost" label → Task 6. ✓
- Server dedup-or-insert, announce-or-not, `runs.source`, `ghost_imports` audit → Tasks 10-13. ✓
- Clip validation (`temp/ghostsample.mp4`) → Task 5. ✓

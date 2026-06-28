# Asset Sweep Control Shell (SP1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A single Tkinter window that supervises the 3-process recording sweep (agent + tracker + sweep), with start / begin / pause / resume / stop, a live thumbnail, a health strip, and three split log panes — at negligible resource cost.

**Architecture:** A supervisor process spawns the three children with piped stdout (no console windows) and subscribes read-only to the tracker's `:8766` WebSocket for live state + a new low-rate `preview` thumbnail. Pure-logic units (state machine, progress, health) are split from I/O units (process supervisor, WS consumer, manual controller) so the logic is unit-tested with fakes; the Tk layer is thin wiring.

**Tech Stack:** Python ≥3.10, stdlib `tkinter` (PhotoImage — no Pillow), `subprocess`, `websockets` (dev-only, already used), `cv2`/`numpy` (already deps).

## Global Constraints

- New console modules live in `tools/sweep_console/` as **bare modules** imported via `tests/conftest.py` sys.path (matches the `tools/autotemplate` convention — no package `__init__`).
- WS clients MUST connect to **`127.0.0.1`**, never `localhost` (broadcaster binds IPv4 `0.0.0.0`; `::1` is refused → WinError 1225).
- The tracker is launched as: `python -m mkw_tracker --clip-capture --ws-port 8766 --no-display`.
- The UI MUST NOT open the capture device. The thumbnail comes only from the WS `preview` event (the tracker's already-decoded frame).
- No new entries in `requirements.txt` (Tkinter is stdlib; `websockets` is dev-only).
- Clips directory: `captures_sdr/en_uk/clips/`. Grid total = **6,273** clips.
- Run tests from the repo root with `python -m pytest`.
- Commit after every task.

---

## File Structure

**New (`tools/sweep_console/`):**
- `controlstate.py` — `ControlState` lifecycle machine (pure)
- `progress.py` — `ProgressModel` done/ETA (pure)
- `health.py` — `HealthModel` WS-field aggregator (pure)
- `commands.py` — child-process argv builders (pure)
- `manual.py` — `ManualController` (thin wrapper over `ControllerBridge`)
- `supervisor.py` — `ProcessSupervisor` (Popen + stdout pumps + teardown)
- `wsconsumer.py` — `WsConsumer` (read-only `:8766` subscriber)
- `app.py` — `ConsoleApp` Tk window + entry point

**New (`mkw_tracker/tools/`):** `preview.py` — preview-thumbnail encoder (pure)

**New (repo root):** `run_console.bat`

**Modified:** `tests/conftest.py` (sys.path), `mkw_tracker/main.py` (preview broadcast), `tools/autotemplate/sweep_runner.py` (stop-file + retire pilot), `tools/autotemplate/start_agent.py` (in-WSL teardown).

**New tests:** `tests/test_console_controlstate.py`, `test_console_progress.py`, `test_console_health.py`, `test_console_commands.py`, `test_console_manual.py`, `test_console_preview.py`, plus additions to `tests/test_sweep_runner.py` and `tests/test_start_agent.py`.

---

### Task 1: conftest sys.path + ControlState

**Files:**
- Modify: `tests/conftest.py:8-12`
- Create: `tools/sweep_console/controlstate.py`
- Test: `tests/test_console_controlstate.py`

**Interfaces:**
- Produces: `ControlState` with attribute `state: str` and method `on_event(event: str) -> list[str]` (returns action names). State constants `IDLE, RIG_WARM, SWEEPING, PAUSE_REQUESTED, PAUSED, STOP_REQUESTED`. Event constants `START_RIG, BEGIN_SWEEP, PAUSE, RESUME, STOP, SWEEP_EXITED`. Action strings: `start_agent, start_tracker, connect_ws, connect_manual, enable_manual, disable_manual, start_sweep, request_sweep_stop, stop_rig, disconnect`.

- [ ] **Step 1: Add the console dir to the test path**

In `tests/conftest.py`, change the dirs tuple (line 9):

```python
for _d in ("tools/autotemplate", "tools/asset_matte", "tools/sweep_console"):
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_console_controlstate.py`:

```python
import controlstate as cs


def test_start_rig_from_idle():
    m = cs.ControlState()
    acts = m.on_event(cs.START_RIG)
    assert m.state == cs.RIG_WARM
    assert acts == ["start_agent", "start_tracker", "connect_ws", "connect_manual", "enable_manual"]


def test_begin_sweep_disables_manual_and_starts():
    m = cs.ControlState(); m.on_event(cs.START_RIG)
    acts = m.on_event(cs.BEGIN_SWEEP)
    assert m.state == cs.SWEEPING
    assert acts == ["disable_manual", "start_sweep"]


def test_pause_requests_stop_then_paused_on_exit():
    m = cs.ControlState(); m.on_event(cs.START_RIG); m.on_event(cs.BEGIN_SWEEP)
    assert m.on_event(cs.PAUSE) == ["request_sweep_stop"]
    assert m.state == cs.PAUSE_REQUESTED
    assert m.on_event(cs.SWEEP_EXITED) == ["enable_manual"]
    assert m.state == cs.PAUSED


def test_resume_from_paused():
    m = cs.ControlState(); m.on_event(cs.START_RIG); m.on_event(cs.BEGIN_SWEEP)
    m.on_event(cs.PAUSE); m.on_event(cs.SWEEP_EXITED)
    assert m.on_event(cs.RESUME) == ["disable_manual", "start_sweep"]
    assert m.state == cs.SWEEPING


def test_stop_while_sweeping_waits_for_exit_then_tears_down():
    m = cs.ControlState(); m.on_event(cs.START_RIG); m.on_event(cs.BEGIN_SWEEP)
    assert m.on_event(cs.STOP) == ["request_sweep_stop"]
    assert m.state == cs.STOP_REQUESTED
    assert m.on_event(cs.SWEEP_EXITED) == ["stop_rig", "disconnect"]
    assert m.state == cs.IDLE


def test_stop_from_rig_warm_is_immediate():
    m = cs.ControlState(); m.on_event(cs.START_RIG)
    assert m.on_event(cs.STOP) == ["stop_rig", "disconnect"]
    assert m.state == cs.IDLE


def test_sweep_exits_on_its_own_lands_paused():
    m = cs.ControlState(); m.on_event(cs.START_RIG); m.on_event(cs.BEGIN_SWEEP)
    assert m.on_event(cs.SWEEP_EXITED) == ["enable_manual"]
    assert m.state == cs.PAUSED


def test_invalid_transition_is_noop():
    m = cs.ControlState()
    assert m.on_event(cs.PAUSE) == []
    assert m.state == cs.IDLE
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_console_controlstate.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'controlstate'`).

- [ ] **Step 4: Implement `controlstate.py`**

Create `tools/sweep_console/controlstate.py`:

```python
"""Pure lifecycle state machine for the sweep console.

No I/O: on_event() returns a list of action names that the supervisor maps to
real calls, so the whole control flow is unit-testable without processes.
"""

IDLE = "IDLE"
RIG_WARM = "RIG_WARM"
SWEEPING = "SWEEPING"
PAUSE_REQUESTED = "PAUSE_REQUESTED"
PAUSED = "PAUSED"
STOP_REQUESTED = "STOP_REQUESTED"

START_RIG = "START_RIG"
BEGIN_SWEEP = "BEGIN_SWEEP"
PAUSE = "PAUSE"
RESUME = "RESUME"
STOP = "STOP"
SWEEP_EXITED = "SWEEP_EXITED"


class ControlState:
    def __init__(self):
        self.state = IDLE

    def on_event(self, event):
        s = self.state
        if event == START_RIG and s == IDLE:
            self.state = RIG_WARM
            return ["start_agent", "start_tracker", "connect_ws", "connect_manual", "enable_manual"]
        if event == BEGIN_SWEEP and s == RIG_WARM:
            self.state = SWEEPING
            return ["disable_manual", "start_sweep"]
        if event == RESUME and s == PAUSED:
            self.state = SWEEPING
            return ["disable_manual", "start_sweep"]
        if event == PAUSE and s == SWEEPING:
            self.state = PAUSE_REQUESTED
            return ["request_sweep_stop"]
        if event == STOP and s in (RIG_WARM, PAUSED):
            self.state = IDLE
            return ["stop_rig", "disconnect"]
        if event == STOP and s in (SWEEPING, PAUSE_REQUESTED):
            self.state = STOP_REQUESTED
            return ["request_sweep_stop"]
        if event == SWEEP_EXITED and s == STOP_REQUESTED:
            self.state = IDLE
            return ["stop_rig", "disconnect"]
        if event == SWEEP_EXITED and s in (PAUSE_REQUESTED, SWEEPING):
            self.state = PAUSED
            return ["enable_manual"]
        return []
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_console_controlstate.py -q`
Expected: PASS (8 passed).

- [ ] **Step 6: Commit**

```bash
git add tests/conftest.py tools/sweep_console/controlstate.py tests/test_console_controlstate.py
git commit -m "feat(console): pure control-state machine + test sys.path"
```

---

### Task 2: ProgressModel

**Files:**
- Create: `tools/sweep_console/progress.py`
- Test: `tests/test_console_progress.py`

**Interfaces:**
- Produces: `ProgressModel(total: int, window: int = 20)` with `update(done: int, now: float)`, `snapshot(now=None) -> dict` returning keys `done, total, pct, rate_per_sec, eta_seconds` (`eta_seconds` is `None` when rate is 0).

- [ ] **Step 1: Write the failing test**

Create `tests/test_console_progress.py`:

```python
from progress import ProgressModel


def test_empty_is_zero_no_eta():
    snap = ProgressModel(6273).snapshot()
    assert snap["done"] == 0 and snap["total"] == 6273
    assert snap["pct"] == 0.0 and snap["eta_seconds"] is None


def test_rate_and_eta_from_two_samples():
    p = ProgressModel(100)
    p.update(0, 0.0)
    p.update(10, 10.0)          # 10 clips in 10s -> 1/s, 90 remaining -> 90s
    snap = p.snapshot()
    assert abs(snap["rate_per_sec"] - 1.0) < 1e-9
    assert abs(snap["eta_seconds"] - 90.0) < 1e-9
    assert abs(snap["pct"] - 0.10) < 1e-9


def test_no_progress_means_no_eta():
    p = ProgressModel(100)
    p.update(5, 0.0)
    p.update(5, 10.0)           # unchanged -> rate 0
    assert p.snapshot()["eta_seconds"] is None


def test_complete():
    p = ProgressModel(10)
    p.update(0, 0.0); p.update(10, 5.0)
    snap = p.snapshot()
    assert snap["done"] == 10 and snap["pct"] == 1.0 and snap["eta_seconds"] == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_console_progress.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'progress'`).

- [ ] **Step 3: Implement `progress.py`**

Create `tools/sweep_console/progress.py`:

```python
"""Pure progress + ETA from clip counts sampled over time."""


class ProgressModel:
    def __init__(self, total, window=20):
        self.total = total
        self._window = window
        self._samples = []   # list of (time, done)

    def update(self, done, now):
        if not self._samples or self._samples[-1][1] != done:
            self._samples.append((now, done))
            if len(self._samples) > self._window:
                self._samples.pop(0)

    def _rate(self):
        if len(self._samples) < 2:
            return 0.0
        (t0, d0), (t1, d1) = self._samples[0], self._samples[-1]
        dt = t1 - t0
        return (d1 - d0) / dt if dt > 0 and d1 > d0 else 0.0

    def snapshot(self, now=None):
        done = self._samples[-1][1] if self._samples else 0
        rate = self._rate()
        remaining = max(0, self.total - done)
        eta = (remaining / rate) if rate > 0 else None
        pct = (done / self.total) if self.total else 0.0
        return {"done": done, "total": self.total, "pct": pct,
                "rate_per_sec": rate, "eta_seconds": eta}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_console_progress.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add tools/sweep_console/progress.py tests/test_console_progress.py
git commit -m "feat(console): ProgressModel (done/rate/ETA)"
```

---

### Task 3: HealthModel

**Files:**
- Create: `tools/sweep_console/health.py`
- Test: `tests/test_console_health.py`

**Interfaces:**
- Produces: `HealthModel()` with `apply(msg: dict, now: float = 0.0)`, `set_controller(connected: bool, mac: str = "")`, `snapshot(now: float) -> dict` (keys `screen, character, costume, kart, fps, controller, mac, last_clip_age`). Consumes WS message dicts with `type` in `heartbeat` (`fps`,`screen`), `screen_change` (`to`), `selection_update` (`character`,`costume`,`kart`), `clip_done`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_console_health.py`:

```python
from health import HealthModel


def test_heartbeat_sets_fps_and_screen():
    h = HealthModel()
    h.apply({"type": "heartbeat", "fps": 60.0, "screen": "KART_SELECT", "tracking": True})
    snap = h.snapshot(now=5.0)
    assert snap["fps"] == 60.0 and snap["screen"] == "KART_SELECT"


def test_selection_update_sets_names():
    h = HealthModel()
    h.apply({"type": "selection_update", "character": "Mario", "costume": "Touring", "kart": "Pipe Frame"})
    snap = h.snapshot(now=0.0)
    assert (snap["character"], snap["costume"], snap["kart"]) == ("Mario", "Touring", "Pipe Frame")


def test_screen_change_overrides_screen():
    h = HealthModel()
    h.apply({"type": "screen_change", "from": "KART_SELECT", "to": "COURSE_SELECT"})
    assert h.snapshot(now=0.0)["screen"] == "COURSE_SELECT"


def test_clip_done_drives_last_clip_age():
    h = HealthModel()
    h.apply({"type": "clip_done", "item": "mario__base"}, now=100.0)
    assert h.snapshot(now=107.0)["last_clip_age"] == 7.0


def test_no_clip_yet_age_none():
    assert HealthModel().snapshot(now=10.0)["last_clip_age"] is None


def test_set_controller():
    h = HealthModel(); h.set_controller(True, "E0:EF:BF:03:74:19")
    snap = h.snapshot(now=0.0)
    assert snap["controller"] is True and snap["mac"].startswith("E0:")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_console_health.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'health'`).

- [ ] **Step 3: Implement `health.py`**

Create `tools/sweep_console/health.py`:

```python
"""Pure aggregator: WS broadcasts + supervisor signals -> health-strip fields."""


class HealthModel:
    def __init__(self):
        self.screen = None
        self.character = None
        self.costume = None
        self.kart = None
        self.fps = None
        self.controller = False
        self.mac = ""
        self.last_clip_t = None

    def apply(self, msg, now=0.0):
        t = msg.get("type")
        if t == "heartbeat":
            self.fps = msg.get("fps")
            self.screen = msg.get("screen") or self.screen
        elif t == "screen_change":
            self.screen = msg.get("to") or self.screen
        elif t == "selection_update":
            self.character = msg.get("character")
            self.costume = msg.get("costume")
            self.kart = msg.get("kart")
        elif t == "clip_done":
            self.last_clip_t = now

    def set_controller(self, connected, mac=""):
        self.controller = bool(connected)
        self.mac = mac or ""

    def snapshot(self, now):
        age = (now - self.last_clip_t) if self.last_clip_t is not None else None
        return {"screen": self.screen, "character": self.character,
                "costume": self.costume, "kart": self.kart, "fps": self.fps,
                "controller": self.controller, "mac": self.mac,
                "last_clip_age": age}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_console_health.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add tools/sweep_console/health.py tests/test_console_health.py
git commit -m "feat(console): HealthModel aggregator"
```

---

### Task 4: preview.py encoder (tracker side)

**Files:**
- Create: `mkw_tracker/tools/preview.py`
- Test: `tests/test_console_preview.py`

**Interfaces:**
- Produces: `encode_preview_b64(frame, width=320) -> str` (base64 PNG, "" on failure); `maybe_preview(frame, now, last_emit, interval=0.5, width=320) -> (dict|None, float)` where the dict is `{"type":"preview","w":int,"h":int,"data":str}`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_console_preview.py`:

```python
import base64
import numpy as np
from mkw_tracker.tools.preview import encode_preview_b64, maybe_preview


def _frame():
    return np.zeros((1080, 1920, 3), dtype=np.uint8)


def test_encode_downscales_to_width():
    import cv2
    b64 = encode_preview_b64(_frame(), width=320)
    img = cv2.imdecode(np.frombuffer(base64.b64decode(b64), np.uint8), cv2.IMREAD_COLOR)
    assert img.shape[1] == 320 and img.shape[0] == 180   # 16:9 preserved


def test_maybe_preview_throttles():
    msg, last = maybe_preview(_frame(), now=0.0, last_emit=0.0, interval=0.5)
    assert msg is None and last == 0.0                   # too soon (0 since last)


def test_maybe_preview_emits_after_interval():
    msg, last = maybe_preview(_frame(), now=1.0, last_emit=0.0, interval=0.5)
    assert msg["type"] == "preview" and msg["w"] == 320 and msg["h"] == 180
    assert isinstance(msg["data"], str) and last == 1.0


def test_maybe_preview_none_frame():
    assert maybe_preview(None, now=5.0, last_emit=0.0)[0] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_console_preview.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'mkw_tracker.tools.preview'`).

- [ ] **Step 3: Implement `preview.py`**

Create `mkw_tracker/tools/preview.py`:

```python
"""Low-cost preview-thumbnail encoder for the clip-capture WS broadcast.

Pure helpers (cv2 + base64) so the throttle/encoding logic is unit-tested away
from the main loop. No ffmpeg, no device access.
"""
import base64

import cv2


def _dims(frame, width):
    h, w = frame.shape[:2]
    nw = int(width)
    nh = max(1, int(round(h * (width / w))))
    return nw, nh


def encode_preview_b64(frame, width=320):
    nw, nh = _dims(frame, width)
    small = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".png", small)
    if not ok:
        return ""
    return base64.b64encode(buf.tobytes()).decode("ascii")


def maybe_preview(frame, now, last_emit, interval=0.5, width=320):
    """Throttle gate. Returns (message|None, new_last_emit)."""
    if frame is None or (now - last_emit) < interval:
        return None, last_emit
    b64 = encode_preview_b64(frame, width)
    if not b64:
        return None, last_emit
    nw, nh = _dims(frame, width)
    return {"type": "preview", "w": nw, "h": nh, "data": b64}, now
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_console_preview.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add mkw_tracker/tools/preview.py tests/test_console_preview.py
git commit -m "feat(clip): preview-thumbnail encoder helper"
```

---

### Task 5: Wire the preview broadcast into the tracker loop

**Files:**
- Modify: `mkw_tracker/main.py` (clip-capture import block ~951; loop-state init ~992; clip_mgr branch ~1106-1110)

**Interfaces:**
- Consumes: `mkw_tracker.tools.preview.maybe_preview` (Task 4); the existing `broadcaster.broadcast(line)` and `_clip_json` (json) in `main.py`.
- Produces: a `{"type":"preview",...}` line on `:8766` at ~2 Hz while clip-capture runs (consumed by Task 10).

- [ ] **Step 1: Import the helper in the clip-capture setup block**

In `mkw_tracker/main.py`, find:

```python
        import json as _clip_json
        from .tools.clip_capture import ClipCaptureManager
```

Add a line after it:

```python
        import json as _clip_json
        from .tools.clip_capture import ClipCaptureManager
        from .tools import preview as _preview
```

- [ ] **Step 2: Initialise the throttle timestamp with the other clip loop-state**

Find:

```python
    _clip_pipe_seq_t = time.monotonic()   # wall-clock when that counter last advanced
```

Add after it:

```python
    _last_preview_emit = 0.0               # throttle for the WS preview thumbnail
```

- [ ] **Step 3: Emit the throttled preview in the clip_mgr branch**

Find:

```python
            _clipped = current_frame[0]
            if _clipped is not None:
                frame = _norm(_clipped)
                current_frame[0] = frame
                lifecycle.current_frame = frame
                t_frame = time.perf_counter()
                frame_times.append(t_frame)
```

Replace with (adds the broadcast after the frame is ready):

```python
            _clipped = current_frame[0]
            if _clipped is not None:
                frame = _norm(_clipped)
                current_frame[0] = frame
                lifecycle.current_frame = frame
                if broadcaster is not None:
                    _pmsg, _last_preview_emit = _preview.maybe_preview(
                        frame, time.monotonic(), _last_preview_emit, interval=0.5)
                    if _pmsg is not None:
                        broadcaster.broadcast(_clip_json.dumps(_pmsg))
                t_frame = time.perf_counter()
                frame_times.append(t_frame)
```

- [ ] **Step 4: Verify nothing else broke (import + unit suite)**

Run: `python -c "import ast; ast.parse(open('mkw_tracker/main.py').read())"`
Expected: no output (parses clean).
Run: `python -m pytest tests/test_console_preview.py -q`
Expected: PASS.

- [ ] **Step 5: Manual verification note (bring-up only)**

This is loop glue and can only be exercised with the capture card. During bring-up, run the tracker (`python -m mkw_tracker --clip-capture --ws-port 8766 --no-display`) and a one-off WS client to `ws://127.0.0.1:8766`; confirm `preview` messages arrive ~2/s with non-empty `data`. (Done as part of Task 11's smoke test.)

- [ ] **Step 6: Commit**

```bash
git add mkw_tracker/main.py
git commit -m "feat(clip): broadcast ~2Hz preview thumbnail on the clip-capture WS"
```

---

### Task 6: sweep_runner — cooperative stop-file + retire the msvcrt pilot

**Files:**
- Modify: `tools/autotemplate/sweep_runner.py` (`SweepRunner.__init__`, `sweep_karts`, `main`; delete `_pilot`)
- Test: `tests/test_sweep_runner.py` (additions)

**Interfaces:**
- Consumes: nothing new.
- Produces: `SweepRunner(..., stop_check: callable | None = None)`; `sweep_karts(combo_slug) -> bool` (True = paused mid-row after returning to CHARACTER_SELECT, False = completed). New CLI flag `--stop-file PATH`. `--pilot` removed.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_sweep_runner.py`:

```python
class FakeScreenClient(FakeClient):
    """FakeClient whose at_current_screen reports a fixed screen (so _return_to lands)."""
    def __init__(self, screen="CHARACTER_SELECT", **kw):
        super().__init__(**kw)
        self._screen_name = screen
    def send(self, msg):
        if msg.get("type") == "at_current_screen":
            return {"type": "current_screen", "screen": self._screen_name}
        return super().send(msg)


def test_sweep_karts_pauses_at_next_kart_and_returns_to_anchor():
    g = grid.load_grid(YAML)
    ctrl, client = FakeController(), FakeScreenClient("CHARACTER_SELECT")
    calls = [0]
    def stop_check():
        calls[0] += 1
        return calls[0] > 1                       # allow one kart, pause before the second
    r = SweepRunner(g, ctrl, client, idle_seconds=0.0, ground_timeout=0.0, stop_check=stop_check)
    captured = []
    r.capture_kart = lambda combo, kart: captured.append(kart)   # stub hardware
    assert r.sweep_karts("mario__base") is True   # paused
    assert len(captured) == 1
    assert ("press", "B") in ctrl.log             # returned to CHARACTER_SELECT anchor


def test_sweep_karts_completes_when_not_paused():
    g = grid.load_grid(YAML)
    ctrl, client = FakeController(), FakeScreenClient("CHARACTER_SELECT")
    r = SweepRunner(g, ctrl, client, idle_seconds=0.0, ground_timeout=0.0, stop_check=lambda: False)
    captured = []
    r.capture_kart = lambda combo, kart: captured.append(kart)
    assert r.sweep_karts("mario__base") is False  # completed
    assert len(captured) == len(list(g.cells("karts")))
    assert ("press", "B") in ctrl.log             # anchor return at end of row
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_sweep_runner.py -k sweep_karts -q`
Expected: FAIL (`sweep_karts` returns a list / `stop_check` is not an accepted kwarg).

- [ ] **Step 3: Add `stop_check` to `SweepRunner.__init__`**

In `tools/autotemplate/sweep_runner.py`, change the signature + body:

```python
    def __init__(self, grid, controller, client, *, idle_seconds=10.0, settle_seconds=0.8,
                 ground_timeout=4.0, ground_stable_reads=3, lang="en_uk", stop_check=None):
        self.grid = grid
        self.ctrl = controller
        self.client = client
        self.idle = idle_seconds
        self.settle = settle_seconds
        self.ground_timeout = ground_timeout
        self.ground_stable_reads = ground_stable_reads
        self.lang = lang
        self.stop_check = stop_check
```

- [ ] **Step 4: Add a stop predicate and rewrite `sweep_karts`**

Add the helper just above `sweep_karts`:

```python
    def _stop_requested(self) -> bool:
        return bool(self.stop_check and self.stop_check())
```

Replace the whole `sweep_karts` method with:

```python
    def sweep_karts(self, combo_slug):
        # Anti-spin runs all the time (the agent holds it on by default).  Returns True if a
        # pause was requested mid-row (after returning to CHARACTER_SELECT), else False.
        karts = [c.slug for c in self.grid.cells("karts")]
        for kart in karts:
            if self._stop_requested():
                self._return_to("CHARACTER_SELECT", "pause mid kart row")
                return True
            self.capture_kart(combo_slug, kart)
        self._return_to("CHARACTER_SELECT", "after kart row")
        return False
```

- [ ] **Step 5: Run the new tests**

Run: `python -m pytest tests/test_sweep_runner.py -k sweep_karts -q`
Expected: PASS (2 passed).

- [ ] **Step 6: Replace `main()` — drop the pilot/preamble, add `--stop-file`**

Delete the entire `_pilot(ctrl)` function. Replace `main()` with:

```python
def main():
    import argparse
    import os
    from grid import load_grid

    p = argparse.ArgumentParser(
        description="Clip sweep: walk the character/kart grid, record one clip per item. "
                    "Runs on Windows; delegates the controller to the WSL2 controller_agent via TCP. "
                    "Drive to character-select first (the console's manual cluster), then start this.")
    p.add_argument("--capture-ws", default="ws://127.0.0.1:8766",
                   help="WebSocket URL of the clip-recorder broadcaster (use 127.0.0.1, not localhost).")
    p.add_argument("--agent-host", default="127.0.0.1", help="controller_agent host (default 127.0.0.1)")
    p.add_argument("--agent-port", type=int, default=7878, help="controller_agent port (default 7878)")
    p.add_argument("--start-from", default=None, metavar="SLUG",
                   help="Resume from this character slug (skip earlier cells).")
    p.add_argument("--stop-file", default=None, metavar="PATH",
                   help="Graceful-stop flag: when this file exists, finish the current clip, return "
                        "to CHARACTER_SELECT, and exit (the console creates it for Pause/Stop).")
    p.add_argument("--dry-run", action="store_true",
                   help="Print all steps without opening controller or capture WS.")
    a = p.parse_args()

    yaml_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "clip_sweep.yaml")
    g = load_grid(yaml_path)

    stop_check = (lambda: bool(a.stop_file and os.path.exists(a.stop_file)))

    print(f"Agent:       {a.agent_host}:{a.agent_port}")
    print(f"Capture WS:  {a.capture_ws}")
    print(f"Start from:  {a.start_from or '(beginning)'}")
    print(f"Stop file:   {a.stop_file or '(none)'}")
    print(f"Mode:        {'DRY RUN' if a.dry_run else 'LIVE'}\n")

    if a.dry_run:
        ctrl, client = _DryController(), _DryClient()
        runner = SweepRunner(g, ctrl, client, idle_seconds=0.0, settle_seconds=0.0, stop_check=stop_check)
    else:
        ctrl = BridgeController(host=a.agent_host, port=a.agent_port)
        client = WsClient(a.capture_ws)
        runner = SweepRunner(g, ctrl, client, stop_check=stop_check)

    try:
        if not a.dry_run:
            print("Waiting for the controller agent to connect to the Switch...", flush=True)
            if hasattr(ctrl, "wait_ready") and not ctrl.wait_ready():
                print("Controller never became ready — is start_agent.py running and the Switch on? Aborting.")
                return

        skipping = bool(a.start_from)
        paused = False
        for slug, presses in g.sweep_steps("characters"):
            if skipping:
                if slug == a.start_from:
                    skipping = False
                else:
                    print(f"  [skip] {slug}")
                    continue
            if runner._stop_requested():
                paused = True
                break                                  # at CHARACTER_SELECT (anchor) already
            print(f"\n-- char: {slug} --")
            for btn in presses:
                ctrl.press(btn)
            runner.verify_on(slug, "characters")
            runner.capture_char(slug)
            if runner.sweep_karts(slug):
                paused = True
                break
        print("\nPaused (stop-file present)." if paused else "\nSweep complete.")
    finally:
        ctrl.stop()
        client.close()
```

- [ ] **Step 7: Verify the file parses and the full sweep suite passes**

Run: `python -c "import ast; ast.parse(open('tools/autotemplate/sweep_runner.py').read())"`
Expected: no output.
Run: `python -m pytest tests/test_sweep_runner.py -q`
Expected: PASS (all, including the two new tests).

- [ ] **Step 8: Commit**

```bash
git add tools/autotemplate/sweep_runner.py tests/test_sweep_runner.py
git commit -m "feat(sweep): cooperative --stop-file pause; retire msvcrt pilot"
```

---

### Task 7: start_agent — clean in-WSL teardown

**Files:**
- Modify: `tools/autotemplate/start_agent.py` (add `pkill_cmd`, `stop_agent`, `--stop`)
- Test: `tests/test_start_agent.py`

**Interfaces:**
- Produces: `pkill_cmd(distro: str, port: int = 7878) -> list[str]` (pure); `stop_agent(distro=None, port=7878) -> int`; CLI `python start_agent.py --stop`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_start_agent.py`:

```python
from start_agent import pkill_cmd


def test_pkill_cmd_targets_distro_and_agent():
    cmd = pkill_cmd("Ubuntu", 7878)
    assert cmd[:3] == ["wsl", "-d", "Ubuntu"]
    joined = " ".join(cmd)
    assert "pkill" in joined and "controller_agent.py" in joined and "7878" in joined
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_start_agent.py -q`
Expected: FAIL (`cannot import name 'pkill_cmd'`).

- [ ] **Step 3: Add `pkill_cmd`, `stop_agent`, and the `--stop` flag**

In `tools/autotemplate/start_agent.py`, add these functions (after `run(args)`):

```python
def pkill_cmd(distro: str, port: int = 7878) -> list:
    """argv that kills the in-WSL controller_agent for `port` (run under sudo -S)."""
    return ["wsl", "-d", distro, "--", "sudo", "-S", "pkill", "-f",
            f"controller_agent.py --port {port}"]


def stop_agent(distro=None, port: int = 7878) -> int:
    """Best-effort: kill the in-WSL controller_agent so a later start_agent can reconnect
    cleanly (the agent has no in-band shutdown command). Returns the subprocess rc (0 = ok)."""
    distro = distro or nxauto_cfg("wsl_distro") or detect_distro()
    if not distro:
        print("[stop_agent] no WSL distro found; nothing to stop.", file=sys.stderr)
        return 2
    pw = sudo_password()
    proc = subprocess.run(pkill_cmd(distro, port), input=pw + "\n",
                          capture_output=True, text=True)
    print(f"[stop_agent] pkill controller_agent on {distro}: rc={proc.returncode}")
    return proc.returncode
```

In `main()`, add the `--stop` flag and short-circuit. Find:

```python
    p.add_argument("--venv-python", default=NXAUTO_VENV_PY, dest="venv_python",
                   help="WSL python with nxbt (default: nxauto's venv).")
    sys.exit(run(p.parse_args()))
```

Replace with:

```python
    p.add_argument("--venv-python", default=NXAUTO_VENV_PY, dest="venv_python",
                   help="WSL python with nxbt (default: nxauto's venv).")
    p.add_argument("--stop", action="store_true",
                   help="Kill the in-WSL controller_agent and exit (clean teardown).")
    args = p.parse_args()
    if args.stop:
        sys.exit(stop_agent(distro=args.distro, port=args.port))
    sys.exit(run(args))
```

NOTE: `--port` and `--distro` already exist in `main()`'s parser (lines ~189–192), so `--stop` reuses `args.port` / `args.distro`. Do NOT re-add them.

- [ ] **Step 4: Run the test**

Run: `python -m pytest tests/test_start_agent.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add tools/autotemplate/start_agent.py tests/test_start_agent.py
git commit -m "feat(agent): --stop / stop_agent for clean in-WSL teardown"
```

---

### Task 8: ManualController

**Files:**
- Create: `tools/sweep_console/manual.py`
- Test: `tests/test_console_manual.py`

**Interfaces:**
- Produces: `to_button(label: str) -> str` (pure: maps `up/down/left/right/a/b/plus/home` → agent button names); `ManualController(bridge)` with `press(label) -> bool`, `status() -> dict`, `close()`. `bridge` is any object exposing `press(button)`, `get_status() -> dict`, `close()` (the real one is `controller_bridge.ControllerBridge`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_console_manual.py`:

```python
import pytest
from manual import ManualController, to_button


class FakeBridge:
    def __init__(self): self.pressed = []; self.closed = False
    def press(self, button, **kw): self.pressed.append(button); return True
    def get_status(self): return {"connected": True, "mac": "AA:BB"}
    def close(self): self.closed = True


@pytest.mark.parametrize("label,btn", [
    ("up", "DPAD_UP"), ("down", "DPAD_DOWN"), ("left", "DPAD_LEFT"),
    ("right", "DPAD_RIGHT"), ("a", "A"), ("b", "B"), ("plus", "PLUS"), ("home", "HOME"),
])
def test_to_button(label, btn):
    assert to_button(label) == btn


def test_press_maps_and_forwards():
    b = FakeBridge(); m = ManualController(b)
    assert m.press("up") is True
    assert b.pressed == ["DPAD_UP"]


def test_status_and_close():
    b = FakeBridge(); m = ManualController(b)
    assert m.status()["connected"] is True
    m.close(); assert b.closed is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_console_manual.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'manual'`).

- [ ] **Step 3: Implement `manual.py`**

Create `tools/sweep_console/manual.py`:

```python
"""Thin wrapper over controller_bridge.ControllerBridge for the on-screen cluster."""

_BUTTONS = {
    "up": "DPAD_UP", "down": "DPAD_DOWN", "left": "DPAD_LEFT", "right": "DPAD_RIGHT",
    "a": "A", "b": "B", "plus": "PLUS", "home": "HOME",
}


def to_button(label):
    return _BUTTONS[label]


class ManualController:
    def __init__(self, bridge):
        self._bridge = bridge

    def press(self, label):
        return self._bridge.press(to_button(label))

    def status(self):
        return self._bridge.get_status()

    def close(self):
        self._bridge.close()
```

- [ ] **Step 4: Run the test**

Run: `python -m pytest tests/test_console_manual.py -q`
Expected: PASS (11 passed).

- [ ] **Step 5: Commit**

```bash
git add tools/sweep_console/manual.py tests/test_console_manual.py
git commit -m "feat(console): ManualController button wrapper"
```

---

### Task 9: commands.py (child argv builders) + ProcessSupervisor

**Files:**
- Create: `tools/sweep_console/commands.py`, `tools/sweep_console/supervisor.py`
- Test: `tests/test_console_commands.py`

**Interfaces:**
- Produces (pure, `commands.py`): `agent_cmd(py, at_dir)`, `tracker_cmd(py, ws_port=8766)`, `sweep_cmd(py, at_dir, capture_ws, agent_port, start_from, stop_file)` — each returns an argv `list[str]`.
- Produces (`supervisor.py`): `ProcessSupervisor(repo_root, on_line, py=sys.executable)` with `start_agent()`, `start_tracker(ws_port=8766)`, `start_sweep(start_from, stop_file)`, `request_stop_file(stop_file)`, `wait_sweep(timeout)`, `kill_tracker()`, `kill_agent(distro=None)`, `clip_count()`, `read_resume()/write_resume(slug)`. `on_line(child_name, text)` is called from reader threads.

- [ ] **Step 1: Write the failing test (pure builders)**

Create `tests/test_console_commands.py`:

```python
import commands as c


def test_tracker_cmd_has_required_flags():
    cmd = c.tracker_cmd("python", ws_port=8766)
    assert cmd == ["python", "-m", "mkw_tracker", "--clip-capture",
                   "--ws-port", "8766", "--no-display"]


def test_sweep_cmd_includes_stop_file_and_optional_start_from():
    base = c.sweep_cmd("python", "/at", "ws://127.0.0.1:8766", 7878, None, "/x/.stop")
    assert "--stop-file" in base and "/x/.stop" in base
    assert "--start-from" not in base
    resumed = c.sweep_cmd("python", "/at", "ws://127.0.0.1:8766", 7878, "luigi__base", "/x/.stop")
    assert resumed[resumed.index("--start-from") + 1] == "luigi__base"


def test_agent_cmd_points_at_start_agent():
    cmd = c.agent_cmd("python", "/at")
    assert cmd[0] == "python" and cmd[-1].endswith("start_agent.py")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_console_commands.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'commands'`).

- [ ] **Step 3: Implement `commands.py`**

Create `tools/sweep_console/commands.py`:

```python
"""Pure argv builders for the three child processes."""
import os


def agent_cmd(py, at_dir):
    return [py, os.path.join(at_dir, "start_agent.py")]


def tracker_cmd(py, ws_port=8766):
    return [py, "-m", "mkw_tracker", "--clip-capture", "--ws-port", str(ws_port), "--no-display"]


def sweep_cmd(py, at_dir, capture_ws, agent_port, start_from, stop_file):
    cmd = [py, os.path.join(at_dir, "sweep_runner.py"),
           "--capture-ws", capture_ws, "--agent-port", str(agent_port),
           "--stop-file", stop_file]
    if start_from:
        cmd += ["--start-from", start_from]
    return cmd
```

- [ ] **Step 4: Run the test**

Run: `python -m pytest tests/test_console_commands.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Implement `supervisor.py` (no unit test — exercised in Task 11 smoke)**

Create `tools/sweep_console/supervisor.py`:

```python
"""Spawns + monitors the 3 children with piped stdout (no console windows).

I/O glue: pure argv come from commands.py; teardown of the in-WSL agent reuses
start_agent.stop_agent. on_line(child_name, text) is invoked from reader threads.
"""
import os
import re
import subprocess
import sys
import threading

import commands

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0     # CREATE_NO_WINDOW
_CHAR_RE = re.compile(r"-- char:\s*(\S+)\s*--")


class ProcessSupervisor:
    def __init__(self, repo_root, on_line, py=sys.executable):
        self.repo_root = repo_root
        self.at_dir = os.path.join(repo_root, "tools", "autotemplate")
        self.clips_dir = os.path.join(repo_root, "captures_sdr", "en_uk", "clips")
        self.on_line = on_line
        self.py = py
        self.procs = {}            # name -> Popen
        self._resume = os.path.join(self.clips_dir, ".resume_char")

    # ── spawning ──────────────────────────────────────────────────────────────
    def _spawn(self, name, cmd, on_exit=None):
        p = subprocess.Popen(cmd, cwd=self.repo_root, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, bufsize=1,
                              creationflags=_NO_WINDOW)
        self.procs[name] = p
        threading.Thread(target=self._pump, args=(name, p, on_exit), daemon=True).start()
        return p

    def _pump(self, name, p, on_exit):
        for line in p.stdout:
            line = line.rstrip("\n")
            if name == "sweep":
                m = _CHAR_RE.search(line)
                if m:
                    self.write_resume(m.group(1))
            self.on_line(name, line)
        p.wait()
        if on_exit:
            on_exit()

    def start_agent(self):
        return self._spawn("agent", commands.agent_cmd(self.py, self.at_dir))

    def start_tracker(self, ws_port=8766):
        return self._spawn("tracker", commands.tracker_cmd(self.py, ws_port))

    def start_sweep(self, start_from, stop_file, on_exit=None):
        try:
            if os.path.exists(stop_file):
                os.remove(stop_file)                 # clear any stale pause flag
        except OSError:
            pass
        return self._spawn("sweep",
                           commands.sweep_cmd(self.py, self.at_dir, "ws://127.0.0.1:8766",
                                              7878, start_from, stop_file), on_exit=on_exit)

    # ── stop / teardown ─────────────────────────────────────────────────────────
    def request_stop_file(self, stop_file):
        os.makedirs(os.path.dirname(stop_file), exist_ok=True)
        with open(stop_file, "w") as f:
            f.write("stop")

    def wait_sweep(self, timeout=60.0):
        p = self.procs.get("sweep")
        if p:
            try:
                p.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                p.kill()

    def _kill_tree(self, name):
        p = self.procs.pop(name, None)
        if not p or p.poll() is not None:
            return
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                           capture_output=True)
        else:
            p.terminate()

    def kill_tracker(self):
        self._kill_tree("tracker")

    def kill_agent(self, distro=None):
        # 1) stop the in-WSL agent (no in-band shutdown), 2) kill the Windows launcher tree.
        try:
            import start_agent
            start_agent.stop_agent(distro=distro)
        except Exception as exc:                      # best-effort
            self.on_line("agent", f"[console] stop_agent failed: {exc}")
        self._kill_tree("agent")

    # ── progress / resume marker ────────────────────────────────────────────────
    def clip_count(self):
        try:
            return sum(1 for f in os.listdir(self.clips_dir) if f.endswith(".mkv"))
        except OSError:
            return 0

    def read_resume(self):
        try:
            with open(self._resume) as f:
                return f.read().strip() or None
        except OSError:
            return None

    def write_resume(self, slug):
        try:
            os.makedirs(self.clips_dir, exist_ok=True)
            with open(self._resume, "w") as f:
                f.write(slug)
        except OSError:
            pass
```

- [ ] **Step 6: Commit**

```bash
git add tools/sweep_console/commands.py tools/sweep_console/supervisor.py tests/test_console_commands.py
git commit -m "feat(console): child argv builders + ProcessSupervisor"
```

---

### Task 10: WsConsumer

**Files:**
- Create: `tools/sweep_console/wsconsumer.py`
- Test: `tests/test_console_wsconsumer.py`

**Interfaces:**
- Produces: `route(msg: dict) -> tuple[str, dict]` (pure: `("preview", msg)` or `("state", msg)`); `WsConsumer(url, on_preview, on_state)` with `start()` and `close()` (background asyncio thread; mirrors `sweep_runner.WsClient`).

- [ ] **Step 1: Write the failing test (pure router)**

Create `tests/test_console_wsconsumer.py`:

```python
from wsconsumer import route


def test_route_preview():
    kind, msg = route({"type": "preview", "data": "x"})
    assert kind == "preview"


def test_route_state():
    assert route({"type": "heartbeat", "fps": 60})[0] == "state"
    assert route({"type": "selection_update"})[0] == "state"
    assert route({"type": "clip_done", "item": "z"})[0] == "state"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_console_wsconsumer.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'wsconsumer'`).

- [ ] **Step 3: Implement `wsconsumer.py`**

Create `tools/sweep_console/wsconsumer.py`:

```python
"""Read-only subscriber to the tracker broadcaster (:8766).

route() is pure; the socket loop mirrors sweep_runner.WsClient (background
asyncio thread). Connects to 127.0.0.1 (broadcaster binds IPv4; ::1 refused).
"""
import asyncio
import json
import threading


def route(msg):
    if isinstance(msg, dict) and msg.get("type") == "preview":
        return "preview", msg
    return "state", msg


class WsConsumer:
    def __init__(self, url, on_preview, on_state):
        self._url = url
        self._on_preview = on_preview
        self._on_state = on_state
        self._loop = asyncio.new_event_loop()
        self._stop = False

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="ws-consumer").start()

    def _run(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._main())

    async def _main(self):
        try:
            import websockets
        except ImportError:
            self._on_state({"type": "console_error", "message": "websockets not installed"})
            return
        while not self._stop:
            try:
                async with websockets.connect(self._url) as ws:
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except Exception:
                            continue
                        kind, payload = route(msg)
                        (self._on_preview if kind == "preview" else self._on_state)(payload)
            except Exception:
                await asyncio.sleep(1.0)            # reconnect after a beat

    def close(self):
        self._stop = True
        self._loop.call_soon_threadsafe(self._loop.stop)
```

- [ ] **Step 4: Run the test**

Run: `python -m pytest tests/test_console_wsconsumer.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add tools/sweep_console/wsconsumer.py tests/test_console_wsconsumer.py
git commit -m "feat(console): read-only WS consumer (state + preview)"
```

---

### Task 11: ConsoleApp (Tk window) + launcher

**Files:**
- Create: `tools/sweep_console/app.py`, `run_console.bat`

**Interfaces:**
- Consumes: every prior module. No `Produces` (this is the entry point).

- [ ] **Step 1: Implement `app.py`**

Create `tools/sweep_console/app.py`:

```python
"""Sweep Console — one Tk window supervising agent + tracker + sweep.

Thin wiring: pure logic lives in controlstate/progress/health; I/O in
supervisor/wsconsumer/manual. Cross-thread updates are marshalled onto the Tk
main thread via a queue drained by after().
"""
import base64
import os
import queue
import sys
import time
import tkinter as tk
from tkinter import ttk

_HERE = os.path.dirname(os.path.abspath(__file__))
for _d in (_HERE, os.path.join(_HERE, "..", "autotemplate")):
    _p = os.path.abspath(_d)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import controlstate as cs
from controller_bridge import ControllerBridge
from health import HealthModel
from manual import ManualController
from progress import ProgressModel
from supervisor import ProcessSupervisor
from wsconsumer import WsConsumer

REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
STOP_FILE = os.path.join(REPO_ROOT, "captures_sdr", "en_uk", ".sweep_stop")
TOTAL = 6273
WS_URL = "ws://127.0.0.1:8766"


def _fmt_eta(s):
    if s is None:
        return "—"
    s = int(s)
    return f"{s // 3600}h{(s % 3600) // 60:02d}m"


class ConsoleApp:
    def __init__(self, root):
        self.root = root
        root.title("MKW Asset Sweep")
        self.q = queue.Queue()                       # (callable) marshalled to the Tk thread
        self.state = cs.ControlState()
        self.health = HealthModel()
        self.progress = ProgressModel(TOTAL)
        self.sup = ProcessSupervisor(REPO_ROOT, self._on_line)
        self.ws = None
        self.manual = None
        self._photo = None
        self._build()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.after(100, self._drain)
        root.after(1000, self._tick)

    # ── layout ──────────────────────────────────────────────────────────────────
    def _build(self):
        bar = ttk.Frame(self.root); bar.pack(fill="x", padx=6, pady=4)
        self.btn = {}
        for key, label in [("start", "Start Rig"), ("begin", "Begin Sweep"),
                           ("pause", "Pause"), ("stop", "Stop")]:
            b = ttk.Button(bar, text=label, command=lambda k=key: self._click(k))
            b.pack(side="left", padx=2); self.btn[key] = b
        self.status = ttk.Label(bar, text="● idle"); self.status.pack(side="right")

        body = ttk.Frame(self.root); body.pack(fill="both", expand=True)
        left = ttk.Frame(body); left.pack(side="left", fill="y", padx=6, pady=6)
        self.thumb = tk.Label(left, width=320, height=180, background="#111")
        self.thumb.pack()
        man = ttk.LabelFrame(left, text="Manual control"); man.pack(pady=8, fill="x")
        grid = ttk.Frame(man); grid.pack(padx=4, pady=4)
        for (r, c, key, txt) in [(0, 1, "up", "▲"), (1, 0, "left", "◀"),
                                 (1, 2, "right", "▶"), (2, 1, "down", "▼")]:
            ttk.Button(grid, width=3, text=txt,
                       command=lambda k=key: self._manual(k)).grid(row=r, column=c, padx=1, pady=1)
        extra = ttk.Frame(man); extra.pack(padx=4, pady=4)
        for key, txt in [("a", "A"), ("b", "B"), ("plus", "+"), ("home", "HOME")]:
            ttk.Button(extra, width=4, text=txt,
                       command=lambda k=key: self._manual(k)).pack(side="left", padx=1)
        self.man_frame = man

        right = ttk.Frame(body); right.pack(side="left", fill="both", expand=True, padx=6, pady=6)
        self.hl = ttk.Label(right, justify="left", font=("Consolas", 10))
        self.hl.pack(anchor="w")
        self.logs = {}
        for name in ("agent", "tracker", "sweep"):
            lf = ttk.LabelFrame(right, text=name); lf.pack(fill="both", expand=True, pady=2)
            txt = tk.Text(lf, height=7, wrap="none", font=("Consolas", 8))
            txt.pack(fill="both", expand=True); txt.configure(state="disabled")
            self.logs[name] = txt
        self._refresh_buttons()

    # ── thread-safe plumbing ─────────────────────────────────────────────────────
    def _on_line(self, name, text):
        self.q.put(lambda: self._append(name, text))

    def _on_preview(self, msg):
        self.q.put(lambda: self._set_thumb(msg))

    def _on_state(self, msg):
        self.q.put(lambda: self._apply_state(msg))

    def _drain(self):
        try:
            while True:
                self.q.get_nowait()()
        except queue.Empty:
            pass
        self.root.after(100, self._drain)

    def _append(self, name, text):
        t = self.logs.get(name)
        if not t:
            return
        t.configure(state="normal")
        t.insert("end", text + "\n")
        if int(t.index("end-1c").split(".")[0]) > 400:
            t.delete("1.0", "100.0")
        t.see("end"); t.configure(state="disabled")

    def _set_thumb(self, msg):
        try:
            self._photo = tk.PhotoImage(data=msg["data"])   # base64 PNG (Tk 8.6)
            self.thumb.configure(image=self._photo)
        except Exception:
            pass

    def _apply_state(self, msg):
        self.health.apply(msg, now=time.monotonic())
        if msg.get("type") == "clip_done":
            self.progress.update(self.sup.clip_count(), time.monotonic())

    # ── controls ─────────────────────────────────────────────────────────────────
    def _click(self, key):
        event = {"start": cs.START_RIG, "begin": cs.BEGIN_SWEEP,
                 "stop": cs.STOP}.get(key)
        if key == "pause":
            event = cs.RESUME if self.state.state == cs.PAUSED else cs.PAUSE
        for act in self.state.on_event(event):
            self._do(act)
        self._refresh_buttons()

    def _do(self, action):
        if action == "start_agent":
            self.sup.start_agent()
        elif action == "start_tracker":
            self.sup.start_tracker()
        elif action == "connect_ws":
            self.ws = WsConsumer(WS_URL, self._on_preview, self._on_state); self.ws.start()
        elif action == "connect_manual":
            br = ControllerBridge(); br.connect(); br.start_reconnect_loop()
            self.manual = ManualController(br)
        elif action in ("enable_manual", "disable_manual"):
            pass                                       # handled by _refresh_buttons
        elif action == "start_sweep":
            start_from = self.sup.read_resume()
            self.sup.start_sweep(start_from, STOP_FILE, on_exit=self._sweep_exited)
        elif action == "request_sweep_stop":
            self.sup.request_stop_file(STOP_FILE)
        elif action == "stop_rig":
            self.sup.kill_tracker(); self.sup.kill_agent()
        elif action == "disconnect":
            if self.ws: self.ws.close(); self.ws = None
            if self.manual: self.manual.close(); self.manual = None

    def _sweep_exited(self):
        self.q.put(lambda: self._after_sweep_exit())

    def _after_sweep_exit(self):
        for act in self.state.on_event(cs.SWEEP_EXITED):
            self._do(act)
        self._refresh_buttons()

    def _manual(self, key):
        if self.manual and self.state.state in (cs.RIG_WARM, cs.PAUSED):
            self.manual.press(key)

    def _refresh_buttons(self):
        s = self.state.state
        self.btn["start"].configure(state=("normal" if s == cs.IDLE else "disabled"))
        self.btn["begin"].configure(state=("normal" if s == cs.RIG_WARM else "disabled"))
        self.btn["pause"].configure(
            text=("Resume" if s == cs.PAUSED else "Pause"),
            state=("normal" if s in (cs.SWEEPING, cs.PAUSED) else "disabled"))
        self.btn["stop"].configure(state=("disabled" if s == cs.IDLE else "normal"))
        manual_on = s in (cs.RIG_WARM, cs.PAUSED)
        for child in self.man_frame.winfo_children():
            for b in child.winfo_children():
                try: b.configure(state=("normal" if manual_on else "disabled"))
                except tk.TclError: pass
        self.status.configure(text=f"● {s.lower().replace('_', ' ')}")

    # ── 1 Hz refresh ─────────────────────────────────────────────────────────────
    def _tick(self):
        if self.manual:
            try:
                st = self.manual.status(); self.health.set_controller(st["connected"], st["mac"])
            except Exception:
                self.health.set_controller(False)
        self.progress.update(self.sup.clip_count(), time.monotonic())
        h = self.health.snapshot(time.monotonic()); pr = self.progress.snapshot()
        age = h["last_clip_age"]
        self.hl.configure(text=(
            f"controller {'OK' if h['controller'] else 'X'}    screen {h['screen'] or '-'}\n"
            f"{h['character'] or '-'} / {h['costume'] or '-'} / {h['kart'] or '-'}\n"
            f"clips {pr['done']}/{pr['total']}  {pr['pct']*100:4.1f}%   ETA {_fmt_eta(pr['eta_seconds'])}\n"
            f"last clip {('%.0fs' % age) if age is not None else '-'}   fps {h['fps'] or '-'}"))
        self.root.after(1000, self._tick)

    def _on_close(self):
        if self.state.state != cs.IDLE:
            for act in self.state.on_event(cs.STOP):
                self._do(act)
            if self.state.state == cs.STOP_REQUESTED:
                self.sup.wait_sweep(timeout=60)
                for act in self.state.on_event(cs.SWEEP_EXITED):
                    self._do(act)
        self.root.destroy()


def main():
    root = tk.Tk()
    ConsoleApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Create the launcher**

Create `run_console.bat`:

```bat
@echo off
cd /d "%~dp0"
python tools\sweep_console\app.py
```

- [ ] **Step 3: Verify the app imports cleanly (no display needed)**

Run: `python -c "import ast; ast.parse(open('tools/sweep_console/app.py').read())"`
Expected: no output.
Run: `python -m pytest -q`
Expected: PASS (full suite, including all console tests).

- [ ] **Step 4: Manual smoke test (bring-up, on the rig)**

With the Switch on and at the title/home, run `run_console.bat`. Confirm, in order:
1. **Start Rig** → Agent + Tracker panes stream output; "rig warm"; manual buttons enabled.
2. Manual D-pad/A drive to character-select; the **thumbnail updates** (~2 Hz) and matches the screen.
3. **Begin Sweep** → Sweep pane streams `-- char: … --`; manual buttons grey out; health strip shows screen/char/kart, `clips n/6273`, ETA, last-clip age.
4. **Pause** → after the current clip the sweep returns to CHARACTER_SELECT and exits; button shows **Resume**; manual re-enabled.
5. **Resume** → sweep continues from the same character (skips recorded clips fast).
6. **Stop** → all three torn down, "idle"; verify no lingering `controller_agent.py` in WSL (`wsl -- pgrep -af controller_agent.py` returns nothing).
7. Re-open and **Start Rig → Begin Sweep** → resumes from the marker (continues where it left off).

- [ ] **Step 5: Commit**

```bash
git add tools/sweep_console/app.py run_console.bat
git commit -m "feat(console): Tk supervisor window + launcher"
```

---

## Self-Review

**Spec coverage:**
- One window / no extra consoles → Task 11 (`CREATE_NO_WINDOW`, single Tk window). ✓
- Three split log panes → Task 11 (three `Text` widgets). ✓
- Thumbnail (no overlay) → Tasks 4–5 (broadcast) + 10 (route) + 11 (display); tracker `--no-display`. ✓
- Health strip → Task 3 + Task 11 `_tick`. ✓
- Two-step start, Pause/Resume, Stop, stop-on-close → Task 1 (machine) + Task 11. ✓
- Pause = graceful between clips + warm rig; Resume continues; cross-session resume marker → Task 6 (stop-file) + Task 9 (resume marker) + Task 1. ✓
- Manual cluster (click only) → Task 8 + Task 11. ✓
- Clean in-WSL teardown → Task 7 + Task 9 `kill_agent`. ✓
- Minimal resources: no device open (WS-only), `--no-display` removes the cv2 window, ~2 Hz small PNG → Tasks 4–5, 10–11. ✓

**Placeholder scan:** No TBD/TODO; every code step has complete code; glue-only steps (5, 9 supervisor, 11 app) are flagged as manual-verified with concrete smoke checks. ✓

**Type consistency:** `ControlState.on_event` action strings ↔ `app._do` handlers match (`start_agent/start_tracker/connect_ws/connect_manual/enable_manual/disable_manual/start_sweep/request_sweep_stop/stop_rig/disconnect`). `sweep_karts` returns `bool` ↔ `main()` consumes a bool. `maybe_preview` dict shape (`type/w/h/data`) ↔ `route` "preview" ↔ `_set_thumb` `data`. `ProcessSupervisor.start_sweep(start_from, stop_file, on_exit)` ↔ `app._do`. ✓

**Note (refines spec lifecycle):** the controller agent is **multi-client** (`listen(8)`, thread-per-connection), so the manual `ControllerBridge` stays connected throughout; the buttons are merely disabled during `SWEEPING` (Task 11 `_refresh_buttons`) rather than the bridge being closed/reopened. This is simpler than and equivalent to the spec's "close manual bridge before sweep" note, with no controller contention (manual is click-disabled while the sweep drives).

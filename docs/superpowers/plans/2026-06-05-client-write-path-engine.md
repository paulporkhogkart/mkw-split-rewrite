# Client Write Path — Engine `run_finalized` Emit (sub-project B, Phase 1, part 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Python engine emit one consolidated `run_finalized` IPC event per attempt (finished **or** reset), carrying the full attempt payload, so the Tauri/Rust app can forward it to the server. The engine stays a pure detector: it just emits — no network.

**Architecture:** Add a `run_finalized` emitter to `ipc/protocol.py` and a `points` accessor to `MinimapRecorder`, then build + emit the payload inside `RaceLifecycle._finalize_recording` (which already fires on every attempt end), just before the existing local `save()`. The engine mints the `attempt_id` (UUID). This is the first of two plans for the client write path; the second is the Tauri/Rust `sync.rs` outbox + uploader + Sync settings tab.

**Tech Stack:** Python ≥3.10, pytest. Spec: `docs/superpowers/specs/2026-06-04-server-api-and-sync-design.md` (§11 payload, §14 client write path).

**Scope notes (carried from the spec / discovered here):**
- **Per-lap coins/shrooms are emitted as absent (null).** The engine currently tracks lap split *times* (`ts.splits`) and running coin/mushroom *totals*, not per-lap buckets. Bucketing coins/shrooms per lap is a separate capture enhancement; the payload's `laps` carry `time_ms` only for now (the server schema + wire allow null coins/shrooms).
- **`cc` is omitted** from the engine payload (the server defaults missing `cc` to 150). It becomes an app-injected setting in the sync plan.
- **`started_at` is null**, `ended_at` is the wall-clock at finalize. (The recorder tracks elapsed via `perf_counter`, not wall-clock; good-enough for the server's timeline ordering, which uses `ended_at`.)

Every commit ends with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Work on a new branch off `main` (see Task 1). Run pytest from the repo root.

---

## File Structure

| Path | Change |
|---|---|
| `mkw_tracker/minimap/recorder.py` | Add a `points` property (read-only copy of `_points`). |
| `mkw_tracker/ipc/protocol.py` | Add `emit_run_finalized(run: dict) -> str`. |
| `mkw_tracker/lifecycle/race.py` | In `_finalize_recording`, build + emit the `run_finalized` payload (mint `attempt_id`) just before `save()`. |
| `tests/test_run_finalized.py` | New tests for the emitter, the recorder accessor, and the lifecycle wiring. |

**Interface locked here:** `emit_run_finalized(run: dict)` returns the JSON line `{"type":"run_finalized", **run}`. The `run` dict has keys: `attempt_id, course, status('finished'|'reset'), character, kart, costume, started_at, ended_at, total_time, laps:[{lap,time_ms}], points:[[t_ms,cx,cy,score]]`.

---

### Task 1: Branch + recorder `points` accessor + `emit_run_finalized`

**Files:** Modify `mkw_tracker/minimap/recorder.py`, `mkw_tracker/ipc/protocol.py`; create `tests/test_run_finalized.py`.

- [ ] **Step 1: Create the branch**

Run (from repo root): `git checkout -b client-write-path-engine`
Expected: `Switched to a new branch 'client-write-path-engine'`.

- [ ] **Step 2: Write the failing tests** `tests/test_run_finalized.py`

```python
"""Tests for the run_finalized IPC emit + recorder points accessor."""
import json
from mkw_tracker.minimap.recorder import MinimapRecorder
from mkw_tracker.ipc.protocol import emit_run_finalized


def test_recorder_points_returns_copy():
    rec = MinimapRecorder()
    rec._points = [(0, 1.0, 2.0, 0.9), (16, 1.1, 2.1, 0.95)]
    pts = rec.points
    assert pts == [(0, 1.0, 2.0, 0.9), (16, 1.1, 2.1, 0.95)]
    pts.append((99, 0.0, 0.0, 0.0))            # mutating the copy
    assert len(rec._points) == 2               # ...does not touch the recorder


def test_emit_run_finalized_shapes_the_line():
    line = emit_run_finalized({
        "attempt_id": "abc", "course": "Rainbow Road", "status": "finished",
        "total_time": "1:23.456", "laps": [{"lap": 1, "time_ms": 41000}],
        "points": [[0, 1.0, 2.0, 0.9]],
    })
    obj = json.loads(line)
    assert obj["type"] == "run_finalized"
    assert obj["attempt_id"] == "abc"
    assert obj["course"] == "Rainbow Road"
    assert obj["status"] == "finished"
    assert obj["laps"] == [{"lap": 1, "time_ms": 41000}]
    assert obj["points"] == [[0, 1.0, 2.0, 0.9]]
```

- [ ] **Step 3: Run to verify it fails**

Run: `python -m pytest tests/test_run_finalized.py -v`
Expected: FAIL — `ImportError: cannot import name 'emit_run_finalized'` (and the recorder test would fail on `.points`).

- [ ] **Step 4: Add the `points` property to `MinimapRecorder`** (after the existing `is_paused` property, ~line 33)

```python
    @property
    def points(self) -> list:
        """Read-only copy of the recorded points (list of (t_ms, cx, cy, score))."""
        return list(self._points)
```

- [ ] **Step 5: Add `emit_run_finalized` to `ipc/protocol.py`** (near the other `emit_*` functions, e.g. after `emit_pb_export`)

```python
def emit_run_finalized(run: dict) -> str:
    """Serialise the full finalized-attempt payload for the Tauri app to upload.

    ``run`` carries: attempt_id, course, status, character, kart, costume,
    started_at, ended_at, total_time, laps [{lap, time_ms}], points [[t_ms,cx,cy,score]].
    """
    return json.dumps({"type": "run_finalized", **run})
```

- [ ] **Step 6: Run to verify it passes**

Run: `python -m pytest tests/test_run_finalized.py -v`
Expected: PASS (2 tests).

- [ ] **Step 7: Commit**

```bash
git add mkw_tracker/minimap/recorder.py mkw_tracker/ipc/protocol.py tests/test_run_finalized.py
git commit -m "feat(engine): run_finalized emitter + recorder points accessor" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Emit `run_finalized` from `_finalize_recording`

**Files:** Modify `mkw_tracker/lifecycle/race.py`; add tests to `tests/test_run_finalized.py`.

- [ ] **Step 1: Add the failing tests** (append to `tests/test_run_finalized.py`)

```python
import json
from unittest.mock import MagicMock
from mkw_tracker.detection.screen import Screen
from mkw_tracker.race.finish import FinishStillDetector
from mkw_tracker.lifecycle.race import RaceLifecycle


class _FakeIpc:
    def __init__(self): self.lines = []
    def emit(self, line): self.lines.append(json.loads(line))


def _lifecycle(ipc, total_time, splits):
    sel = MagicMock()
    sel.state.course = "Rainbow Road"
    sel.state.character = "Mario"
    sel.state.costume = "Base"
    sel.state.kart = "Standard Kart"
    ts = MagicMock(); ts.total_time = total_time; ts.splits = splits
    minimap = MagicMock(); minimap._calibrated = True   # skip calibrate branch
    mm_rec = MagicMock(); mm_rec.points = [(0, 1.0, 2.0, 0.9)]; mm_rec.save.return_value = None
    return RaceLifecycle(
        selection=sel, laps=MagicMock(), coins=MagicMock(), ts=ts,
        finish=FinishStillDetector(), mush=MagicMock(), minimap=minimap,
        mm_rec=mm_rec, mm_player=MagicMock(), ipc=ipc,
    )


def _run_finalized(ipc):
    return next(l for l in ipc.lines if l["type"] == "run_finalized")


def test_finish_emits_run_finalized():
    ipc = _FakeIpc()
    lc = _lifecycle(ipc, total_time="1:23.456", splits={1: "0:41.000", 2: "1:23.456"})
    lc.on_screen_change(Screen.RACING, Screen.POST_TIME_TRIAL)
    evt = _run_finalized(ipc)
    assert evt["status"] == "finished"
    assert evt["course"] == "Rainbow Road"
    assert evt["total_time"] == "1:23.456"
    assert evt["character"] == "Mario" and evt["kart"] == "Standard Kart" and evt["costume"] == "Base"
    assert evt["laps"] == [{"lap": 1, "time_ms": 41000}, {"lap": 2, "time_ms": 83456}]
    assert evt["points"] == [[0, 1.0, 2.0, 0.9]]
    assert isinstance(evt["attempt_id"], str) and len(evt["attempt_id"]) >= 8
    assert evt["ended_at"] is not None


def test_reset_emits_run_finalized_with_null_total():
    ipc = _FakeIpc()
    lc = _lifecycle(ipc, total_time=None, splits={})
    lc.on_screen_change(Screen.RACING, Screen.RESET)
    evt = _run_finalized(ipc)
    assert evt["status"] == "reset"
    assert evt["total_time"] is None
    assert evt["laps"] == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_run_finalized.py -k "emits" -v`
Expected: FAIL — no `run_finalized` line is emitted (the lifecycle doesn't emit it yet), so `_run_finalized` raises `StopIteration`.

- [ ] **Step 3: Emit in `_finalize_recording`** — in `mkw_tracker/lifecycle/race.py`, insert the emit block immediately **before** the `replay_id = self._mm_rec.save(...)` call (currently ~line 165), after the calibrate branch:

```python
        # Emit the full finalized-attempt payload for the Tauri app to upload.
        # (Pure detector: emit only; no network. Built before save() clears points.)
        if self._ipc is not None and course:
            import uuid
            from datetime import datetime, timezone
            from ..ipc.protocol import emit_run_finalized
            from ..database.replay_repo import _to_ms
            laps = [{"lap": int(lap), "time_ms": _to_ms(txt)}
                    for lap, txt in sorted(self._ts.splits.items())]
            self._ipc.emit(emit_run_finalized({
                "attempt_id": uuid.uuid4().hex,
                "course":     course,
                "status":     "finished" if completed else "reset",
                "character":  character,
                "kart":       sel.kart,
                "costume":    costume,
                "started_at": None,
                "ended_at":   datetime.now(timezone.utc).isoformat(),
                "total_time": best_total_time,
                "laps":       laps,
                "points":     [[t, cx, cy, sc] for (t, cx, cy, sc) in self._mm_rec.points],
            }))
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_run_finalized.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Confirm no regression in the lifecycle suite**

Run: `python -m pytest tests/test_race_lifecycle_finish.py tests/test_run_finalized.py -q`
Expected: PASS (the existing finalize tests still pass; they pass `ipc=None` so the new block is skipped).

- [ ] **Step 6: Commit**

```bash
git add mkw_tracker/lifecycle/race.py tests/test_run_finalized.py
git commit -m "feat(engine): emit run_finalized on every attempt finalize" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Notes for the executor

- Run pytest from the repo root. The new block uses a local import of `_to_ms` from `database.replay_repo` (already the lifecycle's pattern of importing from `replay_repo`).
- The emit fires only when `self._ipc` is set **and** a `course` is known — runs with no detected course are not uploadable, so they're skipped (mirrors `recorder.save`, which returns `None` without a course).
- This plan is **engine-only**. The Tauri/Rust `sync.rs` (rusqlite outbox + retrying uploader) and the "Sync" settings tab — which consume this `run_finalized` event — are the **second client-write-path plan** (needs new `reqwest` + `rusqlite` Cargo deps and mirrors the `discord.rs` decoupled-module pattern).

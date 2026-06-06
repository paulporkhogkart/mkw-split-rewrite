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
    assert evt["started_at"] is None             # no _start_race ran in this shortcut


def test_run_finalized_includes_started_at_from_race_start():
    ipc = _FakeIpc()
    lc = _lifecycle(ipc, total_time="1:23.456", splits={1: "0:41.000"})
    # _start_race sets this on a fresh RACING entry; set it directly so the test
    # doesn't need the DB-backed minimap seed/ROI lookups _start_race performs.
    lc._race_started_at = "2026-06-05T12:00:00+00:00"
    lc.on_screen_change(Screen.RACING, Screen.POST_TIME_TRIAL)
    evt = _run_finalized(ipc)
    assert evt["started_at"] == "2026-06-05T12:00:00+00:00"


def test_finish_lock_emits_once_and_screen_change_does_not_duplicate():
    # Bug 1: the finished run must be emitted the instant the final time locks
    # (finalize_on_finish, called from the main loop), and the later POST_TIME_TRIAL
    # screen change must NOT emit a duplicate.
    ipc = _FakeIpc()
    lc = _lifecycle(ipc, total_time="1:23.456", splits={1: "0:41.000", 2: "1:23.456"})
    lc._race_started_at = "2026-06-06T12:00:00+00:00"
    lc.finalize_on_finish()
    finalized = [l for l in ipc.lines if l["type"] == "run_finalized"]
    assert len(finalized) == 1
    assert finalized[0]["status"] == "finished"
    assert finalized[0]["total_time"] == "1:23.456"
    lc.on_screen_change(Screen.RACING, Screen.POST_TIME_TRIAL)
    finalized = [l for l in ipc.lines if l["type"] == "run_finalized"]
    assert len(finalized) == 1


def test_reset_emits_run_finalized_with_null_total():
    ipc = _FakeIpc()
    lc = _lifecycle(ipc, total_time=None, splits={})
    lc.on_screen_change(Screen.RACING, Screen.RESET)
    evt = _run_finalized(ipc)
    assert evt["status"] == "reset"
    assert evt["total_time"] is None
    assert evt["laps"] == []

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

"""Tests for replay_repo.replay_paths and the get_replay_paths IPC handler."""
import json
import pytest

from mkw_tracker.database.replay_repo import save_run, replay_paths
from mkw_tracker.database.connection import get_connection


# ── Unit tests for replay_paths() ────────────────────────────────────────────

def test_replay_paths_returns_one_entry(memdb):
    """Insert one run with points; replay_paths must return exactly one entry."""
    points = [(0, 1600.0, 700.0, 0.95), (500, 1610.0, 710.0, 0.90)]
    save_run("Mario Circuit", points, total_time="1:23.456")
    result = replay_paths(get_connection(), "Mario Circuit")
    assert len(result) == 1


def test_replay_paths_points_are_time_xy_triples(memdb):
    """Each point must be [t_ms, x, y] — the leading time axis lets the UI
    interpolate a moving dot rather than drawing a frozen polyline."""
    points = [(0, 1600.0, 700.0, 0.95), (500, 1610.0, 710.0, 0.90)]
    save_run("Mario Circuit", points, total_time="1:23.456")
    result = replay_paths(get_connection(), "Mario Circuit")
    assert len(result) == 1
    pts = result[0]["points"]
    assert len(pts) == 2
    assert pts[0] == [0, 1600.0, 700.0]
    assert pts[1] == [500, 1610.0, 710.0]


def test_replay_paths_carries_run_metadata(memdb):
    """Each entry exposes the fields the UI needs to style dots: label, is_pb,
    total_time (None marks an abandoned run)."""
    save_run("Mario Circuit", [(0, 1600.0, 700.0, 0.95)], total_time="1:23.456")
    entry = replay_paths(get_connection(), "Mario Circuit")[0]
    assert "label" in entry
    assert entry["is_pb"] in (True, False)
    assert "total_time" in entry


def test_replay_paths_id_field_present(memdb):
    """Each entry must have an 'id' field."""
    points = [(0, 1500.0, 600.0, 0.80)]
    save_run("Rainbow Road", points, total_time="2:00.000")
    result = replay_paths(get_connection(), "Rainbow Road")
    assert "id" in result[0]


def test_replay_paths_multiple_runs(memdb):
    """Two runs on the same course produce two entries."""
    save_run("Rainbow Road", [(0, 1500.0, 600.0, 0.8)], total_time="2:00.000")
    save_run("Rainbow Road", [(0, 1510.0, 610.0, 0.9)], total_time="1:59.999")
    result = replay_paths(get_connection(), "Rainbow Road")
    assert len(result) == 2


def test_replay_paths_empty_for_unknown_course(memdb):
    """No runs for an unknown course returns an empty list."""
    result = replay_paths(get_connection(), "Nonexistent Course")
    assert result == []


def test_replay_paths_course_filter(memdb):
    """Points from a different course are not included."""
    save_run("Mario Circuit", [(0, 1600.0, 700.0, 0.95)], total_time="1:23.456")
    save_run("Rainbow Road",  [(0, 1500.0, 600.0, 0.80)], total_time="2:00.000")
    result = replay_paths(get_connection(), "Mario Circuit")
    assert len(result) == 1
    assert result[0]["points"] == [[0, 1600.0, 700.0]]


def test_replay_paths_aborted_run_included(memdb):
    """Aborted runs (total_time=None) are still included — they have valid points."""
    save_run("Peach Beach", [(0, 1700.0, 750.0, 0.85)], total_time=None)
    result = replay_paths(get_connection(), "Peach Beach")
    assert len(result) == 1


def test_replay_paths_run_with_no_points_excluded(memdb):
    """A run with no recorded points is excluded (save_run returns None for empty)."""
    # save_run returns None early when points is empty and skips INSERT
    rid = save_run("Peach Beach", [], total_time="1:00.000")
    assert rid is not None  # row inserted, but no replay_points rows
    result = replay_paths(get_connection(), "Peach Beach")
    # The replay row exists but has no points — replay_paths should exclude it
    assert len(result) == 0


# ── Integration test: IPC dispatch ────────────────────────────────────────────

def test_dispatch_get_replay_paths(memdb):
    """Dispatching get_replay_paths returns a replay_paths-typed result."""
    import json
    from unittest.mock import MagicMock

    # Insert a run so the result is non-empty
    save_run("Mario Circuit", [(0, 1600.0, 700.0, 0.95)], total_time="1:23.456")

    emitted = []

    class FakeIpc:
        def emit(self, line):
            emitted.append(json.loads(line))

    fake_ipc = FakeIpc()
    msg = {"type": "get_replay_paths", "course": "Mario Circuit"}

    # Import and call the same dispatch function used by the main loop
    from mkw_tracker.main import _handle_ipc_command
    _handle_ipc_command(
        msg, fake_ipc,
        detector=MagicMock(), settings=MagicMock(),
        minimap=MagicMock(), lifecycle=MagicMock(),
        show_debug=[False], cap=None,
        current_frame=[None], setup_mode=[False],
    )

    assert len(emitted) == 1
    evt = emitted[0]
    assert evt["type"] == "replay_paths"
    assert evt["course"] == "Mario Circuit"
    assert isinstance(evt["paths"], list)
    assert len(evt["paths"]) == 1


def test_dispatch_get_minimap_sample_no_image(memdb):
    """get_minimap_sample with no seed emits replay_minimap_sample with png_b64=null."""
    import json
    from unittest.mock import MagicMock

    emitted = []

    class FakeIpc:
        def emit(self, line):
            emitted.append(json.loads(line))

    fake_ipc = FakeIpc()
    msg = {"type": "get_minimap_sample", "course": "Nonexistent Course"}

    from mkw_tracker.main import _handle_ipc_command
    _handle_ipc_command(
        msg, fake_ipc,
        detector=MagicMock(), settings=MagicMock(),
        minimap=MagicMock(), lifecycle=MagicMock(),
        show_debug=[False], cap=None,
        current_frame=[None], setup_mode=[False],
    )

    assert len(emitted) == 1
    evt = emitted[0]
    assert evt["type"] == "minimap_sample"
    assert evt["course"] == "Nonexistent Course"
    assert evt["png_b64"] is None


def test_dispatch_get_screen_thumbs(memdb):
    """get_screen_thumbs emits a screen_thumbs message carrying a {screen: b64} dict."""
    import json
    from unittest.mock import MagicMock

    emitted = []

    class FakeIpc:
        def emit(self, line):
            emitted.append(json.loads(line))

    from mkw_tracker.main import _handle_ipc_command
    _handle_ipc_command(
        {"type": "get_screen_thumbs", "lang": "en_uk"}, FakeIpc(),
        detector=MagicMock(), settings=MagicMock(),
        minimap=MagicMock(), lifecycle=MagicMock(),
        show_debug=[False], cap=None,
        current_frame=[None], setup_mode=[False],
    )
    assert len(emitted) == 1
    evt = emitted[0]
    assert evt["type"] == "screen_thumbs"
    assert isinstance(evt["thumbs"], dict)
    # Values (when present) are base64 PNG strings keyed by Screen enum name.
    for name, b64 in evt["thumbs"].items():
        assert isinstance(name, str) and isinstance(b64, str) and b64

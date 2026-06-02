"""Tests for per-lap split persistence and the get_pb_splits IPC."""
import json
from unittest.mock import MagicMock

from mkw_tracker.database.connection import get_connection
from mkw_tracker.database.migrations import apply_migrations
from mkw_tracker.database.replay_repo import save_run, get_pb_splits


def test_replay_splits_table_exists(memdb):
    row = get_connection().execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='replay_splits'"
    ).fetchone()
    assert row is not None


def test_replay_splits_added_to_existing_db(memdb):
    """Pre-v4 DBs must gain the table via migration, not only fresh DBs."""
    conn = get_connection()
    conn.execute("DROP TABLE IF EXISTS replay_splits")
    conn.execute("UPDATE schema_version SET version=3")
    conn.commit()
    apply_migrations(memdb)
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='replay_splits'"
    ).fetchone()
    assert row is not None


def test_save_run_persists_lap_splits(memdb):
    rid = save_run(
        "Mario Circuit",
        [(0, 1600.0, 700.0, 0.95)],
        total_time="1:23.456",
        lap_splits={1: "0:41.000", 2: "1:23.456"},
    )
    rows = get_connection().execute(
        "SELECT lap, split_ms, split_text FROM replay_splits WHERE replay_id=? ORDER BY lap",
        (rid,),
    ).fetchall()
    assert [(r["lap"], r["split_ms"], r["split_text"]) for r in rows] == [
        (1, 41000, "0:41.000"),
        (2, 83456, "1:23.456"),
    ]


def test_get_pb_splits_returns_pb_splits(memdb):
    # First run becomes the PB (no prior PB), with splits.
    save_run("Rainbow Road", [(0, 1.0, 2.0, 0.9)], total_time="2:00.000",
             lap_splits={1: "0:40.000", 2: "1:20.000", 3: "2:00.000"})
    assert get_pb_splits("Rainbow Road") == {1: 40000, 2: 80000, 3: 120000}


def test_get_pb_splits_none_when_no_pb(memdb):
    assert get_pb_splits("Rainbow Road") is None


def test_dispatch_get_pb_splits(memdb):
    save_run("Mario Circuit", [(0, 1.0, 2.0, 0.9)], total_time="1:23.456",
             lap_splits={1: "0:41.000", 2: "1:23.456"})

    emitted = []

    class FakeIpc:
        def emit(self, line):
            emitted.append(json.loads(line))

    from mkw_tracker.main import _handle_ipc_command
    _handle_ipc_command(
        {"type": "get_pb_splits", "course": "Mario Circuit"}, FakeIpc(),
        detector=MagicMock(), settings=MagicMock(),
        minimap=MagicMock(), lifecycle=MagicMock(),
        show_debug=[False], cap=None,
        current_frame=[None], setup_mode=[False],
    )
    assert len(emitted) == 1
    evt = emitted[0]
    assert evt["type"] == "pb_splits"
    assert evt["course"] == "Mario Circuit"
    # JSON object keys are strings.
    assert evt["splits"] == {"1": 41000, "2": 83456}


def test_maybe_update_pb_returns_bool(memdb):
    from mkw_tracker.database.replay_repo import _maybe_update_pb
    conn = get_connection()
    def _ins(ms):
        return conn.execute(
            "INSERT INTO replays(player,source,course,total_time,total_time_ms,is_pb) "
            "VALUES('me','local','X','t',?,0)", (ms,)).lastrowid
    assert _maybe_update_pb("X", _ins(60000), 60000, conn) is True    # no prior PB -> new PB
    assert _maybe_update_pb("X", _ins(120000), 120000, conn) is False # slower -> not PB
    assert _maybe_update_pb("X", _ins(59000), 59000, conn) is True    # faster -> new PB


def test_recorder_save_forwards_lap_splits(memdb, monkeypatch):
    import mkw_tracker.minimap.recorder as rec_mod
    captured = {}

    def fake_save_run(**kwargs):
        captured.update(kwargs)
        return 1

    monkeypatch.setattr(rec_mod, "save_run", fake_save_run)

    rec = rec_mod.MinimapRecorder.__new__(rec_mod.MinimapRecorder)
    rec._points = [(0, 1.0, 2.0, 0.9)]
    rec._recording = False
    rec._pause_start = None

    rec.save("Mario Circuit", total_time="1:23.456",
             character="Mario", kart="Pipe Frame",
             lap_splits={1: "0:41.000"})
    assert captured.get("lap_splits") == {1: "0:41.000"}

"""Tests for per-lap split persistence and the get_pb_splits IPC."""
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

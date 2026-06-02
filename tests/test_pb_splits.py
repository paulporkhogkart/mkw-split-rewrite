"""Tests for per-lap split persistence and the get_pb_splits IPC."""
from mkw_tracker.database.connection import get_connection
from mkw_tracker.database.migrations import apply_migrations


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

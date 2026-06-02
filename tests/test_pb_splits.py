"""Tests for per-lap split persistence and the get_pb_splits IPC."""
import json
from unittest.mock import MagicMock

from mkw_tracker.database.connection import get_connection
from mkw_tracker.database.replay_repo import save_run, get_pb_splits


def test_replay_splits_table_exists(memdb):
    row = get_connection().execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='replay_splits'"
    ).fetchone()
    assert row is not None

from __future__ import annotations

import sqlite3

import pytest

from hotline.db import Db


def test_init_idempotent(tmp_path):
    db = Db(tmp_path / "h.db")
    db.init()
    db.init()  # second run must not raise
    db.close()


def test_call_roundtrip(tmp_path):
    db = Db(tmp_path / "h.db")
    db.init()
    db.create_call("c1", "twitch:pork_fan", 60)
    db.finish_call("c1", "completed", 58, "recordings/c1")
    row = db._conn.execute(
        "SELECT caller_label, outcome, seconds_used FROM calls WHERE call_id='c1'"
    ).fetchone()
    assert tuple(row) == ("twitch:pork_fan", "completed", 58)
    db.add_strike("c1", 12000, 4000, "dump")
    n = db._conn.execute("SELECT COUNT(*) FROM strikes").fetchone()[0]
    assert n == 1
    db.close()


def test_settings(tmp_path):
    db = Db(tmp_path / "h.db")
    db.init()
    assert db.get_setting("delay_n", "4") == "4"
    db.set_setting("delay_n", "2")
    assert db.get_setting("delay_n", "4") == "2"
    db.close()


def test_strikes_require_existing_call(tmp_path):
    db = Db(tmp_path / "h.db")
    db.init()
    with pytest.raises(sqlite3.IntegrityError):
        db.add_strike("nope", 0, 100, "dump")
    db.close()

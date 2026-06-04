# tests/test_server_db.py
"""Tests for the canonical server schema."""
import importlib


def test_server_package_imports():
    mod = importlib.import_module("server")
    assert mod is not None


import sqlite3
from server.db import connect, init_schema

EXPECTED_TABLES = {
    "seasons", "players", "season_rosters", "courses",
    "runs", "run_laps", "run_points", "world_records",
}


def _fresh():
    conn = connect(":memory:")
    init_schema(conn)
    return conn


def test_all_tables_created():
    conn = _fresh()
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert EXPECTED_TABLES <= names


def test_runs_status_check_rejects_bad_value():
    conn = _fresh()
    conn.execute("INSERT INTO seasons(name) VALUES('Season 0')")
    conn.execute("INSERT INTO players(display_name) VALUES('P')")
    conn.execute("INSERT INTO courses(slug, display_name) VALUES('x','X')")
    try:
        conn.execute(
            "INSERT INTO runs(season_id, player_id, course_id, cc, status, provenance) "
            "VALUES(1,1,1,150,'bogus','live')")
        assert False, "CHECK on status should have rejected 'bogus'"
    except sqlite3.IntegrityError:
        pass


def test_foreign_keys_enabled():
    conn = _fresh()
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_init_schema_is_idempotent():
    conn = _fresh()
    init_schema(conn)  # second call must not raise
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert EXPECTED_TABLES <= names

# tests/test_server_queries.py
"""Tests for is_pb maintenance and leaderboard derivation."""
from server.db import connect, init_schema
from server.courses import seed_courses
from server.queries import recompute_is_pb, current_pb, course_leaderboard


def _build():
    conn = connect(":memory:")
    init_schema(conn)
    seed_courses(conn)
    conn.execute("INSERT INTO seasons(id, name, is_active) VALUES(1,'Season 0',0)")
    conn.execute("INSERT INTO players(id, display_name) VALUES(1,'Paul'),(2,'Luke')")
    cid = conn.execute("SELECT id FROM courses WHERE slug='mario_circuit'").fetchone()["id"]
    rows = [
        (1, 1, cid, 150, "finished", "live", 110000, "2025-01-01"),
        (1, 1, cid, 150, "finished", "live", 108000, "2025-02-01"),
        (1, 2, cid, 150, "finished", "live", 112000, "2025-01-15"),
        (1, 1, cid, 150, "reset",    "live", None,   "2025-02-02"),
    ]
    conn.executemany(
        "INSERT INTO runs(season_id, player_id, course_id, cc, status, provenance, "
        "total_time_ms, ended_at) VALUES (?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    return conn, cid


def test_recompute_is_pb_flags_only_the_fastest_per_player():
    conn, cid = _build()
    recompute_is_pb(conn, 1)
    flagged = conn.execute(
        "SELECT player_id, total_time_ms FROM runs WHERE is_pb=1 ORDER BY player_id"
    ).fetchall()
    assert [(r["player_id"], r["total_time_ms"]) for r in flagged] == [(1, 108000), (2, 112000)]


def test_current_pb_returns_the_flagged_row():
    conn, cid = _build()
    recompute_is_pb(conn, 1)
    pb = current_pb(conn, 1, 1, cid, 150)
    assert pb["total_time_ms"] == 108000


def test_course_leaderboard_is_ordered_and_named():
    conn, cid = _build()
    recompute_is_pb(conn, 1)
    lb = course_leaderboard(conn, 1, cid, 150)
    assert [(r["display_name"], r["total_time_ms"]) for r in lb] == [
        ("Paul", 108000), ("Luke", 112000)]

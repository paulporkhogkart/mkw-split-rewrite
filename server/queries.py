"""Derived-data layer: is_pb maintenance + leaderboard reads."""
import sqlite3


def recompute_is_pb(conn: sqlite3.Connection, season_id: int) -> None:
    """Set is_pb=1 on each (player, course, cc)'s fastest finished run, 0 elsewhere."""
    conn.execute("UPDATE runs SET is_pb=0 WHERE season_id=?", (season_id,))
    conn.execute(
        """
        UPDATE runs SET is_pb=1 WHERE id IN (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY player_id, course_id, cc
                    ORDER BY total_time_ms ASC, ended_at ASC
                ) AS rn
                FROM runs
                WHERE season_id=? AND status='finished'
            ) WHERE rn=1
        )
        """,
        (season_id,),
    )
    conn.commit()


def current_pb(conn, season_id, player_id, course_id, cc):
    """The current PB row for a (season, player, course, cc), or None."""
    return conn.execute(
        "SELECT * FROM runs WHERE season_id=? AND player_id=? AND course_id=? "
        "AND cc=? AND is_pb=1",
        (season_id, player_id, course_id, cc),
    ).fetchone()


def course_leaderboard(conn, season_id, course_id, cc):
    """Each player's PB on a course, fastest first, with display_name joined."""
    return conn.execute(
        "SELECT r.*, p.display_name FROM runs r "
        "JOIN players p ON p.id = r.player_id "
        "WHERE r.season_id=? AND r.course_id=? AND r.cc=? AND r.is_pb=1 "
        "ORDER BY r.total_time_ms ASC",
        (season_id, course_id, cc),
    ).fetchall()

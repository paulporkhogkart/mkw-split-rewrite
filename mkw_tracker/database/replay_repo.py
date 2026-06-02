"""Replay storage - history, PB, and friends' PBs."""
import json
from typing import Optional
from .connection import get_connection
from .config_repo import get_config


def _to_ms(ts: str) -> Optional[int]:
    try:
        mins, rest = ts.split(":")
        secs, millis = rest.split(".")
        return int(mins) * 60_000 + int(secs) * 1_000 + int(millis)
    except Exception:
        return None


def save_run(
    course: str,
    points: list,
    total_time: Optional[str] = None,
    character: Optional[str] = None,
    costume: Optional[str] = None,
    kart: Optional[str] = None,
    player: str = "me",
    source: str = "local",
) -> int:
    """
    Insert a new replay row and its points.  Returns the new replay id.

    For player='me', also updates the PB if total_time beats the stored PB,
    then prunes old history runs beyond the configured limit.
    """
    conn = get_connection()
    total_time_ms = _to_ms(total_time) if total_time else None

    cur = conn.execute(
        """INSERT INTO replays(player, source, course, character, costume, kart,
                               total_time, total_time_ms, is_pb)
           VALUES(?,?,?,?,?,?,?,?,0)""",
        (player, source, course, character, costume, kart, total_time, total_time_ms),
    )
    replay_id = cur.lastrowid

    if points:
        conn.executemany(
            "INSERT INTO replay_points(replay_id, t_ms, cx, cy, score) VALUES(?,?,?,?,?)",
            [(replay_id, t, cx, cy, sc) for t, cx, cy, sc in points],
        )

    conn.commit()

    if player == "me" and total_time_ms is not None:
        _maybe_update_pb(course, replay_id, total_time_ms, conn)
        _prune_history(course, conn)

    return replay_id


def _maybe_update_pb(course: str, new_id: int, new_ms: int, conn):
    """Mark new_id as PB if it beats the existing PB for this course."""
    existing = conn.execute(
        "SELECT id, total_time_ms FROM replays WHERE player='me' AND course=? AND is_pb=1",
        (course,),
    ).fetchone()

    if existing is None or new_ms < existing["total_time_ms"]:
        # Clear old PB flag
        conn.execute(
            "UPDATE replays SET is_pb=0 WHERE player='me' AND course=? AND is_pb=1",
            (course,),
        )
        conn.execute("UPDATE replays SET is_pb=1 WHERE id=?", (new_id,))
        conn.commit()


def _prune_history(course: str, conn):
    """Delete oldest 'me' non-PB runs beyond replay_history_limit."""
    limit = get_config("replay_history_limit", 100)
    conn.execute(
        """DELETE FROM replays WHERE id IN (
               SELECT id FROM replays
               WHERE player='me' AND is_pb=0 AND course=?
               ORDER BY recorded_at DESC
               LIMIT -1 OFFSET ?
           )""",
        (course, limit),
    )
    conn.commit()


def get_pb_splits(course: str, player: str = "me"):
    """Return {lap: split_ms} for the course PB, or None. (Implemented in a later task.)"""
    return None


def get_pb(course: str, player: str = "me") -> Optional[dict]:
    """Return the PB replay (metadata + points) or None."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM replays WHERE player=? AND course=? AND is_pb=1",
        (player, course),
    ).fetchone()
    if row is None:
        return None
    return _load_replay(dict(row), conn)


def get_history(course: str, limit: int = 100) -> list:
    """Return up to *limit* history runs for 'me' (newest first)."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT * FROM replays WHERE player='me' AND course=? AND is_pb=0
           ORDER BY recorded_at DESC LIMIT ?""",
        (course, limit),
    ).fetchall()
    return [_load_replay(dict(r), conn) for r in rows]


def get_friends_pbs(course: str) -> list:
    """Return all friend PBs (source='server', is_pb=1) for a course."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM replays WHERE source='server' AND course=? AND is_pb=1",
        (course,),
    ).fetchall()
    return [_load_replay(dict(r), conn) for r in rows]


def _load_replay(meta: dict, conn) -> dict:
    """Attach points list to a replay metadata dict."""
    pts = conn.execute(
        "SELECT t_ms, cx, cy, score FROM replay_points WHERE replay_id=? ORDER BY t_ms",
        (meta["id"],),
    ).fetchall()
    meta["points"] = [(r["t_ms"], r["cx"], r["cy"], r["score"]) for r in pts]
    return meta


def export_mkwreplay(replay_id: int) -> Optional[dict]:
    """
    Export a replay as a .mkwreplay dict ready for JSON serialisation or
    server upload.
    """
    conn = get_connection()
    row = conn.execute("SELECT * FROM replays WHERE id=?", (replay_id,)).fetchone()
    if row is None:
        return None
    meta = dict(row)
    pts = conn.execute(
        "SELECT t_ms, cx, cy, score FROM replay_points WHERE replay_id=? ORDER BY t_ms",
        (replay_id,),
    ).fetchall()
    return {
        "version": 1,
        "course": meta["course"],
        "player": meta["player"],
        "character": meta["character"],
        "costume": meta["costume"],
        "kart": meta["kart"],
        "total_time": meta["total_time"],
        "recorded_at": meta["recorded_at"],
        "points": [[r["t_ms"], r["cx"], r["cy"], r["score"]] for r in pts],
    }


def save_friend_pb(
    course: str,
    player: str,
    total_time: str,
    points: list,
    character: Optional[str] = None,
    costume: Optional[str] = None,
    kart: Optional[str] = None,
):
    """Upsert a friend's PB (replaces existing if present)."""
    conn = get_connection()
    # Delete existing friend PB for this player+course
    conn.execute(
        "DELETE FROM replays WHERE player=? AND course=? AND source='server' AND is_pb=1",
        (player, course),
    )
    total_time_ms = _to_ms(total_time)
    cur = conn.execute(
        """INSERT INTO replays(player, source, course, character, costume, kart,
                               total_time, total_time_ms, is_pb)
           VALUES(?,?,?,?,?,?,?,?,1)""",
        (player, "server", course, character, costume, kart, total_time, total_time_ms),
    )
    replay_id = cur.lastrowid
    if points:
        conn.executemany(
            "INSERT INTO replay_points(replay_id, t_ms, cx, cy, score) VALUES(?,?,?,?,?)",
            [(replay_id, t, cx, cy, sc) for t, cx, cy, sc in points],
        )
    conn.commit()


def replay_paths(conn, course: str) -> list:
    """Return replay trail paths for a given course.

    Returns a list of dicts, one per replay run that has recorded points::

        [{"id": str, "label": str, "is_pb": bool, "total_time": str|None,
          "points": [[t_ms, cx, cy], ...]}, ...]

    Each point carries its capture timestamp (``t_ms``) first so the UI can
    animate a *moving dot* interpolated against race-elapsed time (mirroring
    ``MinimapPlayer._interpolate``), rather than drawing a static polyline.
    ``cx``/``cy`` are full-frame 1080p pixel coordinates (the same space as
    ``MinimapState.cx`` / ``MinimapState.cy``).  ``is_pb`` lets the UI accent
    personal-best dots; ``total_time is None`` marks an abandoned run (drawn as
    an X at its final point).  Runs with no recorded points are excluded; all
    replay types (local, server, PB, history) are included.
    """
    rows = conn.execute(
        """SELECT id, player, is_pb, total_time FROM replays
           WHERE course = ?
           ORDER BY recorded_at DESC""",
        (course,),
    ).fetchall()

    result = []
    for row in rows:
        pts = conn.execute(
            """SELECT cx, cy, t_ms FROM replay_points
               WHERE replay_id = ? ORDER BY t_ms""",
            (row["id"],),
        ).fetchall()
        if not pts:
            continue
        player = row["player"] if row["player"] is not None else ""
        result.append({
            "id":         str(row["id"]),
            "label":      player or str(row["id"]),
            "is_pb":      bool(row["is_pb"]),
            "total_time": row["total_time"],
            "points":     [[p["t_ms"], p["cx"], p["cy"]] for p in pts],
        })
    return result


def get_minimap_seed(course: str) -> Optional[dict]:
    """Return minimap seed for a course, or None."""
    conn = get_connection()
    row = conn.execute(
        "SELECT cx, cy, radius, conf FROM minimap_seeds WHERE course=?", (course,)
    ).fetchone()
    return dict(row) if row else None


def set_minimap_seed(course: str, cx: int, cy: int, radius: int = 0,
                     conf: Optional[float] = None):
    """Upsert a minimap seed."""
    conn = get_connection()
    conn.execute(
        """INSERT INTO minimap_seeds(course, cx, cy, radius, conf, updated_at)
           VALUES(?,?,?,?,?,datetime('now'))
           ON CONFLICT(course) DO UPDATE SET
               cx=excluded.cx, cy=excluded.cy, radius=excluded.radius,
               conf=excluded.conf, updated_at=excluded.updated_at""",
        (course, cx, cy, radius, conf),
    )
    conn.commit()


def get_minimap_roi(course: str) -> Optional[dict]:
    """Return custom minimap ROI for a course, or None."""
    conn = get_connection()
    row = conn.execute(
        "SELECT x, y, w, h FROM minimap_rois WHERE course=?", (course,)
    ).fetchone()
    return dict(row) if row else None


def set_minimap_roi(course: str, x: int, y: int, w: int, h: int):
    """Upsert a minimap ROI."""
    conn = get_connection()
    conn.execute(
        """INSERT INTO minimap_rois(course, x, y, w, h, updated_at)
           VALUES(?,?,?,?,?,datetime('now'))
           ON CONFLICT(course) DO UPDATE SET
               x=excluded.x, y=excluded.y, w=excluded.w, h=excluded.h,
               updated_at=excluded.updated_at""",
        (course, x, y, w, h),
    )
    conn.commit()


def get_minimap_threshold(course: str, character: str,
                          costume: Optional[str] = None) -> Optional[float]:
    """Return calibrated minimap threshold, or None."""
    conn = get_connection()
    row = conn.execute(
        "SELECT threshold FROM minimap_thresholds WHERE course=? AND character=? AND costume=?",
        (course, character, costume or ""),
    ).fetchone()
    return row["threshold"] if row else None


def set_minimap_threshold(course: str, character: str, costume: Optional[str],
                          threshold: float):
    """Upsert a minimap threshold."""
    conn = get_connection()
    conn.execute(
        """INSERT INTO minimap_thresholds(course, character, costume, threshold, updated_at)
           VALUES(?,?,?,?,datetime('now'))
           ON CONFLICT(course, character, costume) DO UPDATE SET
               threshold=excluded.threshold, updated_at=excluded.updated_at""",
        (course, character, costume or "", threshold),
    )
    conn.commit()



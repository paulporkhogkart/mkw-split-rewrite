"""Minimap detection config (seeds / ROIs / thresholds) + a time helper.

Race data (replays / PB / history / friends' trails) now lives on the server
(Phase 2). The engine's local race-data tier was removed; only the minimap
detection-config rows and the `_to_ms` helper remain here.
"""
import re
from typing import Optional
from .connection import get_connection


def _to_ms(ts: str) -> Optional[int]:
    try:
        mins, rest = ts.split(":")
        secs, millis = rest.split(".")
        return int(mins) * 60_000 + int(secs) * 1_000 + int(millis)
    except Exception:
        return None


def _norm_course(s: str) -> str:
    """Lowercase + strip everything non-alphanumeric, so the two course-name
    writers can never miss each other again: detection derives names from
    template FILENAMES ("Sky High Sundae", "Mario Bros Circuit") while the
    _SEED_V2 migration and the WR service use canonical punctuation
    ("Sky-High Sundae", "Mario Bros. Circuit", curly apostrophes). The SHS
    mismatch silently disabled live minimap tracking on that course for
    everyone from the day trails shipped until 2026-07-20."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _norm_lookup(conn, table: str, course: str) -> Optional[str]:
    """The stored course key in `table` matching `course` up to normalization,
    or None. Exact matches never get here (callers try exact first), so this
    only scans the ~30 course rows on a miss."""
    for (stored,) in conn.execute(f"SELECT course FROM {table}"):  # noqa: S608 - fixed table names
        if _norm_course(stored) == _norm_course(course):
            return stored
    return None


def get_minimap_seed(course: str) -> Optional[dict]:
    """Return minimap seed for a course, or None. Exact key first; on a miss,
    a normalized (case/punctuation-insensitive) match — see _norm_course."""
    conn = get_connection()
    row = conn.execute(
        "SELECT cx, cy, radius, conf FROM minimap_seeds WHERE course=?", (course,)
    ).fetchone()
    if row is None:
        stored = _norm_lookup(conn, "minimap_seeds", course)
        if stored is not None:
            row = conn.execute(
                "SELECT cx, cy, radius, conf FROM minimap_seeds WHERE course=?", (stored,)
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
    """Return custom minimap ROI for a course, or None. Same exact-then-
    normalized lookup as get_minimap_seed."""
    conn = get_connection()
    row = conn.execute(
        "SELECT x, y, w, h FROM minimap_rois WHERE course=?", (course,)
    ).fetchone()
    if row is None:
        stored = _norm_lookup(conn, "minimap_rois", course)
        if stored is not None:
            row = conn.execute(
                "SELECT x, y, w, h FROM minimap_rois WHERE course=?", (stored,)
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
    """Return calibrated minimap threshold, or None. The course key gets the
    exact-then-normalized treatment; character/costume stay exact (they have a
    single writer, so their spellings cannot diverge)."""
    conn = get_connection()
    row = conn.execute(
        "SELECT threshold FROM minimap_thresholds WHERE course=? AND character=? AND costume=?",
        (course, character, costume or ""),
    ).fetchone()
    if row is None:
        stored = _norm_lookup(conn, "minimap_thresholds", course)
        if stored is not None:
            row = conn.execute(
                "SELECT threshold FROM minimap_thresholds WHERE course=? AND character=? AND costume=?",
                (stored, character, costume or ""),
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

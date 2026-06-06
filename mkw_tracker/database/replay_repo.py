"""Minimap detection config (seeds / ROIs / thresholds) + a time helper.

Race data (replays / PB / history / friends' trails) now lives on the server
(Phase 2). The engine's local race-data tier was removed; only the minimap
detection-config rows and the `_to_ms` helper remain here.
"""
from typing import Optional
from .connection import get_connection


def _to_ms(ts: str) -> Optional[int]:
    try:
        mins, rest = ts.split(":")
        secs, millis = rest.split(".")
        return int(mins) * 60_000 + int(secs) * 1_000 + int(millis)
    except Exception:
        return None


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

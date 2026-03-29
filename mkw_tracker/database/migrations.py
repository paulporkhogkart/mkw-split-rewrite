"""Schema versioning and migrations."""
import sqlite3
from .connection import get_connection

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS config (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS minimap_seeds (
    course     TEXT PRIMARY KEY,
    cx         INTEGER NOT NULL,
    cy         INTEGER NOT NULL,
    radius     INTEGER NOT NULL DEFAULT 0,
    conf       REAL,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS minimap_rois (
    course     TEXT PRIMARY KEY,
    x INTEGER NOT NULL,
    y INTEGER NOT NULL,
    w INTEGER NOT NULL,
    h INTEGER NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS minimap_thresholds (
    course    TEXT NOT NULL,
    character TEXT NOT NULL,
    costume   TEXT NOT NULL DEFAULT '',
    threshold REAL NOT NULL,
    updated_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (course, character, costume)
);

CREATE TABLE IF NOT EXISTS replays (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    player         TEXT    NOT NULL DEFAULT 'me',
    source         TEXT    NOT NULL DEFAULT 'local',
    course         TEXT    NOT NULL,
    character      TEXT,
    costume        TEXT,
    kart           TEXT,
    total_time     TEXT,
    total_time_ms  INTEGER,
    is_pb          INTEGER NOT NULL DEFAULT 0,
    recorded_at    TEXT    DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_replays_course_player ON replays(course, player);

CREATE TABLE IF NOT EXISTS replay_points (
    replay_id INTEGER NOT NULL REFERENCES replays(id) ON DELETE CASCADE,
    t_ms      INTEGER NOT NULL,
    cx        REAL    NOT NULL,
    cy        REAL    NOT NULL,
    score     REAL    NOT NULL DEFAULT 1.0
);

CREATE INDEX IF NOT EXISTS idx_replay_points_id ON replay_points(replay_id);
"""


def apply_migrations(db_path: str = "mkw_tracker.db"):
    """Apply pending schema migrations. Safe to call on every startup."""
    conn = get_connection(db_path)
    cur = conn.cursor()

    # Check current schema version
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'")
    version_exists = cur.fetchone() is not None

    if not version_exists:
        # Fresh DB — apply full v1 schema
        conn.executescript(_SCHEMA_V1)
        conn.execute("INSERT INTO schema_version VALUES (1)")
        conn.commit()
        print("[DB] Schema v1 applied")
    else:
        cur.execute("SELECT version FROM schema_version")
        row = cur.fetchone()
        current = row[0] if row else 0
        if current < 1:
            conn.executescript(_SCHEMA_V1)
            conn.execute("UPDATE schema_version SET version=1")
            conn.commit()
            print("[DB] Schema migrated to v1")

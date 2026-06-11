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
"""
# Note: the replays / replay_points / replay_splits race-data tables were removed in
# Phase 2 (race data now lives on the server). Fresh DBs no longer create them;
# existing DBs keep the now-unused tables (no destructive DROP migration).


_SEED_V2 = """
INSERT OR IGNORE INTO minimap_seeds (course, cx, cy, radius) VALUES
    ('Acorn Heights',       1708, 613,  20),
    ('Airship Fortress',    1777, 874,  20),
    ('Boo Cinema',          1546, 802,  20),
    ('Bowsers Castle',      1713, 745,  20),
    ('Cheep Cheep Falls',   1691, 835,  20),
    ('Choco Mountain',      1662, 692,  20),
    ('Crown City',          1688, 981,  20),
    ('Dandelion Depths',    1585, 848,  20),
    ('Desert Hills',        1615, 778,  20),
    ('Dino Dino Jungle',    1843, 795,  20),
    ('Dk Pass',             1642, 872,  20),
    ('Dk Spaceport',        1860, 837,  20),
    ('Dry Bones Burnout',   1774, 777,  20),
    ('Faraway Oasis',       1737, 893,  20),
    ('Great Block Ruins',   1715, 699,  20),
    ('Koopa Troopa Beach',  1799, 792,  20),
    ('Mario Bros Circuit',  1832, 749,  20),
    ('Mario Circuit',       1636, 876,  20),
    ('Moo Moo Meadows',     1836, 759,  20),
    ('Peach Beach',         1710, 737,  20),
    ('Peach Stadium',       1701, 800,  20),
    ('Rainbow Road',        1759, 507,  20),
    ('Salty Salty Speedway',1678, 826,  20),
    ('Shy Guy Bazaar',      1623, 875,  20),
    ('Sky-High Sundae',     1753, 767,  20),
    ('Starview Peak',       1643, 709,  20),
    ('Toads Factory',       1629, 777,  20),
    ('Wario Stadium',       1853, 869,  20),
    ('Warios Galleon',      1730, 889,  20),
    ('Whistlestop Summit',  1696, 880,  20);

INSERT OR IGNORE INTO minimap_rois (course, x, y, w, h) VALUES
    ('Acorn Heights',       1556, 507, 326, 502),
    ('Airship Fortress',    1587, 567, 307, 426),
    ('Boo Cinema',          1470, 622, 438, 367),
    ('Bowsers Castle',      1546, 475, 320, 522),
    ('Cheep Cheep Falls',   1546, 549, 347, 449),
    ('Choco Mountain',      1525, 574, 352, 431),
    ('Crown City',          1470, 606, 435, 423),
    ('Dandelion Depths',    1499, 643, 408, 321),
    ('Desert Hills',        1562, 591, 314, 416),
    ('Dino Dino Jungle',    1497, 628, 410, 362),
    ('Dk Pass',             1563, 503, 323, 499),
    ('Dk Spaceport',        1451, 620, 452, 366),
    ('Dry Bones Burnout',   1559, 505, 288, 500),
    ('Faraway Oasis',       1495, 567, 407, 429),
    ('Great Block Ruins',   1589, 474, 226, 527),
    ('Koopa Troopa Beach',  1548, 605, 342, 389),
    ('Mario Bros Circuit',  1526, 593, 362, 418),
    ('Mario Circuit',       1466, 619, 431, 355),
    ('Moo Moo Meadows',     1503, 591, 386, 428),
    ('Peach Beach',         1537, 502, 330, 499),
    ('Peach Stadium',       1539, 565, 345, 441),
    ('Rainbow Road',        1539, 319, 345, 707),
    ('Salty Salty Speedway',1538, 580, 360, 410),
    ('Shy Guy Bazaar',      1557, 546, 320, 456),
    ('Sky-High Sundae',     1610, 467, 220, 520),
    ('Starview Peak',       1518, 599, 386, 398),
    ('Toads Factory',       1572, 604, 322, 398),
    ('Wario Stadium',       1556, 672, 342, 358),
    ('Warios Galleon',      1506, 576, 399, 428),
    ('Whistlestop Summit',  1530, 553, 351, 455);
"""


# v4 originally created replay_splits; removed in Phase 2. Kept as a no-op so the
# schema_version chain still advances to 4 on older DBs.
_SCHEMA_V4 = "-- replay_splits removed in Phase 2 (race data moved to server)"


# v5: minimap identity scores moved from raw-CCORR to badge-NCC scale; stored
# per-combo confident thresholds are meaningless on the new scale and would
# lock races into ring_only. Auto-calibration repopulates them per race.
_SCHEMA_V5 = "DELETE FROM minimap_thresholds;"


def apply_migrations(db_path: str | None = None):
    """Apply pending schema migrations. Safe to call on every startup."""
    conn = get_connection(db_path)
    cur = conn.cursor()

    # Check current schema version
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'")
    version_exists = cur.fetchone() is not None

    if not version_exists:
        # Fresh DB - apply full v1 schema
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
            current = 1

    cur.execute("SELECT version FROM schema_version")
    row = cur.fetchone()
    current = row[0] if row else 0
    if current < 2:
        conn.executescript(_SEED_V2)
        conn.execute("UPDATE schema_version SET version=2")
        conn.commit()
        print("[DB] Seed data v2 applied (minimap seeds + ROIs)")

    cur.execute("SELECT version FROM schema_version")
    row = cur.fetchone()
    current = row[0] if row else 0
    if current < 3:
        from .tell_repo import migrate_tells_to_tree
        n = migrate_tells_to_tree()
        conn.execute("UPDATE schema_version SET version=3")
        conn.commit()
        print(f"[DB] Migrated {n} tell override(s) to boolean-tree format (v3)")

    cur.execute("SELECT version FROM schema_version")
    row = cur.fetchone()
    current = row[0] if row else 0
    if current < 4:
        conn.executescript(_SCHEMA_V4)
        conn.execute("UPDATE schema_version SET version=4")
        conn.commit()
        print("[DB] Schema version bumped to v4")

    cur.execute("SELECT version FROM schema_version")
    row = cur.fetchone()
    current = row[0] if row else 0
    if current < 5:
        conn.executescript(_SCHEMA_V5)
        conn.execute("UPDATE schema_version SET version=5")
        conn.commit()
        print("[DB] v5: cleared minimap_thresholds (badge score rescale)")

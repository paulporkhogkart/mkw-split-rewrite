# Database Schema

Single file: `mkw_tracker.db` (SQLite, WAL mode).

## `schema_version`
```sql
CREATE TABLE schema_version (version INTEGER NOT NULL);
```
Seeded with `(1)` on first run. Used by `apply_migrations()` to determine which migrations to apply.

## `config`
Every tunable constant. Value is JSON-encoded (number, string, list, etc.).
```sql
CREATE TABLE config (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);
```
Populated on first run from `config/defaults.py`. Tauri writes here; Python hot-reloads via `settings.reload(keys)`.

## `minimap_seeds`
```sql
CREATE TABLE minimap_seeds (
    course     TEXT PRIMARY KEY,
    cx         INTEGER NOT NULL,
    cy         INTEGER NOT NULL,
    radius     INTEGER NOT NULL DEFAULT 0,
    conf       REAL,
    updated_at TEXT DEFAULT (datetime('now'))
);
```
Per-course minimap starting positions (full 1080p pixels). Migrated from `minimap_seeds.json`.

## `minimap_rois`
```sql
CREATE TABLE minimap_rois (
    course     TEXT PRIMARY KEY,
    x INTEGER NOT NULL, y INTEGER NOT NULL,
    w INTEGER NOT NULL, h INTEGER NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);
```
Per-course custom minimap scan ROI. Migrated from `minimap_rois.json`.

## `minimap_thresholds`
```sql
CREATE TABLE minimap_thresholds (
    course    TEXT NOT NULL,
    character TEXT NOT NULL,
    costume   TEXT NOT NULL DEFAULT '',
    threshold REAL NOT NULL,
    updated_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (course, character, costume)
);
```
Auto-calibrated NCC confidence thresholds. Migrated from `minimap_thresholds.json`.

## `replays`
```sql
CREATE TABLE replays (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    player         TEXT    NOT NULL DEFAULT 'me',
    source         TEXT    NOT NULL DEFAULT 'local',  -- 'local' | 'server'
    course         TEXT    NOT NULL,
    character      TEXT,
    costume        TEXT,
    kart           TEXT,
    total_time     TEXT,           -- NULL = aborted/reset
    total_time_ms  INTEGER,        -- for ORDER BY comparison
    is_pb          INTEGER NOT NULL DEFAULT 0,
    recorded_at    TEXT    DEFAULT (datetime('now'))
);
CREATE INDEX idx_replays_course_player ON replays(course, player);
```

### History rollover
Controlled by config key `replay_history_limit` (default 100). Only touches `player='me' AND is_pb=0` rows — PBs and friends' PBs are never pruned:
```sql
DELETE FROM replays WHERE id IN (
    SELECT id FROM replays
    WHERE player = 'me' AND is_pb = 0 AND course = ?
    ORDER BY recorded_at DESC
    LIMIT -1 OFFSET <history_limit>
)
```

### PB update
On new PB: clear `is_pb=1` for previous PB row, mark new row `is_pb=1`. Old PB becomes a normal history run.

### Friends' PBs
Stored as `player='<name>', source='server', is_pb=1`. New PB from server deletes old row for that `player+course` before inserting the new one.

## `replay_points`
```sql
CREATE TABLE replay_points (
    replay_id INTEGER NOT NULL REFERENCES replays(id) ON DELETE CASCADE,
    t_ms      INTEGER NOT NULL,
    cx        REAL    NOT NULL,
    cy        REAL    NOT NULL,
    score     REAL    NOT NULL DEFAULT 1.0
);
CREATE INDEX idx_replay_points_id ON replay_points(replay_id);
```
Time-series minimap positions. Cascade-deleted when parent replay is pruned.

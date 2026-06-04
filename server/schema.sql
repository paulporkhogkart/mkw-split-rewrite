-- Canonical server schema (sub-project A). SQLite, WAL. Re-runnable (IF NOT EXISTS).
CREATE TABLE IF NOT EXISTS seasons (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    started_at  TEXT,
    ended_at    TEXT,
    is_active   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS players (
    id            INTEGER PRIMARY KEY,
    display_name  TEXT NOT NULL UNIQUE,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS season_rosters (
    season_id  INTEGER NOT NULL REFERENCES seasons(id),
    player_id  INTEGER NOT NULL REFERENCES players(id),
    PRIMARY KEY (season_id, player_id)
);

CREATE TABLE IF NOT EXISTS courses (
    id            INTEGER PRIMARY KEY,
    slug          TEXT NOT NULL UNIQUE,
    display_name  TEXT NOT NULL,
    default_laps  INTEGER
);

CREATE TABLE IF NOT EXISTS runs (
    id             INTEGER PRIMARY KEY,
    season_id      INTEGER NOT NULL REFERENCES seasons(id),
    player_id      INTEGER NOT NULL REFERENCES players(id),
    course_id      INTEGER NOT NULL REFERENCES courses(id),
    cc             INTEGER NOT NULL,
    status         TEXT NOT NULL CHECK (status IN ('reset','dnf','finished')),
    provenance     TEXT NOT NULL CHECK (provenance IN ('live','legacy_import','carryover')),
    started_at     TEXT,
    ended_at       TEXT,
    total_time_ms  INTEGER,
    total_time_str TEXT,
    character      TEXT,
    kart           TEXT,
    costume        TEXT,
    is_pb          INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS run_laps (
    run_id        INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    lap_index     INTEGER NOT NULL,
    lap_time_ms   INTEGER NOT NULL,
    lap_time_str  TEXT,
    coins         INTEGER,
    shrooms       INTEGER,
    PRIMARY KEY (run_id, lap_index)
);

CREATE TABLE IF NOT EXISTS run_points (
    run_id   INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    t_ms     INTEGER NOT NULL,
    cx       REAL NOT NULL,
    cy       REAL NOT NULL,
    score    REAL NOT NULL DEFAULT 1.0
);

CREATE TABLE IF NOT EXISTS world_records (
    id           INTEGER PRIMARY KEY,
    course_id    INTEGER NOT NULL REFERENCES courses(id),
    cc           INTEGER NOT NULL,
    holder_name  TEXT,
    record_ms    INTEGER NOT NULL,
    record_str   TEXT NOT NULL,
    achieved_at  TEXT,
    video_url    TEXT,
    character    TEXT,
    vehicle      TEXT,
    provenance   TEXT NOT NULL DEFAULT 'legacy_import',
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_runs_leaderboard ON runs(season_id, course_id, cc, is_pb);
CREATE INDEX IF NOT EXISTS idx_runs_player      ON runs(season_id, player_id, course_id, cc);
CREATE INDEX IF NOT EXISTS idx_run_laps_run     ON run_laps(run_id);
CREATE INDEX IF NOT EXISTS idx_run_points_run   ON run_points(run_id);
CREATE INDEX IF NOT EXISTS idx_wr_course        ON world_records(course_id, cc, achieved_at);

-- Canonical server schema (sub-project A). SQLite, WAL. Re-runnable (IF NOT EXISTS).
CREATE TABLE IF NOT EXISTS seasons (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    started_at  TEXT,
    ended_at    TEXT,
    is_active   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS players (
    id              INTEGER PRIMARY KEY,
    display_name    TEXT NOT NULL UNIQUE,
    auth_token_hash TEXT UNIQUE,
    color           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS season_rosters (
    season_id  INTEGER NOT NULL REFERENCES seasons(id),
    player_id  INTEGER NOT NULL REFERENCES players(id),
    PRIMARY KEY (season_id, player_id)
);

CREATE TABLE IF NOT EXISTS courses (
    id            INTEGER PRIMARY KEY,
    slug          TEXT NOT NULL UNIQUE,
    display_name  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id             INTEGER PRIMARY KEY,
    attempt_id     TEXT UNIQUE,
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
    coins_gained   INTEGER,
    coins_lost     INTEGER,
    mushrooms_used INTEGER,
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

CREATE TABLE IF NOT EXISTS course_models (
    course_id        INTEGER NOT NULL REFERENCES courses(id),
    cc               INTEGER NOT NULL,
    model_json       TEXT NOT NULL,
    lap_length_px    REAL NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('graph','centerline')),
    source_run_count INTEGER NOT NULL,
    version          INTEGER NOT NULL,
    built_at         TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (course_id, cc)
);

CREATE TABLE IF NOT EXISTS player_alignment (
    player_id     INTEGER PRIMARY KEY REFERENCES players(id),
    dx            REAL NOT NULL,
    dy            REAL NOT NULL,
    scale         REAL NOT NULL DEFAULT 1.0,
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    sample_count  INTEGER NOT NULL
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
    is_current   INTEGER NOT NULL DEFAULT 0,
    provenance   TEXT NOT NULL DEFAULT 'legacy_import',
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_runs_leaderboard ON runs(season_id, course_id, cc, is_pb);
CREATE INDEX IF NOT EXISTS idx_runs_player      ON runs(season_id, player_id, course_id, cc);
CREATE INDEX IF NOT EXISTS idx_run_laps_run     ON run_laps(run_id);
CREATE INDEX IF NOT EXISTS idx_run_points_run   ON run_points(run_id);
CREATE INDEX IF NOT EXISTS idx_wr_course        ON world_records(course_id, cc, achieved_at);

CREATE TABLE IF NOT EXISTS screen_intervals (
    id          INTEGER PRIMARY KEY,
    season_id   INTEGER NOT NULL REFERENCES seasons(id),
    player_id   INTEGER NOT NULL REFERENCES players(id),
    screen      TEXT NOT NULL,
    started_ms  INTEGER NOT NULL,
    ended_ms    INTEGER NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(player_id, started_ms)
);
CREATE INDEX IF NOT EXISTS idx_screen_intervals ON screen_intervals(season_id, player_id, screen, started_ms);

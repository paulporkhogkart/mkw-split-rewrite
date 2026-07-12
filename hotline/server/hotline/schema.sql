-- Pork Phone hotline schema (spec §12). Own DB; zero overlap with pi/ tables.
CREATE TABLE IF NOT EXISTS identities (
  twitch_user_id TEXT PRIMARY KEY,
  display_name   TEXT NOT NULL,
  avatar_url     TEXT,
  created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS credits (
  id             INTEGER PRIMARY KEY,
  twitch_user_id TEXT NOT NULL,
  seconds        INTEGER NOT NULL,
  source         TEXT NOT NULL,             -- channel_points | stripe | ...
  status         TEXT NOT NULL DEFAULT 'unspent',  -- unspent|reserved|spent|refunded
  redemption_id  TEXT,
  created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS bans (
  twitch_user_id TEXT PRIMARY KEY,
  reason         TEXT NOT NULL,
  strike_call_id TEXT,
  created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS calls (
  call_id        TEXT PRIMARY KEY,
  caller_label   TEXT NOT NULL,             -- Plan 1: freeform; Plan 2: twitch id
  seconds_bought INTEGER NOT NULL,
  seconds_used   INTEGER,
  outcome        TEXT,                      -- completed|dropped|banned|test
  consent_at     TEXT,
  recording_dir  TEXT,
  started_at     TEXT NOT NULL DEFAULT (datetime('now')),
  ended_at       TEXT
);
CREATE TABLE IF NOT EXISTS strikes (
  id       INTEGER PRIMARY KEY,
  call_id  TEXT NOT NULL REFERENCES calls(call_id),
  at_ms    INTEGER NOT NULL,                -- offset into the call
  span_ms  INTEGER NOT NULL,
  action   TEXT NOT NULL,                   -- dump|ban
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

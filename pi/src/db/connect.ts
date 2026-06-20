import { DatabaseSync } from 'node:sqlite';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { backfillWasPb } from './pb';

export function openDb(path: string): DatabaseSync {
  const db = new DatabaseSync(path);
  db.exec('PRAGMA journal_mode = WAL');
  db.exec('PRAGMA foreign_keys = ON');
  return db;
}

// pi/src/db/connect.ts → repo root is four levels up; A's schema is the single source.
const SCHEMA_PATH = fileURLToPath(new URL('../../../server/schema.sql', import.meta.url));

export function applySchema(db: DatabaseSync): void {
  db.exec(readFileSync(SCHEMA_PATH, 'utf8'));
  // Additive migrations for existing DBs (CREATE TABLE IF NOT EXISTS won't add columns).
  try { db.exec('ALTER TABLE players ADD COLUMN color TEXT'); } catch { /* already present */ }
  try {
    db.exec('ALTER TABLE world_records ADD COLUMN is_current INTEGER NOT NULL DEFAULT 0');
    // One-time seed (only runs the first time the column is added): flag the
    // latest-achieved WR per (course,cc) as current.
    db.exec(`UPDATE world_records SET is_current = 1 WHERE id = (
      SELECT w2.id FROM world_records w2
      WHERE w2.course_id = world_records.course_id AND w2.cc = world_records.cc
      ORDER BY w2.achieved_at DESC, w2.id DESC LIMIT 1)`);
  } catch { /* already present + seeded */ }
  // Additive: per-run "was a PB when set" flag. Backfilled once, on first add.
  try {
    db.exec('ALTER TABLE runs ADD COLUMN was_pb INTEGER NOT NULL DEFAULT 0');
    backfillWasPb(db);
  } catch { /* already present + backfilled */ }
  // Additive: run-level coin/mushroom totals (capture resets; per-lap rows stay for splits).
  try { db.exec('ALTER TABLE runs ADD COLUMN coins_gained INTEGER'); } catch { /* present */ }
  try { db.exec('ALTER TABLE runs ADD COLUMN coins_lost INTEGER'); } catch { /* present */ }
  try { db.exec('ALTER TABLE runs ADD COLUMN mushrooms_used INTEGER'); } catch { /* present */ }
  // Additive: per-point HUD lap (1-based). Null for legacy rows; builder falls back to time.
  try { db.exec('ALTER TABLE run_points ADD COLUMN lap INTEGER'); } catch { /* present */ }
  // Additive: ghost-import source mark (nullable; 'ghost' when ghost-sourced).
  try { db.exec('ALTER TABLE runs ADD COLUMN source TEXT'); } catch { /* present */ }
  // Additive: ghost import audit log (no-op once present).
  db.exec(`CREATE TABLE IF NOT EXISTS ghost_imports (
    id INTEGER PRIMARY KEY, run_id INTEGER REFERENCES runs(id),
    player_id INTEGER NOT NULL REFERENCES players(id),
    course_id INTEGER NOT NULL REFERENCES courses(id),
    cc INTEGER NOT NULL, total_time_ms INTEGER,
    action TEXT NOT NULL CHECK (action IN ('enriched','new')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')))`);
  // Idempotent: at most one current WR per (course,cc). Created here (not in schema.sql)
  // so the column is guaranteed present for both fresh and migrated DBs.
  db.exec('CREATE UNIQUE INDEX IF NOT EXISTS idx_wr_current ON world_records(course_id, cc) WHERE is_current=1');
  // Player recolour (existing DBs): Gub teal -> blue. Idempotent (no row matches once recoloured),
  // and gated on the old value so it never fights a future colour change. server/importer.py
  // PLAYER_COLORS carries the same value, so fresh imports seed blue directly.
  db.exec("UPDATE players SET color = '#38bdf8' WHERE display_name = 'Gub' COLLATE NOCASE AND color = '#2dd4bf'");
}

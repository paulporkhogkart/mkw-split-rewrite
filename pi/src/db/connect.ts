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
  // Idempotent: at most one current WR per (course,cc). Created here (not in schema.sql)
  // so the column is guaranteed present for both fresh and migrated DBs.
  db.exec('CREATE UNIQUE INDEX IF NOT EXISTS idx_wr_current ON world_records(course_id, cc) WHERE is_current=1');
}

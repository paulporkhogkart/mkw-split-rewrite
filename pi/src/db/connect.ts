import { DatabaseSync } from 'node:sqlite';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

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
}

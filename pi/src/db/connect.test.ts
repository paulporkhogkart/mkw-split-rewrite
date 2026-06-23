import { describe, it, expect } from 'vitest';
import { DatabaseSync } from 'node:sqlite';
import { openDb, applySchema } from './connect';

describe('openDb', () => {
  it('opens an in-memory db with foreign keys on', () => {
    const db = openDb(':memory:');
    db.exec('CREATE TABLE t(x INTEGER)');
    db.prepare('INSERT INTO t(x) VALUES (?)').run(7);
    const row = db.prepare('SELECT COUNT(*) c, SUM(x) s FROM t').get() as { c: number; s: number };
    expect(row).toEqual({ c: 1, s: 7 });
    expect((db.prepare('PRAGMA foreign_keys').get() as { foreign_keys: number }).foreign_keys).toBe(1);
  });
});

/** Build a pre-`is_current` world_records table (a fresh applySchema DB already has the
 *  column, so we hand-build the old shape) with several WRs per course. */
function legacyShapeDb(): DatabaseSync {
  const db = new DatabaseSync(':memory:');
  db.exec(`
    CREATE TABLE courses(id INTEGER PRIMARY KEY, slug TEXT, display_name TEXT);
    INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','Rainbow Road'),(2,'mc','Mario Circuit');
    CREATE TABLE world_records(
      id INTEGER PRIMARY KEY, course_id INTEGER, cc INTEGER, holder_name TEXT,
      record_ms INTEGER, record_str TEXT, achieved_at TEXT);
    INSERT INTO world_records(course_id,cc,holder_name,record_ms,record_str,achieved_at) VALUES
      (1,150,'A',100000,'1:40.000','2026-01-01'),
      (1,150,'B', 99000,'1:39.000','2026-02-01'),
      (2,150,'C',120000,'2:00.000','2026-01-15');
  `);
  return db;
}

describe('applySchema is_current migration', () => {
  it('flags exactly the latest-achieved WR per course as current', () => {
    const db = legacyShapeDb();
    applySchema(db);
    const cur = db.prepare(
      'SELECT course_id, record_ms FROM world_records WHERE is_current=1 ORDER BY course_id'
    ).all();
    expect(cur).toEqual([
      { course_id: 1, record_ms: 99000 },   // 2026-02-01 beats 2026-01-01
      { course_id: 2, record_ms: 120000 },
    ]);
  });

  it('the partial unique index rejects a second current row for a course', () => {
    const db = legacyShapeDb();
    applySchema(db);
    expect(() =>
      db.prepare('UPDATE world_records SET is_current=1 WHERE course_id=1 AND record_ms=100000').run()
    ).toThrow();
  });
});

describe('applySchema Gub recolour migration', () => {
  it('recolours an existing teal Gub to blue, idempotently, leaving others alone', () => {
    const db = openDb(':memory:');
    applySchema(db);   // build the players table
    db.exec("INSERT INTO players(id,display_name,color) VALUES (1,'Gub','#2dd4bf'),(2,'Paul','#a78bfa'),(3,'Mystery','#2dd4bf')");
    applySchema(db);   // a later boot -> the recolour runs
    const colors = Object.fromEntries(
      (db.prepare('SELECT display_name, color FROM players').all() as { display_name: string; color: string }[])
        .map((r) => [r.display_name, r.color]));
    expect(colors.Gub).toBe('#38bdf8');      // teal -> blue
    expect(colors.Paul).toBe('#a78bfa');     // untouched
    expect(colors.Mystery).toBe('#2dd4bf');  // gated on the name: a different teal player is left alone
    applySchema(db);                         // idempotent: no row matches now, still blue
    expect((db.prepare("SELECT color FROM players WHERE display_name='Gub'").get() as { color: string }).color).toBe('#38bdf8');
  });
});

describe('applySchema last_seen_at migration', () => {
  it('adds last_seen_at to a pre-existing players table, idempotently', () => {
    const db = new DatabaseSync(':memory:');
    // Legacy players shape (predates last_seen_at): applySchema's CREATE TABLE
    // IF NOT EXISTS is a no-op, so only the additive ALTER can add the column.
    db.exec(`CREATE TABLE players(
      id INTEGER PRIMARY KEY, display_name TEXT NOT NULL UNIQUE,
      auth_token_hash TEXT UNIQUE, color TEXT,
      created_at TEXT NOT NULL DEFAULT (datetime('now')));
      INSERT INTO players(id,display_name) VALUES (1,'Paul');`);
    applySchema(db);   // additive ALTER adds last_seen_at
    db.prepare('UPDATE players SET last_seen_at=? WHERE id=?').run(1717000000000, 1);
    expect((db.prepare('SELECT last_seen_at FROM players WHERE id=1').get() as { last_seen_at: number }).last_seen_at)
      .toBe(1717000000000);
    applySchema(db);   // idempotent second boot: ALTER is caught, value survives
    expect((db.prepare('SELECT last_seen_at FROM players WHERE id=1').get() as { last_seen_at: number }).last_seen_at)
      .toBe(1717000000000);
  });
});

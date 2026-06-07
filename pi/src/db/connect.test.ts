import { describe, it, expect } from 'vitest';
import { DatabaseSync } from 'node:sqlite';
import { applySchema } from './connect';

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

import { describe, it, expect } from 'vitest';
import { DatabaseSync } from 'node:sqlite';
import { applySchema } from './connect';
import { CANONICAL_COURSES, RETRO_COURSE_SLUGS, seedCourses } from './courses';

function fresh(): DatabaseSync {
  const db = new DatabaseSync(':memory:');
  applySchema(db);
  return db;
}

describe('seedCourses (boot migration)', () => {
  it('seeds all 40 canonical courses on a fresh DB, idempotently', () => {
    const db = fresh();
    expect(seedCourses(db)).toBe(40);
    expect(seedCourses(db)).toBe(0);
    const n = (db.prepare('SELECT COUNT(*) AS n FROM courses').get() as { n: number }).n;
    expect(n).toBe(40);
  });

  it('appends the new courses after the existing 30 without touching their ids', () => {
    const db = fresh();
    const ins = db.prepare('INSERT INTO courses(slug, display_name) VALUES (?, ?)');
    for (const [slug, name] of CANONICAL_COURSES.slice(0, 30)) ins.run(slug, name);
    const before = db.prepare('SELECT id, slug FROM courses ORDER BY id').all();
    expect(seedCourses(db)).toBe(10);
    const after = db.prepare('SELECT id, slug FROM courses ORDER BY id').all() as { id: number; slug: string }[];
    expect(after.slice(0, 30)).toEqual(before);
    expect(after.slice(30).map((r) => r.slug)).toEqual(CANONICAL_COURSES.slice(30).map(([s]) => s));
  });

  it('the 10 retro courses are the SNES ones', () => {
    expect(RETRO_COURSE_SLUGS.size).toBe(10);
    for (const s of RETRO_COURSE_SLUGS) expect(s.startsWith('snes_')).toBe(true);
  });
});

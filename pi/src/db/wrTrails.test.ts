import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { insertWrTrail, getWrTrail, courseWrTrails } from './wrTrails';
import type { TrailPoint } from './trailCodec';

function setup() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'mario_circuit','Mario Circuit')");
  return db;
}

const addWr = (db: any, id: number, ms: number, holder: string, isCurrent = 0) =>
  db.prepare(`INSERT INTO world_records(id, course_id, cc, holder_name, record_ms, record_str, achieved_at, is_current)
              VALUES (?,1,150,?,?,?, '2026-04-06T00:00:00.000Z', ?)`)
    .run(id, holder, ms, '1:02.934', isCurrent);

const pts: TrailPoint[] = [
  { t_ms: 14, cx: 1635, cy: 875, score: 0.79, lap: 1 },
  { t_ms: 114, cx: 1636, cy: 870, score: 0.81, lap: 1 },
  { t_ms: 214, cx: 1640, cy: 860, score: 0.99, lap: 2 },
];

describe('wr trails', () => {
  it('round-trips a trail bit-exactly through the brotli codec', () => {
    const db = setup(); addWr(db, 10, 62934, 'JaK', 1);
    insertWrTrail(db, 10, pts);
    expect(getWrTrail(db, 10)).toEqual(pts);
  });

  it('stores n and max_t_ms as SQL-visible columns', () => {
    const db = setup(); addWr(db, 10, 62934, 'JaK', 1);
    insertWrTrail(db, 10, pts);
    expect(db.prepare('SELECT codec, n, max_t_ms FROM wr_trails WHERE wr_id=10').get())
      .toMatchObject({ codec: 1, n: 3, max_t_ms: 214 });
  });

  it('returns [] for a WR with no trail', () => {
    const db = setup(); addWr(db, 10, 62934, 'JaK', 1);
    expect(getWrTrail(db, 10)).toEqual([]);
  });

  it('replaces an existing trail rather than throwing', () => {
    const db = setup(); addWr(db, 10, 62934, 'JaK', 1);
    insertWrTrail(db, 10, pts);
    insertWrTrail(db, 10, pts.slice(0, 2));
    expect(getWrTrail(db, 10)).toHaveLength(2);
  });

  it('lists a course\'s trails fastest-first as 4-tuples, current flagged', () => {
    const db = setup();
    addWr(db, 10, 62934, 'JaK', 1);
    addWr(db, 11, 62978, 'LaRochelle', 0);
    insertWrTrail(db, 11, pts);
    insertWrTrail(db, 10, pts);
    const rows = courseWrTrails(db, 1, 150);
    expect(rows.map((r) => r.wr_id)).toEqual([10, 11]);        // record_ms ASC
    expect(rows[0].is_current).toBe(1);
    expect(rows[0].points[0]).toEqual([14, 1635, 875, 0.79]);  // lap dropped on the wire
  });

  it('omits trail-less and soft-removed WRs', () => {
    const db = setup();
    addWr(db, 10, 62934, 'JaK', 1);
    addWr(db, 11, 62978, 'Ghost', 0);
    insertWrTrail(db, 10, pts);
    insertWrTrail(db, 11, pts);
    db.prepare("UPDATE world_records SET removed_at = datetime('now') WHERE id=11").run();
    expect(courseWrTrails(db, 1, 150).map((r) => r.wr_id)).toEqual([10]);
  });

  it('wr_trails and wr_jobs cascade away when their world_records row is deleted', () => {
    const db = setup();
    addWr(db, 10, 62934, 'JaK', 1);
    insertWrTrail(db, 10, pts);
    db.prepare('INSERT INTO wr_jobs(wr_id) VALUES (10)').run();
    expect((db.prepare('SELECT COUNT(*) c FROM wr_trails').get() as { c: number }).c).toBe(1);
    expect((db.prepare('SELECT COUNT(*) c FROM wr_jobs').get() as { c: number }).c).toBe(1);
    db.exec('DELETE FROM world_records WHERE id=10');
    expect((db.prepare('SELECT COUNT(*) c FROM wr_trails').get() as { c: number }).c).toBe(0);
    expect((db.prepare('SELECT COUNT(*) c FROM wr_jobs').get() as { c: number }).c).toBe(0);
  });
});

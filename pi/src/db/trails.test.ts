import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { getRunPoints, insertTrail } from './trails';
import type { TrailPoint } from './trailCodec';

// Self-contained legacy DDL: schema.sql stops creating run_points in a later task,
// so the fallback tests build it themselves (IF NOT EXISTS keeps this valid both ways).
const LEGACY_DDL = `CREATE TABLE IF NOT EXISTS run_points (
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    t_ms INTEGER NOT NULL, cx REAL NOT NULL, cy REAL NOT NULL,
    score REAL NOT NULL DEFAULT 1.0, lap INTEGER);`;

function base() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','RR')");
  db.exec("INSERT INTO runs(id,attempt_id,season_id,player_id,course_id,cc,status,provenance) VALUES (10,'a',1,1,1,150,'finished','live')");
  return db;
}

const PTS: TrailPoint[] = [
  { t_ms: 0, cx: 100.25, cy: 200.5, score: 0.9, lap: 1 },
  { t_ms: 40, cx: 101.125, cy: 201.75, score: 0.95, lap: null },
];

describe('insertTrail / getRunPoints', () => {
  it('round-trips a trail and fills codec/n/max_t_ms', () => {
    const db = base();
    insertTrail(db, 10, PTS);
    expect(getRunPoints(db, 10)).toEqual(PTS);
    expect(db.prepare('SELECT codec, n, max_t_ms FROM run_trails WHERE run_id=10').get())
      .toEqual({ codec: 1, n: 2, max_t_ms: 40 });
  });

  it('returns [] for a run with no trail', () => {
    expect(getRunPoints(base(), 10)).toEqual([]);
  });

  it('falls back to legacy run_points rows while that table exists', () => {
    const db = base();
    db.exec(LEGACY_DDL);
    db.exec('INSERT INTO run_points(run_id,t_ms,cx,cy,score,lap) VALUES (10,0,1.5,2.5,0.9,1),(10,16,1.75,2.25,0.8,NULL)');
    expect(getRunPoints(db, 10)).toEqual([
      { t_ms: 0, cx: 1.5, cy: 2.5, score: 0.9, lap: 1 },
      { t_ms: 16, cx: 1.75, cy: 2.25, score: 0.8, lap: null },
    ]);
  });

  it('returns [] when neither a blob nor the legacy table exists', () => {
    const db = base();
    db.exec('DROP TABLE IF EXISTS run_points');
    expect(getRunPoints(db, 10)).toEqual([]);
  });

  it('blob cascades away when its run is deleted', () => {
    const db = base();
    insertTrail(db, 10, PTS);
    db.exec('DELETE FROM runs WHERE id=10');
    expect((db.prepare('SELECT COUNT(*) c FROM run_trails').get() as { c: number }).c).toBe(0);
  });

  it('throws on an unknown codec value (future format bump)', () => {
    const db = base();
    insertTrail(db, 10, PTS);
    db.exec('UPDATE run_trails SET codec=99 WHERE run_id=10');
    expect(() => getRunPoints(db, 10)).toThrow('unknown trail codec');
  });
});

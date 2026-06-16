import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { upsertRun, OVER_LIMIT_MS, findGhostMatch, enrichRunFromGhost } from './ingest';
import type { AttemptPayload } from './types';

function base() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rainbow_road','Rainbow Road')");
  return db;
}

const payload: AttemptPayload = {
  attempt_id: 'a1', course: 'Rainbow Road', cc: 150, status: 'finished',
  character: 'Mario', kart: 'Std', costume: 'Base',
  started_at: '2026-06-01T00:00:00Z', ended_at: '2026-06-01T00:02:00Z',
  total_time: '2:00.000',
  laps: [{ lap: 1, time_ms: 40000, coins: 5, shrooms: 1 }, { lap: 2, time_ms: 80000, coins: 3, shrooms: 0 }],
  points: [[0, 1, 2, 0.9], [16, 1.1, 2.1, 0.95]],
};

describe('upsertRun', () => {
  it('stores lap_time_str (and coins/shrooms) on run_laps', () => {
    const db = openDb(':memory:'); applySchema(db);
    db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
    db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
    db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rainbow_road','Rainbow Road')");
    const runId = upsertRun(db, {
      attempt_id: 'a1', course: 'Rainbow Road', status: 'finished', total_time: '1:40.000',
      laps: [{ lap: 1, time_ms: 40000, time_str: '0:40.000', coins: 5, shrooms: 2 }],
    } as any, 1, 1);
    const row = db.prepare(
      'SELECT lap_time_str, coins, shrooms FROM run_laps WHERE run_id=? AND lap_index=1'
    ).get(runId) as any;
    expect(row.lap_time_str).toBe('0:40.000');
    expect(row.coins).toBe(5);
    expect(row.shrooms).toBe(2);
  });

  it('inserts a live finished run with laps + points', () => {
    const db = base();
    const runId = upsertRun(db, payload, 1, 1);
    const run = db.prepare('SELECT * FROM runs WHERE id=?').get(runId) as any;
    expect(run.season_id).toBe(1);
    expect(run.player_id).toBe(1);
    expect(run.course_id).toBe(1);
    expect(run.provenance).toBe('live');
    expect(run.total_time_ms).toBe(120000);
    expect((db.prepare('SELECT COUNT(*) c FROM run_laps WHERE run_id=?').get(runId) as any).c).toBe(2);
    expect((db.prepare('SELECT COUNT(*) c FROM run_points WHERE run_id=?').get(runId) as any).c).toBe(2);
  });

  it('stores the per-point lap stamp (null when a legacy 4-tuple omits it)', () => {
    const db = base();
    const runId = upsertRun(db, {
      attempt_id: 'L1', course: 'Rainbow Road', status: 'reset',
      points: [[0, 1, 2, 0.9, 1], [16, 1.1, 2.1, 0.95, 2], [32, 1.2, 2.2, 0.8]],   // last omits lap
    } as any, 1, 1);
    const rows = db.prepare('SELECT lap FROM run_points WHERE run_id=? ORDER BY t_ms').all(runId) as { lap: number | null }[];
    expect(rows.map((r) => r.lap)).toEqual([1, 2, null]);
  });

  it('is idempotent by attempt_id (re-send replaces, no dup)', () => {
    const db = base();
    upsertRun(db, payload, 1, 1);
    upsertRun(db, payload, 1, 1);
    expect((db.prepare("SELECT COUNT(*) c FROM runs WHERE provenance='live'").get() as any).c).toBe(1);
    expect((db.prepare('SELECT COUNT(*) c FROM run_laps').get() as any).c).toBe(2);
  });

  it('parses total_time null for resets', () => {
    const db = base();
    const id = upsertRun(db, { attempt_id: 'r1', course: 'Rainbow Road', status: 'reset' }, 1, 1);
    const run = db.prepare('SELECT * FROM runs WHERE id=?').get(id) as any;
    expect(run.status).toBe('reset');
    expect(run.total_time_ms).toBeNull();
  });

  it('persists run-level coin/mushroom totals, incl. on a reset', () => {
    const db = base();
    const id = upsertRun(db, {
      attempt_id: 'c1', course: 'Rainbow Road', status: 'reset',
      coins_gained: 14, coins_lost: 6, mushrooms_used: 3,
    } as any, 1, 1);
    const run = db.prepare('SELECT coins_gained, coins_lost, mushrooms_used FROM runs WHERE id=?').get(id) as any;
    expect(run.coins_gained).toBe(14);   // a reset's coins are no longer lost
    expect(run.coins_lost).toBe(6);
    expect(run.mushrooms_used).toBe(3);
  });

  it('drops the trail of a runaway >11min recording but keeps run + laps', () => {
    const db = base();
    const over = OVER_LIMIT_MS + 5000;
    const runId = upsertRun(db, {
      attempt_id: 'long1', course: 'Rainbow Road', status: 'dnf',
      laps: [{ lap: 1, time_ms: 60000 }],
      points: [[0, 1, 2, 0.9, 1], [over, 3, 4, 0.9, 1]],
    } as any, 1, 1);
    expect((db.prepare('SELECT COUNT(*) c FROM runs WHERE id=?').get(runId) as any).c).toBe(1);
    expect((db.prepare('SELECT COUNT(*) c FROM run_laps WHERE run_id=?').get(runId) as any).c).toBe(1);
    expect((db.prepare('SELECT COUNT(*) c FROM run_points WHERE run_id=?').get(runId) as any).c).toBe(0);
  });

  it('keeps the trail when the recording is within the 11min limit (boundary inclusive)', () => {
    const db = base();
    const runId = upsertRun(db, {
      attempt_id: 'ok1', course: 'Rainbow Road', status: 'finished',
      points: [[0, 1, 2, 0.9, 1], [OVER_LIMIT_MS, 3, 4, 0.9, 1]],   // exactly at the limit -> kept
    } as any, 1, 1);
    expect((db.prepare('SELECT COUNT(*) c FROM run_points WHERE run_id=?').get(runId) as any).c).toBe(2);
  });
});

describe('ghost dedup + enrich', () => {
  it('findGhostMatch matches a finished run by exact total_time_ms', () => {
    const db = base();
    // a carryover-style finished run, no laps/points/identity
    db.exec("INSERT INTO runs(id,attempt_id,season_id,player_id,course_id,cc,status,provenance,total_time_ms) " +
            "VALUES (50,'cv',1,1,1,150,'finished','carryover',100000)");
    expect(findGhostMatch(db, 1, 1, 1, 150, 100000)).toBe(50);
    expect(findGhostMatch(db, 1, 1, 1, 150, 100001)).toBeNull();   // off by 1ms
    expect(findGhostMatch(db, 1, 2, 1, 150, 100000)).toBeNull();   // other player
  });

  it('enrichRunFromGhost gap-fills identity + adds laps/points + marks source', () => {
    const db = base();
    db.exec("INSERT INTO runs(id,attempt_id,season_id,player_id,course_id,cc,status,provenance,total_time_ms) " +
            "VALUES (50,'cv',1,1,1,150,'finished','carryover',100000)");
    const res = enrichRunFromGhost(db, 50, {
      attempt_id: 'g1', course: 'Rainbow Road', status: 'finished', total_time: '1:40.000',
      character: 'Mario', kart: 'K', costume: 'Base', total_laps: 1,
      laps: [{ lap: 1, time_ms: 100000, time_str: '1:40.000', coins: 3, shrooms: 1 }],
      points: [[0, 1, 2, 1, 1]], source: 'ghost',
    } as any);
    const run = db.prepare('SELECT character, kart, costume, source FROM runs WHERE id=50').get() as any;
    expect(run.character).toBe('Mario');
    expect(run.source).toBe('ghost');
    expect((db.prepare('SELECT COUNT(*) c FROM run_laps WHERE run_id=50').get() as any).c).toBe(1);
    expect((db.prepare('SELECT COUNT(*) c FROM run_points WHERE run_id=50').get() as any).c).toBe(1);
    expect(res.trailAdded).toBe(true);
  });

  it('enrich never overwrites existing identity or existing laps', () => {
    const db = base();
    db.exec("INSERT INTO runs(id,attempt_id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,character) " +
            "VALUES (60,'lv',1,1,1,150,'finished','live',100000,'Peach')");
    db.exec("INSERT INTO run_laps(run_id,lap_index,lap_time_ms,lap_time_str,coins,shrooms) VALUES (60,1,100000,'1:40.000',9,9)");
    const res = enrichRunFromGhost(db, 60, {
      attempt_id: 'g2', course: 'Rainbow Road', status: 'finished', character: 'Mario',
      total_laps: 1, laps: [{ lap: 1, time_ms: 100000, time_str: '1:40.000', coins: 1, shrooms: 1 }],
      points: [[0, 1, 2, 1, 1]], source: 'ghost',
    } as any);
    const run = db.prepare('SELECT character FROM runs WHERE id=60').get() as any;
    expect(run.character).toBe('Peach');                       // not overwritten
    const lap = db.prepare('SELECT coins FROM run_laps WHERE run_id=60 AND lap_index=1').get() as any;
    expect(lap.coins).toBe(9);                                 // existing laps kept
    expect(res.trailAdded).toBe(true);                         // had no points -> trail added
  });
});

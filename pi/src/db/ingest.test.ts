import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { upsertRun } from './ingest';
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
});

import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { recomputeIsPb } from './pb';

function withRuns() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','RR')");
  const ins = db.prepare("INSERT INTO runs(season_id,player_id,course_id,cc,status,provenance,total_time_ms,ended_at) VALUES (1,1,1,150,'finished','live',?,?)");
  ins.run(110000, '2026-01-01'); ins.run(108000, '2026-02-01'); ins.run(112000, '2026-03-01');
  return db;
}

describe('recomputeIsPb', () => {
  it('flags the single fastest finished run for the scope', () => {
    const db = withRuns();
    recomputeIsPb(db, 1, 1, 1, 150);
    const pbs = db.prepare('SELECT total_time_ms FROM runs WHERE is_pb=1').all() as { total_time_ms: number }[];
    expect(pbs.map(r => r.total_time_ms)).toEqual([108000]);
  });
});

import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { recordGhostImport } from './ghostImport';

function db() {
  const d = openDb(':memory:'); applySchema(d);
  d.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
  d.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
  d.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'choco_mountain','Choco Mountain')");
  d.exec("INSERT INTO runs(id,attempt_id,season_id,player_id,course_id,cc,status,provenance,total_time_ms) " +
         "VALUES (5,'cv',1,1,1,150,'finished','carryover',100000)");
  return d;
}

describe('recordGhostImport', () => {
  it('writes an audit row', () => {
    const d = db();
    recordGhostImport(d, { runId: 5, playerId: 1, courseId: 1, cc: 150, totalMs: 100000, action: 'enriched' });
    const row = d.prepare('SELECT run_id, action, total_time_ms FROM ghost_imports').get() as any;
    expect(row).toEqual({ run_id: 5, action: 'enriched', total_time_ms: 100000 });
  });
});

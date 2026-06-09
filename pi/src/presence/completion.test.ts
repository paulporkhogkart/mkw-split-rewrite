import { describe, it, expect } from 'vitest';
import { DatabaseSync } from 'node:sqlite';
import { applySchema } from '../db/connect';
import { makeLiveCompletion } from './completion';

function db() {
  const d = new DatabaseSync(':memory:'); applySchema(d);
  d.exec(`INSERT INTO seasons(id,name,is_active) VALUES(1,'S1',1);
          INSERT INTO players(id,display_name) VALUES(1,'Luke');
          INSERT INTO courses(id,slug,display_name) VALUES(1,'bowsers_castle','Bowsers Castle');`);
  // 2-lap reference run: (0,0)->(10,0)->(0,0)[lap1]->(10,0)->(0,0)[finish]
  const LOOP: [number, number, number][] = [[0, 0, 0], [10, 0, 50], [0, 0, 100], [10, 0, 150], [0, 0, 200]];
  d.prepare(`INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,ended_at,total_time_ms)
             VALUES(1,1,1,1,150,'finished','live','2026-06-10T00:00:00.000Z',200000)`).run();
  for (const [cx, cy, t] of LOOP) d.prepare('INSERT INTO run_points(run_id,t_ms,cx,cy,score) VALUES(1,?,?,?,1)').run(t, cx, cy);
  d.prepare('INSERT INTO run_laps(run_id,lap_index,lap_time_ms) VALUES(1,0,100),(1,1,100)').run();
  return d;
}

describe('makeLiveCompletion', () => {
  it('projects a live position onto the course route, lap-gated', () => {
    const live = makeLiveCompletion(db());
    // On lap 2 (1 completed), at the lap-2 apex (10,0) -> route fraction 0.75
    expect(live('Bowsers Castle', 2, [10, 0])).toBeCloseTo(0.75, 5);
  });
  it('returns null with no position or an unknown course', () => {
    const live = makeLiveCompletion(db());
    expect(live('Bowsers Castle', 2, null)).toBeNull();
    expect(live('Nope', 1, [0, 0])).toBeNull();
  });

  it('keeps per-player state and resets it on a new run', () => {
    const live = makeLiveCompletion(db());
    // Player 1 advances on lap 2; a self-recurring (10,0) tracks forward, never snapping back to lap 1
    expect(live('Bowsers Castle', 2, [10, 0], 1, 1000, false)).toBeCloseTo(0.75, 2);
    expect(live('Bowsers Castle', 2, [0, 0], 1, 1100, false)).toBeGreaterThanOrEqual(0.75 - 0.01); // (0,0)=lap2 end ~1.0, forward
    // A new run for player 1 (lap drops to 1) resets state -> back near the start
    expect(live('Bowsers Castle', 1, [0, 0], 1, 5000, false)).toBeLessThan(0.2);
  });

  it('holds completion while the fix is stale', () => {
    const live = makeLiveCompletion(db());
    live('Bowsers Castle', 2, [10, 0], 1, 1000, false);              // s ~ 0.75
    expect(live('Bowsers Castle', 2, [0, 0], 1, 1100, true)).toBeCloseTo(0.75, 2); // stale -> hold
  });
});

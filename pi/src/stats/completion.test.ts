import { describe, it, expect } from 'vitest';
import { DatabaseSync } from 'node:sqlite';
import { applySchema } from '../db/connect';
import { completionFraction, resolveCompletion } from './completion';
import { buildReference } from './progress';
import { resolvePeriod } from './period';

const allTime = () => resolvePeriod('all_time', 'Australia/Melbourne');

// A 2-lap loop: (0,0)->(10,0)->(0,0) [lap 1 end] ->(10,0)->(0,0) [finish]
const LOOP = [
  { cx: 0, cy: 0, t_ms: 0 }, { cx: 10, cy: 0, t_ms: 50 }, { cx: 0, cy: 0, t_ms: 100 },
  { cx: 10, cy: 0, t_ms: 150 }, { cx: 0, cy: 0, t_ms: 200 },
];

describe('completionFraction', () => {
  it('uses lowerS to pick the correct lap when a position recurs', () => {
    const ref = buildReference(LOOP);
    // (10,0) appears at s=0.25 (lap 1) and s=0.75 (lap 2)
    expect(completionFraction(ref, 0, 10, 0)).toBe(0.25);     // no gate -> first match
    expect(completionFraction(ref, 0.5, 10, 0)).toBe(0.75);   // gated past lap 1 -> lap 2
  });
});

function base(): DatabaseSync {
  const d = new DatabaseSync(':memory:');
  applySchema(d);
  d.exec(`INSERT INTO seasons(id,name,is_active) VALUES(1,'S1',1);
          INSERT INTO players(id,display_name) VALUES(1,'Luke');
          INSERT INTO courses(id,slug,display_name) VALUES(1,'bc','BC');`);
  return d;
}
function addRun(d: DatabaseSync, id: number, status: string) {
  d.prepare(`INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,ended_at,total_time_ms)
             VALUES(?,1,1,1,150,?,'live','2026-06-10T00:00:00+00:00',?)`).run(id, status, status === 'finished' ? 200000 : null);
}
function addPoints(d: DatabaseSync, runId: number, pts: { cx: number; cy: number; t_ms: number }[]) {
  for (const p of pts) d.prepare('INSERT INTO run_points(run_id,t_ms,cx,cy,score) VALUES(?,?,?,?,1)').run(runId, p.t_ms, p.cx, p.cy);
}
function addLaps(d: DatabaseSync, runId: number, times: number[]) {
  times.forEach((t, i) => d.prepare('INSERT INTO run_laps(run_id,lap_index,lap_time_ms) VALUES(?,?,?)').run(runId, i, t));
}

describe('resolveCompletion', () => {
  it('estimates a reset that stopped mid lap-2 at ~0.75 of the route', () => {
    const d = base();
    addRun(d, 1, 'finished'); addPoints(d, 1, LOOP); addLaps(d, 1, [100, 100]); // 2-lap reference
    addRun(d, 2, 'reset'); addLaps(d, 2, [100]);                                  // completed 1 lap
    addPoints(d, 2, [{ cx: 5, cy: 0, t_ms: 140 }, { cx: 10, cy: 0, t_ms: 150 }]); // last point (10,0)
    const r = resolveCompletion(d, { metric: 'avg_completion_before_reset', period: allTime(), filters: { course: 'bc' }, seasonId: 1 });
    expect(r.total).toBeCloseTo(0.75, 5);
    expect(r.unevaluable).toBe(0);
  });

  it('counts resets with no trail as unevaluable', () => {
    const d = base();
    addRun(d, 1, 'finished'); addPoints(d, 1, LOOP); addLaps(d, 1, [100, 100]);
    addRun(d, 2, 'reset'); // no points
    const r = resolveCompletion(d, { metric: 'avg_completion_before_reset', period: allTime(), filters: { course: 'bc' }, seasonId: 1 });
    expect(r.total).toBeNull();
    expect(r.unevaluable).toBe(1);
  });

  it('is null when the course has no finished reference', () => {
    const d = base();
    addRun(d, 2, 'reset'); addPoints(d, 2, [{ cx: 10, cy: 0, t_ms: 10 }]);
    const r = resolveCompletion(d, { metric: 'avg_completion_before_reset', period: allTime(), filters: { course: 'bc' }, seasonId: 1 });
    expect(r.total).toBeNull();
    expect(r.unevaluable).toBe(1);
  });
});

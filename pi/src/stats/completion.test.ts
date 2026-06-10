import { describe, it, expect } from 'vitest';
import { DatabaseSync } from 'node:sqlite';
import { applySchema } from '../db/connect';
import { saveCourseModel } from '../db/courseModels';
import { resolveCompletion } from './completion';
import { resolvePeriod } from './period';
import type { CourseModel } from '../progress/types';

const allTime = () => resolvePeriod('all_time', 'Australia/Melbourne');

// Single-lap straight-line model: (0,0) -> (10,0), total 10 px.
// projectStep onto this line at (x,0) -> completion = x/10.
const LINE: [number, number][] = [[0, 0], [10, 0]];
const lineModel = (): CourseModel => ({ version: 2, totalLengthPx: 10, status: 'centerline',
  laps: [{ index: 1, lengthPx: 10, startOffsetPx: 0,
    graph: { version: 1, startNode: 0, lapLengthPx: 10, status: 'centerline',
      nodes: [{ id: 0, x: 0, y: 0, progress: 0 }],
      edges: [{ id: 0, a: 0, b: 0, poly: LINE, arcLen: 10, pLo: 0, pHi: 1, kind: 'main', passThrough: null }] }}] });

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
  it('estimates a reset that stopped mid-run at ~0.75 of the route', () => {
    const d = base();
    saveCourseModel(d, 1, 150, lineModel(), 1);
    addRun(d, 2, 'reset'); addLaps(d, 2, []);
    // last point at x=7.5 on the LINE -> completion = 7.5/10 = 0.75
    addPoints(d, 2, [{ cx: 0, cy: 0, t_ms: 0 }, { cx: 7.5, cy: 0, t_ms: 50 }]);
    const r = resolveCompletion(d, { metric: 'avg_completion_before_reset', period: allTime(), filters: { course: 'bc' }, seasonId: 1 });
    expect(r.total).toBeCloseTo(0.75, 5);
    expect(r.unevaluable).toBe(0);
  });

  it('replays a partial reset and returns a partial score', () => {
    const d = base();
    saveCourseModel(d, 1, 150, lineModel(), 1);
    addRun(d, 2, 'reset'); addLaps(d, 2, []);
    // last point at x=5 on the LINE -> completion = 5/10 = 0.5
    addPoints(d, 2, [{ cx: 0, cy: 0, t_ms: 0 }, { cx: 5, cy: 0, t_ms: 50 }]);
    const r = resolveCompletion(d, { metric: 'avg_completion_before_reset', period: allTime(), filters: { course: 'bc' }, seasonId: 1 });
    expect(r.total).toBeCloseTo(0.5, 5);
    expect(r.unevaluable).toBe(0);
  });

  it('counts resets with no trail as unevaluable', () => {
    const d = base();
    saveCourseModel(d, 1, 150, lineModel(), 1);
    addRun(d, 2, 'reset'); // no points
    const r = resolveCompletion(d, { metric: 'avg_completion_before_reset', period: allTime(), filters: { course: 'bc' }, seasonId: 1 });
    expect(r.total).toBeNull();
    expect(r.unevaluable).toBe(1);
  });

  it('is null when the course has no saved model', () => {
    const d = base();
    // no saveCourseModel call -> getModel returns null -> unevaluable
    addRun(d, 2, 'reset'); addPoints(d, 2, [{ cx: 10, cy: 0, t_ms: 10 }]);
    const r = resolveCompletion(d, { metric: 'avg_completion_before_reset', period: allTime(), filters: { course: 'bc' }, seasonId: 1 });
    expect(r.total).toBeNull();
    expect(r.unevaluable).toBe(1);
  });
});

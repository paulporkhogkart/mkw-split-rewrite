// pi/src/progress/project.test.ts
import { describe, it, expect } from 'vitest';
import { projectStep, prepareModel } from './project';
import type { CourseModel, ProjState } from './types';

const SQUARE: [number, number][] = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]];
const lap = (index: number, startOffsetPx: number, lengthPx = 40): import('./types').LapRoute => ({
  index, lengthPx, startOffsetPx,
  graph: { version: 1, startNode: 0, lapLengthPx: lengthPx, status: 'centerline',
    nodes: [{ id: 0, x: 0, y: 0, progress: 0 }],
    edges: [{ id: 0, a: 0, b: 0, poly: SQUARE, arcLen: lengthPx, pLo: 0, pHi: 1, kind: 'main', passThrough: null }] },
});
// Two equal laps -> total 80; lap boundary at 50%.
const M: CourseModel = { version: 2, totalLengthPx: 80, status: 'centerline', laps: [lap(1, 0), lap(2, 40)] };
const obs = (x: number, y: number, lp = 1, t = 0, stale = false) => ({ x, y, lap: lp, totLap: 2, t, stale });

describe('projectStep (distance, per-lap)', () => {
  it('accumulates distance across laps', () => {
    const pe = prepareModel(M);
    let st: ProjState = null;
    let r = projectStep(st, M, pe, obs(10, 0, 1, 0)); st = r.state;     // lap1 quarter -> 10/80
    expect(r.completion).toBeCloseTo(10 / 80, 2);
    r = projectStep(st, M, pe, obs(10, 0, 2, 100));                     // lap2 quarter -> (40+10)/80
    expect(r.completion).toBeCloseTo(50 / 80, 2);
  });

  it('uneven laps put the lap-1 boundary at its real proportion, not 1/N', () => {
    const UM: CourseModel = { version: 2, totalLengthPx: 100, status: 'centerline', laps: [lap(1, 0, 80), lap(2, 80, 20)] };
    const pe = prepareModel(UM);
    const r = projectStep({ edge: 0, progress: 1, x: 0, y: 0, t: 0 }, UM, pe, { x: 0, y: 0, lap: 2, totLap: 2, t: 1, stale: false });
    expect(r.completion).toBeGreaterThanOrEqual(80 / 100 - 0.01);       // end lap1 ≈ 80%, not 50%
  });

  it('clamps to [0,1] past the final lap, and holds while stale', () => {
    const pe = prepareModel(M);
    expect(projectStep({ edge: 0, progress: 1, x: 0, y: 0, t: 0 }, M, pe, obs(0, 0, 3, 9)).completion).toBe(1);
    expect(projectStep({ edge: 0, progress: 0.5, x: 5, y: 0, t: 0 }, M, pe, obs(9, 9, 1, 9, true)).completion).toBeCloseTo((0 + 0.5 * 40) / 80, 4);
  });
});

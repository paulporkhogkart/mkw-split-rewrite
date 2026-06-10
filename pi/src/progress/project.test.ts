// pi/src/progress/project.test.ts
import { describe, it, expect } from 'vitest';
import { projectStep, prepareEdges } from './project';
import type { CourseGraph, ProjState } from './types';

// Unit square loop as a single cyclic centerline edge, progress = arc fraction.
const SQUARE: [number, number][] = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]];
const G: CourseGraph = { version: 1, startNode: 0, lapLengthPx: 40, status: 'centerline',
  nodes: [{ id: 0, x: 0, y: 0, progress: 0 }],
  edges: [{ id: 0, a: 0, b: 0, poly: SQUARE, arcLen: 40, pLo: 0, pHi: 1, kind: 'main', passThrough: null }] };

const obs = (x: number, y: number, lap = 1, t = 0, stale = false) => ({ x, y, lap, totLap: 3, t, stale });

describe('projectStep', () => {
  it('bootstraps to the nearest progress and advances forward', () => {
    const pe = prepareEdges(G);
    let st: ProjState = null;
    let r = projectStep(st, G, pe, obs(0, 0, 1, 0)); st = r.state;
    expect(r.completion).toBeCloseTo(0, 2);                         // start, lap1/3 -> 0
    r = projectStep(st, G, pe, obs(10, 0, 1, 100)); st = r.state;   // quarter way
    expect(r.completion).toBeCloseTo(0.25 / 3, 2);
  });

  it('does not snap back when the path nears an earlier point (forward window)', () => {
    const pe = prepareEdges(G);
    let st: ProjState = null;
    st = projectStep(st, G, pe, obs(10, 0, 1, 0)).state;           // progress .25
    st = projectStep(st, G, pe, obs(10, 10, 1, 100)).state;        // progress .5
    const r = projectStep(st, G, pe, obs(1, 0, 1, 200));           // near start (.0/1.0) but we're at .5
    expect(r.completion).toBeGreaterThan(0.5 / 3 - 0.02);          // stayed forward, no snap to ~0
  });

  it('seam: lap from HUD makes completion continuous across the line', () => {
    const pe = prepareEdges(G);
    let st: ProjState = { edge: 0, progress: 0.98, x: 0, y: 9, t: 0 };
    const r = projectStep(st, G, pe, obs(0, 0, 2, 100));           // crossed line, HUD lap=2
    expect(r.completion).toBeGreaterThanOrEqual(1 / 3 - 0.02);     // ~ (2-1+0)/3, not dropping below lap1
  });

  it('holds while stale', () => {
    const pe = prepareEdges(G);
    const st: ProjState = { edge: 0, progress: 0.4, x: 4, y: 0, t: 0 };
    expect(projectStep(st, G, pe, obs(9, 9, 1, 50, true)).completion).toBeCloseTo(0.4 / 3, 4);
  });
});

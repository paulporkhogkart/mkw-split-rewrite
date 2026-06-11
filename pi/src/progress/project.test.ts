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

  it('learns a pace EMA from confident steps', () => {
    const pe = prepareModel(M);
    let r = projectStep(null, M, pe, obs(10, 0, 1, 0));
    expect(r.state!.rate ?? null).toBeNull();                  // one fix: no pace yet
    r = projectStep(r.state, M, pe, obs(10, 8, 1, 1000));      // +8px course = +0.1 completion over 1s
    expect(r.state!.rate).toBeCloseTo(0.1 / 1000, 6);
  });

  it('glides on stale at the learned pace, capped at GLIDE_MAX_MS', () => {
    const pe = prepareModel(M);
    let r = projectStep(null, M, pe, obs(10, 0, 1, 0));
    r = projectStep(r.state, M, pe, obs(10, 8, 1, 1000));      // anchor 0.225, rate 1e-4/ms
    expect(r.completion).toBeCloseTo(0.225, 3);
    let h = projectStep(r.state, M, pe, obs(0, 0, 1, 1500, true));    // stale +500ms
    expect(h.completion).toBeCloseTo(0.225 + 1e-4 * 500, 3);
    h = projectStep(h.state, M, pe, obs(0, 0, 1, 2500, true));        // stale +1500ms
    expect(h.completion).toBeCloseTo(0.225 + 1e-4 * 1500, 3);
    h = projectStep(h.state, M, pe, obs(0, 0, 1, 9000, true));        // way past the cap
    expect(h.completion).toBeCloseTo(0.225 + 1e-4 * 2000, 3);         // clamped at 2s of pace
  });

  it('after a glide, re-acquisition never snaps the published value backward', () => {
    const pe = prepareModel(M);
    let r = projectStep(null, M, pe, obs(10, 0, 1, 0));
    r = projectStep(r.state, M, pe, obs(10, 8, 1, 1000));             // anchor 0.225, rate 1e-4
    const g = projectStep(r.state, M, pe, obs(0, 0, 1, 3000, true));  // glide to 0.425 (capped)
    expect(g.completion).toBeCloseTo(0.425, 3);
    // kart re-acquired barely past the anchor: truth ~0.2375 < glided 0.425
    const back = projectStep(g.state, M, pe, obs(10, 9, 1, 3100));
    expect(back.completion).toBeCloseTo(0.425, 3);                    // floored, no backward snap
    // a later fix beyond the floor publishes truth again
    const fwd = projectStep(back.state, M, pe, obs(0, 4, 1, 9000));   // 36px course -> 0.45
    expect(fwd.completion!).toBeGreaterThan(0.425);
  });

  it('with branch edges, picks the nearest branch -> same % whichever route you took', () => {
    // a straight main spine [0,1] with two branch arcs (bump up / bump down) over progress [0.4,0.6]
    const poly = (a: [number, number][]): [number, number][] => a;
    const G = {
      version: 1, startNode: 0, lapLengthPx: 40, status: 'graph' as const,
      nodes: [{ id: 0, x: 0, y: 0, progress: 0 }],
      edges: [
        { id: 0, a: 0, b: 0, poly: poly([[0, 0], [40, 0]]), arcLen: 40, pLo: 0, pHi: 1, kind: 'main' as const, passThrough: null },
        { id: 1, a: 0, b: 0, poly: poly([[16, 0], [20, -10], [24, 0]]), arcLen: 21.5, pLo: 0.4, pHi: 0.6, kind: 'branch' as const, passThrough: null },
        { id: 2, a: 0, b: 0, poly: poly([[16, 0], [20, 10], [24, 0]]), arcLen: 21.5, pLo: 0.4, pHi: 0.6, kind: 'branch' as const, passThrough: null },
      ],
    };
    const BM: CourseModel = { version: 2, totalLengthPx: 40, status: 'graph', laps: [{ index: 1, lengthPx: 40, startOffsetPx: 0, graph: G }] };
    const pe = prepareModel(BM);
    const up = projectStep(null, BM, pe, { x: 20, y: -9, lap: 1, totLap: 1, t: 0, stale: false }).completion;
    const dn = projectStep(null, BM, pe, { x: 20, y: 9, lap: 1, totLap: 1, t: 0, stale: false }).completion;
    expect(up).toBeCloseTo(0.5, 1);
    expect(dn).toBeCloseTo(0.5, 1);       // both branches map to the same progress (~mid of [0.4,0.6])
  });
});

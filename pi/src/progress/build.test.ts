// pi/src/progress/build.test.ts
import { describe, it, expect } from 'vitest';
import { foldRun, fBinCentroids, fitTranslation, type FoldPt } from './build';
import type { RunInput } from './types';

describe('foldRun', () => {
  it('computes in-lap fraction f from the lap stamp, folding all laps into one', () => {
    // 2 laps; lap 1 ends at t=100, lap 2 at t=200. Points stamped with lap.
    const run: RunInput = {
      playerId: 1, lapCumMs: [100, 200],
      points: [
        { t_ms: 0,   cx: 0, cy: 0, score: 1, lap: 1 },
        { t_ms: 50,  cx: 5, cy: 0, score: 1, lap: 1 },   // f=0.5
        { t_ms: 100, cx: 0, cy: 0, score: 1, lap: 2 },   // lap 2 start, f=0
        { t_ms: 150, cx: 5, cy: 0, score: 1, lap: 2 },   // f=0.5
      ],
    };
    const out = foldRun(run);
    expect(out.map((p) => Number(p.f.toFixed(3)))).toEqual([0, 0.5, 0, 0.5]);
    expect(out).toHaveLength(4);              // all laps folded together
  });

  it('falls back to time-derived lap when lap is null', () => {
    const run: RunInput = {
      playerId: 1, lapCumMs: [100, 200],
      points: [{ t_ms: 150, cx: 9, cy: 0, score: 1, lap: null }],  // 150 is in lap 2 -> f=0.5
    };
    expect(foldRun(run)[0].f).toBeCloseTo(0.5, 3);
  });
});

describe('alignment', () => {
  const ring = (n: number, ox = 0, oy = 0): FoldPt[] =>
    Array.from({ length: n }, (_, i) => ({ x: ox + Math.cos((i / n) * 2 * Math.PI),
      y: oy + Math.sin((i / n) * 2 * Math.PI), f: i / n, score: 1 }));

  it('recovers a pure translation between two runs of the same shape', () => {
    const ref = ring(64);
    const drifted = ring(64, 7, -3);                       // same shape, +7,-3 offset
    const t = fitTranslation(fBinCentroids(ref, 16), fBinCentroids(drifted, 16));
    expect(t.dx).toBeCloseTo(-7, 1);                       // maps drifted -> ref
    expect(t.dy).toBeCloseTo(3, 1);
  });
});

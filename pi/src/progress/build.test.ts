// pi/src/progress/build.test.ts
import { describe, it, expect } from 'vitest';
import { foldRun } from './build';
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

import { describe, it, expect } from 'vitest';
import { buildReference, lapBoundaries, prepareReference, step, type ProjState } from './progress';

const LOOP = [
  { cx: 0, cy: 0, t_ms: 0 }, { cx: 10, cy: 0, t_ms: 50 }, { cx: 0, cy: 0, t_ms: 100 },
  { cx: 10, cy: 0, t_ms: 150 }, { cx: 0, cy: 0, t_ms: 200 },
];

describe('buildReference', () => {
  it('normalises arc length to s in [0,1]', () => {
    const ref = buildReference([{ cx: 0, cy: 0, t_ms: 0 }, { cx: 5, cy: 0, t_ms: 50 }, { cx: 10, cy: 0, t_ms: 100 }]);
    expect(ref.map((p) => p.s)).toEqual([0, 0.5, 1]);
  });
});

describe('lapBoundaries', () => {
  it('places the lap-1 boundary at the route fraction matching its end-time', () => {
    expect(lapBoundaries(buildReference(LOOP), [100, 200])).toEqual([0.5, 1]);
  });
});

describe('prepareReference', () => {
  it('drops a teleport spike and a sub-pixel dup, then resamples ~uniformly', () => {
    const pts = [
      { cx: 0, cy: 0, t_ms: 0 }, { cx: 10, cy: 0, t_ms: 10 }, { cx: 10, cy: 0, t_ms: 11 }, // dup
      { cx: 1000, cy: 1000, t_ms: 12 },                                                     // teleport
      { cx: 40, cy: 0, t_ms: 40 }, { cx: 100, cy: 0, t_ms: 100 },
    ];
    const ref = prepareReference(pts, [100]);
    expect(ref.ref.every((p) => p.cx >= 0 && p.cx <= 100 && Math.abs(p.cy) < 1e-6)).toBe(true); // spike gone
    expect(ref.totalLen).toBeGreaterThan(90);
    expect(ref.totalLen).toBeLessThan(110);                                                   // ~100, not ~2828
    const gaps = ref.ref.slice(1).map((p, i) => p.s - ref.ref[i].s);
    expect(Math.max(...gaps)).toBeLessThan(0.1);                                              // resampled ~5px/100
    expect(ref.bounds).toEqual([1]);
  });

  it('preserves a recurring position at two distinct fractions (LOOP)', () => {
    const ref = prepareReference(LOOP, [100, 200]);
    expect(ref.bounds).toEqual([0.5, 1]);
    const at10 = ref.ref.filter((p) => Math.abs(p.cx - 10) < 1e-6).map((p) => p.s);
    expect(at10.some((s) => Math.abs(s - 0.25) < 0.02)).toBe(true);
    expect(at10.some((s) => Math.abs(s - 0.75) < 0.02)).toBe(true);
  });
});

describe('step (bootstrap)', () => {
  it('lap-gates a recurring position to the current lap (3 identical laps)', () => {
    // 3 laps of (0,0)->(10,0)->(0,0): lap ends at t=2,4,6
    const THREE = [
      { cx: 0, cy: 0, t_ms: 0 }, { cx: 10, cy: 0, t_ms: 1 }, { cx: 0, cy: 0, t_ms: 2 },
      { cx: 10, cy: 0, t_ms: 3 }, { cx: 0, cy: 0, t_ms: 4 },
      { cx: 10, cy: 0, t_ms: 5 }, { cx: 0, cy: 0, t_ms: 6 },
    ];
    const ref = prepareReference(THREE, [2, 4, 6]);
    const onLap2 = step(null, ref, { x: 10, y: 0, lap: 2, t: 0, stale: false });
    expect(onLap2.s).toBeCloseTo(0.5, 2);   // lap-2 copy of (10,0), not 0.167 or 0.833
  });

  it('returns null s for an empty reference', () => {
    expect(step(null, { ref: [], bounds: [], totalLen: 0 }, { x: 0, y: 0, lap: 1, t: 0, stale: false }).s).toBeNull();
  });
});

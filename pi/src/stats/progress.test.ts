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

// Self-crossing "X": (0,0)->(20,20)->(20,0)->(0,20); diagonals cross at (10,10) (s~0.185 & ~0.815)
const XPATH = [
  { cx: 0, cy: 0, t_ms: 0 }, { cx: 20, cy: 20, t_ms: 100 }, { cx: 20, cy: 0, t_ms: 200 }, { cx: 0, cy: 20, t_ms: 300 },
];

describe('step (tracking)', () => {
  it('stays on the entered branch through a self-crossing', () => {
    const ref = prepareReference(XPATH, [300]);
    let st: ProjState = null;
    const run = (x: number, y: number, t: number) => { const r = step(st, ref, { x, y, lap: 1, t, stale: false }); st = r.state; return r.s!; };
    run(0, 0, 0);
    run(5, 5, 100);
    const atCrossing1 = run(10, 10, 200);
    expect(atCrossing1).toBeLessThan(0.4);     // branch 1, not the s~0.815 branch
    run(20, 0, 300);
    const atCrossing2 = run(10, 10, 400);
    expect(atCrossing2).toBeGreaterThan(0.7);  // now legitimately on branch 3
  });

  it('clamps a small backward-noisy observation (no reversal beyond EPS_BACK)', () => {
    const LINE = [{ cx: 0, cy: 0, t_ms: 0 }, { cx: 100, cy: 0, t_ms: 100 }];
    const ref = prepareReference(LINE, [100]);
    const r = step({ s: 0.5, t: 0, x: 50, y: 0 }, ref, { x: 48, y: 0, lap: 1, t: 50, stale: false });
    expect(r.s).toBeCloseTo(0.496, 3);         // clamped to 0.5 - EPS_BACK, not 0.48
  });
});

describe('step (bootstrap edges)', () => {
  it('uses heading to pick the right branch at a crossing', () => {
    const ref = prepareReference(XPATH, [300]);
    // dt > DROPOUT_MS forces bootstrap; state supplies only the heading
    const upRight = step({ s: 0.18, t: 0, x: 5, y: 5 }, ref, { x: 10, y: 10, lap: 1, t: 5000, stale: false });
    expect(upRight.s).toBeLessThan(0.4);       // heading (1,1) -> branch 1
    const upLeft = step({ s: 0.8, t: 0, x: 15, y: 5 }, ref, { x: 10, y: 10, lap: 1, t: 5000, stale: false });
    expect(upLeft.s).toBeGreaterThan(0.7);     // heading (-1,1) -> branch 3
  });

  it('re-bootstraps after a dropout (a backward correction is allowed)', () => {
    const LINE = [{ cx: 0, cy: 0, t_ms: 0 }, { cx: 100, cy: 0, t_ms: 100 }];
    const ref = prepareReference(LINE, [100]);
    const dropout = step({ s: 0.2, t: 0, x: 20, y: 0 }, ref, { x: 10, y: 0, lap: 1, t: 5000, stale: false });
    expect(dropout.s).toBeCloseTo(0.1, 2);     // free re-bootstrap, not clamped to ~0.196
    const tracked = step({ s: 0.2, t: 0, x: 20, y: 0 }, ref, { x: 10, y: 0, lap: 1, t: 100, stale: false });
    expect(tracked.s).toBeGreaterThanOrEqual(0.196); // in-window tracking clamps the reversal
  });

  it('holds s while the fix is stale', () => {
    const ref = prepareReference([{ cx: 0, cy: 0, t_ms: 0 }, { cx: 100, cy: 0, t_ms: 100 }], [100]);
    const r = step({ s: 0.42, t: 0, x: 5, y: 5 }, ref, { x: 99, y: 0, lap: 1, t: 100, stale: true });
    expect(r.s).toBe(0.42);
  });
});

describe('step (window isolation)', () => {
  it('the local window — not heading — excludes the far branch of a near-parallel overlap', () => {
    // branch1 along y=0 (s ~0..0.45), branch2 along y=2 (s ~0.5..0.95). A noisy point sits
    // slightly CLOSER to branch2 with a heading (0,1) perpendicular to both branches' tangents,
    // so heading gives no bonus to either -> only the tracking window keeps it on branch1.
    // (Falsifier: bootstrap-only would snap to the nearer far branch at ~0.727.)
    const NEAR = [
      { cx: 0, cy: 0, t_ms: 0 }, { cx: 20, cy: 0, t_ms: 100 },
      { cx: 20, cy: 2, t_ms: 150 }, { cx: 0, cy: 2, t_ms: 250 }, { cx: 0, cy: 4, t_ms: 300 },
    ];
    const ref = prepareReference(NEAR, [300]);
    let st: ProjState = null;
    const run = (x: number, y: number, t: number) => { const r = step(st, ref, { x, y, lap: 1, t, stale: false }); st = r.state; return r.s!; };
    run(0, 0, 0); run(5, 0, 100); run(10, 0, 200);
    expect(run(10, 1.1, 300)).toBeLessThan(0.4); // stays on branch1 (~0.227)
  });
});

// pi/src/progress/build.test.ts
import { describe, it, expect } from 'vitest';
import { foldRun, fBinCentroids, fitTranslation, buildCourseModel, groupByLap, type FoldPt } from './build';
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

function loopRun(playerId: number, ox = 0, oy = 0): RunInput {
  // 2 laps around a unit circle, 1 lap = 36 pts, lap ends 1000/2000ms.
  const pts = [];
  for (let lap = 1; lap <= 2; lap++) for (let i = 0; i < 36; i++) {
    const a = (i / 36) * 2 * Math.PI;
    pts.push({ t_ms: (lap - 1) * 1000 + (i / 36) * 1000, cx: ox + 50 * Math.cos(a),
      cy: oy + 50 * Math.sin(a), score: 1, lap });
  }
  return { playerId, lapCumMs: [1000, 2000], points: pts };
}


describe('groupByLap', () => {
  it('splits points by lap index with per-lap fraction f', () => {
    const run: RunInput = {
      playerId: 1, lapCumMs: [100, 200],
      points: [
        { t_ms: 0,   cx: 0, cy: 0, score: 1, lap: 1 },
        { t_ms: 50,  cx: 5, cy: 0, score: 1, lap: 1 },   // lap1 f=0.5
        { t_ms: 150, cx: 9, cy: 0, score: 1, lap: 2 },   // lap2 f=0.5
      ],
    };
    const g = groupByLap(run);
    expect([...g.keys()].sort()).toEqual([1, 2]);
    expect(g.get(1)!.map((p) => Number(p.f.toFixed(2)))).toEqual([0, 0.5]);
    expect(g.get(2)!.map((p) => Number(p.f.toFixed(2)))).toEqual([0.5]);
  });
});

// lap 1 is a big circle (r=50), lap 2 a small one (r=20): unequal lengths.
function unevenRun(playerId: number): RunInput {
  const pts: RunInput['points'] = [];
  const add = (lap: number, r: number, base: number) => {
    for (let i = 0; i < 48; i++) { const a = (i / 48) * 2 * Math.PI;
      pts.push({ t_ms: base + (i / 48) * 1000, cx: r * Math.cos(a), cy: r * Math.sin(a), score: 1, lap }); }
  };
  add(1, 50, 0); add(2, 20, 1000);
  return { playerId, lapCumMs: [1000, 2000], points: pts };
}

describe('buildCourseModel (v2 per-lap)', () => {
  it('builds one LapRoute per lap, with cumulative offsets and a distance total', () => {
    const res = buildCourseModel([unevenRun(1), unevenRun(2)], { bins: 64 });
    expect(res).not.toBeNull();
    const m = res!.model;
    expect(m.version).toBe(2);
    expect(m.laps).toHaveLength(2);
    expect(m.laps[0].startOffsetPx).toBe(0);
    expect(m.laps[1].startOffsetPx).toBeCloseTo(m.laps[0].lengthPx, 5);   // offset = prior length
    expect(m.totalLengthPx).toBeCloseTo(m.laps[0].lengthPx + m.laps[1].lengthPx, 5);
    expect(m.laps[0].lengthPx).toBeGreaterThan(m.laps[1].lengthPx * 2);    // r50 lap >> r20 lap
  });

  it('returns null with no usable points', () => {
    expect(buildCourseModel([], {})).toBeNull();
  });

  it('drops the post-finish coast (a spurious lap N+1) and builds N laps', () => {
    const run = unevenRun(1);                                              // laps 1,2; lapCumMs [1000,2000]
    run.points.push({ t_ms: 2100, cx: 50, cy: 0, score: 1, lap: null });   // post-finish coast -> lapOf = 3
    run.points.push({ t_ms: 2150, cx: 49, cy: 1, score: 1, lap: null });
    const res = buildCourseModel([run, unevenRun(2)], { bins: 64 });
    expect(res).not.toBeNull();                                            // the sparse "lap 3" must not abort the build
    expect(res!.model.laps).toHaveLength(2);                               // only laps 1 and 2
  });

  it('carries a graph|centerline status and keeps cumulative offsets (no-branch fixture -> centerline)', () => {
    const res = buildCourseModel([unevenRun(1), unevenRun(2)], { bins: 64, grid: 64 });
    expect(res).not.toBeNull();
    const m = res!.model;
    expect(['graph', 'centerline']).toContain(m.status);
    expect(m.status).toBe('centerline');                                   // plain circles, no split
    expect(m.laps[1].startOffsetPx).toBeCloseTo(m.laps[0].lengthPx, 5);
    expect(m.totalLengthPx).toBeCloseTo(m.laps[0].lengthPx + m.laps[1].lengthPx, 5);
  });
});

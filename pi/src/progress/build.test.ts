// pi/src/progress/build.test.ts
import { describe, it, expect } from 'vitest';
import { foldRun, fBinCentroids, fitTranslation, buildCourseModel, anchorAtLine, groupByLap, type FoldPt } from './build';
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

describe('buildCourseModel', () => {
  it('builds a centerline graph that closes the loop with monotonic progress', () => {
    const res = buildCourseModel([loopRun(1), loopRun(2, 6, 0)], { bins: 72 });
    expect(res).not.toBeNull();
    const g = res!.graph;
    expect(g.status).toBe('centerline');
    expect(g.edges).toHaveLength(1);
    expect(g.edges[0].pLo).toBe(0);
    expect(g.edges[0].pHi).toBe(1);
    expect(g.edges[0].poly.length).toBeGreaterThan(40);
    expect(g.lapLengthPx).toBeGreaterThan(250);            // ~2*pi*50 ≈ 314
    // player 2 was offset +6 -> its alignment maps roughly -6 back
    const a2 = res!.alignments.find((a) => a.playerId === 2)!;
    expect(a2.transform.dx).toBeCloseTo(-6, 0);
  });

  it('returns null with no usable points', () => {
    expect(buildCourseModel([], {})).toBeNull();
  });
});

describe('anchorAtLine', () => {
  it('rotates a closed centerline so progress 0 starts nearest the line', () => {
    const sq: [number, number][] = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]];
    const a = anchorAtLine(sq, [9, 10]);            // line nearest the [10,10] vertex
    expect(a[0]).toEqual([10, 10]);                 // progress 0 now at that vertex
    expect(a[a.length - 1]).toEqual([10, 10]);      // re-closed
    expect(a.length).toBe(sq.length);               // same vertex count
  });
});

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

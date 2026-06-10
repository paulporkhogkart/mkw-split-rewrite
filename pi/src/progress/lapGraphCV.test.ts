import { describe, it, expect } from 'vitest';
import { buildLapGraphCV } from './lapGraphCV';
import type { FoldPt } from './build';

// Densely sample a polyline (~0.4px spacing) with f rising f0->f1 along its length.
function seg(xy: [number, number][], f0: number, f1: number): FoldPt[] {
  const segLen: number[] = []; let total = 0;
  for (let i = 1; i < xy.length; i++) { const L = Math.hypot(xy[i][0] - xy[i - 1][0], xy[i][1] - xy[i - 1][1]); segLen.push(L); total += L; }
  const out: FoldPt[] = []; let acc = 0;
  for (let i = 1; i < xy.length; i++) {
    const L = segLen[i - 1], n = Math.max(1, Math.ceil(L / 0.4));
    for (let k = 0; k < n; k++) {
      const t = k / n, x = xy[i - 1][0] + (xy[i][0] - xy[i - 1][0]) * t, y = xy[i - 1][1] + (xy[i][1] - xy[i - 1][1]) * t;
      out.push({ x, y, f: f0 + (f1 - f0) * ((acc + t * L) / total), score: 1 });
    }
    acc += L;
  }
  out.push({ x: xy[xy.length - 1][0], y: xy[xy.length - 1][1], f: f1, score: 1 });
  return out;
}

describe('buildLapGraphCV', () => {
  it('a plain loop has no split -> null (caller keeps the centerline)', () => {
    const pts: FoldPt[] = [];
    for (let k = 0; k < 600; k++) { const a = 2 * Math.PI * k / 600; pts.push({ x: 50 + 40 * Math.cos(a), y: 50 + 40 * Math.sin(a), f: k / 600, score: 1 }); }
    expect(buildLapGraphCV(pts, { grid: 80 })).toBeNull();
  });

  it('a fork that rejoins yields a main spine plus two overlapping branch edges', () => {
    const pts = [
      ...seg([[0, 0], [30, 0]], 0, 0.4),
      ...seg([[30, 0], [45, -12], [60, 0]], 0.4, 0.7),   // upper arc
      ...seg([[30, 0], [45, 12], [60, 0]], 0.4, 0.7),    // lower arc
      ...seg([[60, 0], [90, 0]], 0.7, 1),
    ];
    const g = buildLapGraphCV(pts, { grid: 128, splatR: 1 });
    expect(g).not.toBeNull();
    expect(g!.edges.some((e) => e.kind === 'main')).toBe(true);            // centerline backbone
    const branchy = g!.edges.filter((e) => e.kind === 'branch');
    expect(branchy.length).toBeGreaterThanOrEqual(2);
    const [b1, b2] = branchy;
    expect(Math.min(b1.pHi, b2.pHi) - Math.max(b1.pLo, b2.pLo)).toBeGreaterThan(0.1);   // overlapping progress
  });

  it('falls back (null) on a degenerate trail', () => {
    expect(buildLapGraphCV([{ x: 0, y: 0, f: 0, score: 1 }, { x: 0.1, y: 0, f: 1, score: 1 }], {})).toBeNull();
  });
});

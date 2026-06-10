import { describe, it, expect } from 'vitest';
import { rasterize, cellXY } from './raster';
import type { FoldPt } from './build';

const P = (x: number, y: number, f: number): FoldPt => ({ x, y, f, score: 1 });

describe('rasterize', () => {
  it('maps points into cells and recovers mean-f, round-tripping cell centres', () => {
    const pts = [P(0, 0, 0), P(10, 0, 0.5), P(10, 10, 1)];
    const r = rasterize(pts, { grid: 16, splatR: 0 })!;
    expect(r.w).toBeGreaterThan(0); expect(r.h).toBeGreaterThan(0);
    const ci = Math.floor((10 - r.ox) / r.cell), cj = Math.floor((0 - r.oy) / r.cell);
    const idx = cj * r.w + ci;
    expect(r.wGrid[idx]).toBeGreaterThan(0);
    expect(r.fGrid[idx] / r.wGrid[idx]).toBeCloseTo(0.5, 5);     // mean-f recovered
    const [bx, by] = cellXY(r, ci, cj);
    expect(Math.hypot(bx - 10, by - 0)).toBeLessThan(r.cell);    // round-trips within a cell
  });

  it('splatR>0 fills a disk around each point (4-neighbourhood at least)', () => {
    const r = rasterize([P(2, 2, 1), P(8, 8, 1)], { grid: 20, splatR: 1 })!;
    const ci = Math.floor((2 - r.ox) / r.cell), cj = Math.floor((2 - r.oy) / r.cell);
    const on = (i: number, j: number) => r.wGrid[j * r.w + i] > 0;
    expect(on(ci, cj) && on(ci - 1, cj) && on(ci + 1, cj) && on(ci, cj - 1) && on(ci, cj + 1)).toBe(true);
  });

  it('returns null with <2 points or a zero-area spread', () => {
    expect(rasterize([P(1, 1, 0)], {})).toBeNull();              // single point
    expect(rasterize([P(1, 1, 0), P(1, 1, 1)], {})).toBeNull();  // coincident -> zero bbox
  });
});

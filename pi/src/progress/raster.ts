// pi/src/progress/raster.ts
// Score-weighted density raster of a pooled lap trail: each point splats its score into a small
// disk so the sparse trail becomes a connected blob to skeletonise. fGrid carries f*score so the
// mean within-lap fraction per cell (fGrid/wGrid) survives for the graph's progress assignment.
import type { FoldPt } from './build';

export interface Raster {
  w: number; h: number;           // grid dimensions (cells)
  ox: number; oy: number;         // course-coord origin of cell (0,0)'s corner
  cell: number;                   // course px per cell
  wGrid: Float32Array;            // Σ score per cell
  fGrid: Float32Array;            // Σ f·score per cell (mean-f = fGrid/wGrid)
}

/** Rasterise pooled FoldPts. null if there are <2 points or they span no area. */
export function rasterize(pts: FoldPt[], opts: { grid?: number; splatR?: number } = {}): Raster | null {
  if (pts.length < 2) return null;
  const grid = opts.grid ?? 96;
  const splatR = opts.splatR ?? 1;

  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const p of pts) {
    if (p.x < minX) minX = p.x; if (p.y < minY) minY = p.y;
    if (p.x > maxX) maxX = p.x; if (p.y > maxY) maxY = p.y;
  }
  const maxDim = Math.max(maxX - minX, maxY - minY);
  if (!(maxDim > 0)) return null;                       // all points coincident

  const cell = maxDim / grid;
  const margin = (splatR + 1) * cell;
  const ox = minX - margin, oy = minY - margin;
  const w = Math.ceil((maxX + margin - ox) / cell) + 1;
  const h = Math.ceil((maxY + margin - oy) / cell) + 1;
  if (w * h < 4) return null;

  const wGrid = new Float32Array(w * h), fGrid = new Float32Array(w * h);
  const r2 = splatR * splatR + 1e-9;
  for (const p of pts) {
    const ci = Math.floor((p.x - ox) / cell), cj = Math.floor((p.y - oy) / cell);
    for (let dj = -splatR; dj <= splatR; dj++) for (let di = -splatR; di <= splatR; di++) {
      if (di * di + dj * dj > r2) continue;            // disk, not square
      const i = ci + di, j = cj + dj;
      if (i < 0 || j < 0 || i >= w || j >= h) continue;
      const idx = j * w + i;
      wGrid[idx] += p.score;
      fGrid[idx] += p.f * p.score;
    }
  }
  return { w, h, ox, oy, cell, wGrid, fGrid };
}

/** Course-coord centre of cell (ci, cj). */
export function cellXY(r: Raster, ci: number, cj: number): [number, number] {
  return [r.ox + (ci + 0.5) * r.cell, r.oy + (cj + 0.5) * r.cell];
}

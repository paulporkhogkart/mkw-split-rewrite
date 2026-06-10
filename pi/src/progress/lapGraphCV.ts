// pi/src/progress/lapGraphCV.ts
// Detect split paths in one lap's pooled trail and return a branch-aware CourseGraph:
//   rasterize -> threshold -> Zhang-Suen skeleton -> graph extraction -> find PARALLEL edge pairs
//   (overlapping in within-lap fraction f AND spatially separated) = branches.
// The backbone stays the f-bin centerline (covers [0,1], handles the start/finish seam exactly as
// the shipped model does); detected branch polylines are grafted on over their f-intervals. So this
// is strictly additive: no branches -> null (caller keeps the plain centerline); branches -> the
// projector picks the nearest path, overriding the averaged middle line where runs diverge.
import { centerline, type FoldPt } from './build';
import type { CourseGraph, GraphEdge } from './types';
import { rasterize, cellXY } from './raster';
import { threshold, zhangSuen } from './skeleton';
import { extractGraph } from './graphExtract';

const THR_FRAC = 0.12;          // binarise above frac*max
const MIN_SKEL = 8;             // too few skeleton pixels -> nothing to extract
const BRANCH_OVERLAP = 0.4;     // min f-range overlap (of the shorter) for two edges to be parallel
const SEP_FRAC = 0.06;          // min spatial separation (fraction of course max-dim) for a parallel pair
const MAX_VERTS = 40;           // decimate edge polylines
const DEF_BINS = 180;

function arcLen(poly: [number, number][]): number {
  let s = 0;
  for (let i = 1; i < poly.length; i++) s += Math.hypot(poly[i][0] - poly[i - 1][0], poly[i][1] - poly[i - 1][1]);
  return s;
}
function decimate(poly: [number, number][], maxV: number): [number, number][] {
  if (poly.length <= maxV) return poly;
  const step = poly.length / maxV, out: [number, number][] = [];
  for (let k = 0; k < maxV; k++) out.push(poly[Math.floor(k * step)]);
  out.push(poly[poly.length - 1]);
  return out;
}

type Cand = { poly: [number, number][]; arcLen: number; fLo: number; fHi: number; cx: number; cy: number };

export function buildLapGraphCV(
  pts: FoldPt[],
  opts: { grid?: number; splatR?: number; thrFrac?: number; bins?: number } = {},
): CourseGraph | null {
  const r = rasterize(pts, { grid: opts.grid, splatR: opts.splatR });
  if (!r) return null;
  const skel = zhangSuen(threshold(r, opts.thrFrac ?? THR_FRAC), r.w, r.h);
  let nSkel = 0; for (const v of skel) nSkel += v;
  if (nSkel < MIN_SKEL) return null;
  const raw = extractGraph(skel, r.w, r.h);
  if (raw.edges.length < 2) return null;                 // a lone edge can't have a parallel branch

  // Candidate edges: course-coord polyline, f-range (cell mean-f), spatial centroid.
  const cands: Cand[] = [];
  for (const e of raw.edges) {
    const fs: number[] = [];
    for (const [ci, cj] of e.cells) { const k = cj * r.w + ci; if (r.wGrid[k] > 0) fs.push(r.fGrid[k] / r.wGrid[k]); }
    if (fs.length === 0) continue;
    const poly = decimate(e.cells.map(([ci, cj]) => cellXY(r, ci, cj)) as [number, number][], MAX_VERTS);
    let cx = 0, cy = 0; for (const p of poly) { cx += p[0]; cy += p[1]; } cx /= poly.length; cy /= poly.length;
    cands.push({ poly, arcLen: arcLen(poly), fLo: Math.max(0, Math.min(...fs)), fHi: Math.min(1, Math.max(...fs)), cx, cy });
  }
  if (cands.length < 2) return null;

  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const p of pts) { if (p.x < minX) minX = p.x; if (p.y < minY) minY = p.y; if (p.x > maxX) maxX = p.x; if (p.y > maxY) maxY = p.y; }
  const sep = SEP_FRAC * Math.max(maxX - minX, maxY - minY);

  // Branch pair = two edges that overlap in f (parallel in time) AND are spatially apart (different routes).
  const isBranch = new Array(cands.length).fill(false);
  for (let i = 0; i < cands.length; i++) for (let j = i + 1; j < cands.length; j++) {
    const a = cands[i], b = cands[j];
    const overlap = Math.min(a.fHi, b.fHi) - Math.max(a.fLo, b.fLo);
    const shorter = Math.min(a.fHi - a.fLo, b.fHi - b.fLo) || 1e-9;
    if (overlap > BRANCH_OVERLAP * shorter && Math.hypot(a.cx - b.cx, a.cy - b.cy) > sep) { isBranch[i] = true; isBranch[j] = true; }
  }
  const branches = cands.filter((_, i) => isBranch[i]);
  if (branches.length < 2) return null;                  // no real split -> caller uses the centerline

  // Backbone = the f-bin centerline (seam-safe, covers [0,1]); graft the branch polylines on.
  const cl = centerline(pts, opts.bins ?? DEF_BINS);
  if (cl.length < 2) return null;
  const mainLen = arcLen(cl);
  const edges: GraphEdge[] = [
    { id: 0, a: 0, b: 0, poly: cl, arcLen: mainLen, pLo: 0, pHi: 1, kind: 'main', passThrough: null },
    ...branches.map((b, i): GraphEdge => ({ id: i + 1, a: 0, b: 0, poly: b.poly, arcLen: b.arcLen, pLo: b.fLo, pHi: b.fHi, kind: 'branch', passThrough: null })),
  ];
  return {
    version: 1, startNode: 0, lapLengthPx: mainLen, status: 'graph',
    nodes: [{ id: 0, x: cl[0][0], y: cl[0][1], progress: 0 }],
    edges,
  };
}

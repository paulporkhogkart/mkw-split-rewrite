// pi/src/progress/build.ts
import type { RunInput, Transform, CourseGraph, GraphEdge, GraphNode } from './types';

export interface FoldPt { x: number; y: number; f: number; score: number; }

/** Lap (1-based) of a timestamp from cumulative lap end-times. */
function lapOf(t: number, cum: number[]): number {
  let L = 1;
  for (const b of cum) { if (t >= b) L++; else break; }
  return L;
}

/** Fold every lap of a run into one lap: each point -> (x, y, in-lap fraction f, score). */
export function foldRun(run: RunInput): FoldPt[] {
  const cum = run.lapCumMs;
  const out: FoldPt[] = [];
  for (const p of run.points) {
    const lap = p.lap ?? lapOf(p.t_ms, cum);
    const lo = lap >= 2 ? (cum[lap - 2] ?? 0) : 0;
    const hi = cum[lap - 1] ?? (cum[cum.length - 1] ?? lo + 1);
    const span = hi - lo;
    const f = span > 0 ? Math.min(0.999999, Math.max(0, (p.t_ms - lo) / span)) : 0;
    out.push({ x: p.cx, y: p.cy, f, score: p.score });
  }
  return out;
}

/** Score-weighted centroid per f-bin; null for empty bins. Length = bins. */
export function fBinCentroids(pts: FoldPt[], bins: number): ([number, number] | null)[] {
  const sx = new Array(bins).fill(0), sy = new Array(bins).fill(0), sw = new Array(bins).fill(0);
  for (const p of pts) {
    const b = Math.min(bins - 1, Math.floor(p.f * bins));
    sx[b] += p.x * p.score; sy[b] += p.y * p.score; sw[b] += p.score;
  }
  return sx.map((_, b) => (sw[b] > 0 ? [sx[b] / sw[b], sy[b] / sw[b]] : null));
}

/** Least-squares translation mapping `from` centroids onto `ref` centroids (shared bins only). */
export function fitTranslation(ref: ([number, number] | null)[], from: ([number, number] | null)[]): Transform {
  let dx = 0, dy = 0, n = 0;
  for (let b = 0; b < Math.min(ref.length, from.length); b++) {
    const r = ref[b], g = from[b];
    if (r && g) { dx += r[0] - g[0]; dy += r[1] - g[1]; n++; }
  }
  return { dx: n ? dx / n : 0, dy: n ? dy / n : 0, scale: 1 };
}

export function applyTransform(p: FoldPt, t: Transform): FoldPt {
  return { x: p.x * t.scale + t.dx, y: p.y * t.scale + t.dy, f: p.f, score: p.score };
}

const DEF_BINS = 180;

function arcLen(poly: [number, number][]): number {
  let s = 0;
  for (let i = 1; i < poly.length; i++) s += Math.hypot(poly[i][0] - poly[i - 1][0], poly[i][1] - poly[i - 1][1]);
  return s;
}

/** Ordered cyclic centerline from merged points: score-weighted centroid per f-bin. */
export function centerline(pts: FoldPt[], bins: number): [number, number][] {
  const c = fBinCentroids(pts, bins).filter((p): p is [number, number] => p != null);
  if (c.length >= 2) c.push(c[0]);          // close the loop
  return c;
}

export interface BuildResult {
  graph: CourseGraph;
  alignments: { playerId: number; transform: Transform }[];
}

/** Build a centerline CourseGraph from a set of runs + per-player alignment transforms. */
export function buildCourseModel(runs: RunInput[], opts: { bins?: number } = {}): BuildResult | null {
  const bins = opts.bins ?? DEF_BINS;
  const folded = runs.map(foldRun);
  if (folded.length === 0 || folded.every((f) => f.length === 0)) return null;

  // Reference = densest run; align the rest to it by f-binned centroids.
  let refIdx = 0;
  for (let i = 1; i < folded.length; i++) if (folded[i].length > folded[refIdx].length) refIdx = i;
  const refC = fBinCentroids(folded[refIdx], Math.min(bins, 32));

  const perRun: Transform[] = folded.map((f, i) =>
    i === refIdx ? { dx: 0, dy: 0, scale: 1 } : fitTranslation(refC, fBinCentroids(f, Math.min(bins, 32))));

  const merged: FoldPt[] = [];
  folded.forEach((f, i) => { for (const p of f) merged.push(applyTransform(p, perRun[i])); });

  const poly = centerline(merged, bins);
  if (poly.length < 3) return null;

  const lapLen = arcLen(poly);
  const node: GraphNode = { id: 0, x: poly[0][0], y: poly[0][1], progress: 0 };
  const edge: GraphEdge = { id: 0, a: 0, b: 0, poly, arcLen: lapLen, pLo: 0, pHi: 1, kind: 'main', passThrough: null };
  const graph: CourseGraph = { version: 1, startNode: 0, lapLengthPx: lapLen, nodes: [node], edges: [edge], status: 'centerline' };

  // Per-player transform = mean of that player's runs' transforms.
  const byPlayer = new Map<number, { dx: number; dy: number; n: number }>();
  runs.forEach((r, i) => {
    const cur = byPlayer.get(r.playerId) ?? { dx: 0, dy: 0, n: 0 };
    cur.dx += perRun[i].dx; cur.dy += perRun[i].dy; cur.n++; byPlayer.set(r.playerId, cur);
  });
  const alignments = [...byPlayer.entries()].map(([playerId, v]) =>
    ({ playerId, transform: { dx: v.dx / v.n, dy: v.dy / v.n, scale: 1 } as Transform }));

  return { graph, alignments };
}

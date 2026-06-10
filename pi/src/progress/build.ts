// pi/src/progress/build.ts
import type { RunInput, Transform, CourseGraph, GraphEdge, GraphNode, CourseModel, LapRoute } from './types';

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

/** Ordered centerline (open) from points: score-weighted centroid per f-bin. */
export function centerline(pts: FoldPt[], bins: number): [number, number][] {
  return fBinCentroids(pts, bins).filter((p): p is [number, number] => p != null);
}

export interface BuildResult {
  model: CourseModel;
  alignments: { playerId: number; transform: Transform }[];
}

function lapGraph(poly: [number, number][]): { graph: CourseGraph; lengthPx: number } {
  const lengthPx = arcLen(poly);
  const node: GraphNode = { id: 0, x: poly[0][0], y: poly[0][1], progress: 0 };
  const edge: GraphEdge = { id: 0, a: 0, b: 0, poly, arcLen: lengthPx, pLo: 0, pHi: 1, kind: 'main', passThrough: null };
  const graph: CourseGraph = { version: 1, startNode: 0, lapLengthPx: lengthPx, nodes: [node], edges: [edge], status: 'centerline' };
  return { graph, lengthPx };
}

export function buildCourseModel(runs: RunInput[], opts: { bins?: number } = {}): BuildResult | null {
  const bins = opts.bins ?? DEF_BINS;
  const grouped = runs.map(groupByLap);
  // The real lap count; points after the final lap (post-finish coast) get a spurious lap N+1 from
  // the time-derived lapOf — cap at N so that sparse trailing "lap" can't abort the whole build.
  const N = Math.max(0, ...runs.map((r) => r.lapCumMs.length));
  const lapIndices = [...new Set(grouped.flatMap((g) => [...g.keys()]))]
    .filter((k) => N === 0 || k <= N)
    .sort((a, b) => a - b);
  if (lapIndices.length === 0) return null;

  // Per-player alignment estimated once on lap 1 (cheap; the live frame is one capture).
  const perRunTransform: Transform[] = grouped.map(() => ({ dx: 0, dy: 0, scale: 1 }));
  {
    const lap1 = grouped.map((g) => g.get(lapIndices[0]) ?? []);
    let refIdx = 0;
    for (let i = 1; i < lap1.length; i++) if (lap1[i].length > lap1[refIdx].length) refIdx = i;
    const refC = fBinCentroids(lap1[refIdx], Math.min(bins, 32));
    lap1.forEach((f, i) => { if (i !== refIdx && f.length) perRunTransform[i] = fitTranslation(refC, fBinCentroids(f, Math.min(bins, 32))); });
  }

  const laps: LapRoute[] = [];
  let offset = 0;
  for (const k of lapIndices) {
    const merged: FoldPt[] = [];
    grouped.forEach((g, i) => { for (const p of g.get(k) ?? []) merged.push(applyTransform(p, perRunTransform[i])); });
    const poly = centerline(merged, bins);
    if (poly.length < 3) return null;
    const { graph, lengthPx } = lapGraph(poly);
    laps.push({ index: k, lengthPx, startOffsetPx: offset, graph });
    offset += lengthPx;
  }

  const model: CourseModel = { version: 2, totalLengthPx: offset, laps, status: 'centerline' };

  const byPlayer = new Map<number, { dx: number; dy: number; n: number }>();
  runs.forEach((r, i) => {
    const cur = byPlayer.get(r.playerId) ?? { dx: 0, dy: 0, n: 0 };
    cur.dx += perRunTransform[i].dx; cur.dy += perRunTransform[i].dy; cur.n++; byPlayer.set(r.playerId, cur);
  });
  const alignments = [...byPlayer.entries()].map(([playerId, v]) =>
    ({ playerId, transform: { dx: v.dx / v.n, dy: v.dy / v.n, scale: 1 } as Transform }));

  return { model, alignments };
}

/** Split a run into per-lap-index point lists, each tagged with that lap's within-lap fraction f. */
export function groupByLap(run: RunInput): Map<number, FoldPt[]> {
  const cum = run.lapCumMs;
  const lapOfT = (t: number) => { let L = 1; for (const b of cum) { if (t >= b) L++; else break; } return L; };
  const out = new Map<number, FoldPt[]>();
  for (const p of run.points) {
    const lap = p.lap ?? lapOfT(p.t_ms);
    const lo = lap >= 2 ? (cum[lap - 2] ?? 0) : 0;
    const hi = cum[lap - 1] ?? (cum[cum.length - 1] ?? lo + 1);
    const span = hi - lo;
    const f = span > 0 ? Math.min(0.999999, Math.max(0, (p.t_ms - lo) / span)) : 0;
    if (!out.has(lap)) out.set(lap, []);
    out.get(lap)!.push({ x: p.cx, y: p.cy, f, score: p.score });
  }
  return out;
}

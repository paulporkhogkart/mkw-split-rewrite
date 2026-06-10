// pi/src/progress/build.ts
import type { RunInput, Transform, CourseGraph } from './types';

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

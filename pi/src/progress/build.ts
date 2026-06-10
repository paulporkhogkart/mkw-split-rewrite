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

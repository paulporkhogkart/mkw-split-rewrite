export interface RefPt { cx: number; cy: number; s: number; t: number; }

const dist = (ax: number, ay: number, bx: number, by: number) => Math.hypot(ax - bx, ay - by);

/** Arc-length-normalised reference path from time-ordered trail points (s in [0,1]). */
export function buildReference(points: { cx: number; cy: number; t_ms: number }[]): RefPt[] {
  if (points.length === 0) return [];
  const out: RefPt[] = [{ cx: points[0].cx, cy: points[0].cy, s: 0, t: points[0].t_ms }];
  let acc = 0;
  for (let i = 1; i < points.length; i++) {
    acc += dist(points[i - 1].cx, points[i - 1].cy, points[i].cx, points[i].cy);
    out.push({ cx: points[i].cx, cy: points[i].cy, s: acc, t: points[i].t_ms });
  }
  const total = acc || 1;
  for (const p of out) p.s /= total;
  return out;
}

/** Route fraction at the end of each lap (S_k), from cumulative lap end-times. Length = laps. */
export function lapBoundaries(ref: RefPt[], cumulativeLapMs: number[]): number[] {
  return cumulativeLapMs.map((t) => {
    let best = Infinity, bestS = 0;
    for (const p of ref) { const d = Math.abs(p.t - t); if (d < best) { best = d; bestS = p.s; } }
    return bestS;
  });
}

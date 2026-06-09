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

export interface Reference { ref: RefPt[]; bounds: number[]; totalLen: number; }

const RESAMPLE_SPACING = 5;     // px between resampled reference vertices
const TELEPORT_CLIP_FACTOR = 8; // segment > factor x median => drop the endpoint

function dedup(p: { cx: number; cy: number; t_ms: number }[]) {
  if (p.length === 0) return p;
  const out = [p[0]];
  for (let i = 1; i < p.length; i++) if (dist(out[out.length - 1].cx, out[out.length - 1].cy, p[i].cx, p[i].cy) > 1e-6) out.push(p[i]);
  return out;
}

function clipTeleports(p: { cx: number; cy: number; t_ms: number }[]) {
  if (p.length < 3) return p;
  const seg: number[] = [];
  for (let i = 1; i < p.length; i++) seg.push(dist(p[i - 1].cx, p[i - 1].cy, p[i].cx, p[i].cy));
  const sorted = [...seg].sort((a, b) => a - b);
  const med = sorted[Math.floor((sorted.length - 1) / 2)] || 0;
  if (med <= 0) return p;
  const out = [p[0]];
  for (let i = 1; i < p.length; i++) {
    const last = out[out.length - 1];
    if (dist(last.cx, last.cy, p[i].cx, p[i].cy) <= TELEPORT_CLIP_FACTOR * med) out.push(p[i]);
  }
  return out;
}

function resample(raw: RefPt[], spacingPx: number, totalLen: number): RefPt[] {
  if (raw.length < 2 || totalLen <= 0) return raw.slice();
  const stepS = spacingPx / totalLen;
  const out: RefPt[] = [raw[0]];
  let nextS = stepS;
  for (let i = 1; i < raw.length; i++) {
    const a = raw[i - 1], b = raw[i];
    while (nextS < b.s) {
      const f = b.s !== a.s ? (nextS - a.s) / (b.s - a.s) : 0;
      out.push({ cx: a.cx + f * (b.cx - a.cx), cy: a.cy + f * (b.cy - a.cy), s: nextS, t: a.t + f * (b.t - a.t) });
      nextS += stepS;
    }
  }
  out.push(raw[raw.length - 1]);
  return out;
}

/** Clean + arc-length-normalise + resample a trail; bounds computed on the raw (timed) path. */
export function prepareReference(points: { cx: number; cy: number; t_ms: number }[], lapCumMs: number[]): Reference {
  const cleaned = clipTeleports(dedup(points));
  const raw = buildReference(cleaned);
  if (raw.length === 0) return { ref: [], bounds: [], totalLen: 0 };
  let totalLen = 0;
  for (let i = 1; i < cleaned.length; i++) totalLen += dist(cleaned[i - 1].cx, cleaned[i - 1].cy, cleaned[i].cx, cleaned[i].cy);
  const bounds = lapBoundaries(raw, lapCumMs);
  const ref = totalLen > 0 ? resample(raw, RESAMPLE_SPACING, totalLen) : raw;
  return { ref, bounds, totalLen: totalLen || 1 };
}

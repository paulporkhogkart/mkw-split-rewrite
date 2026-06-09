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

export type ProjState = { s: number; t: number; x: number; y: number } | null;
export interface Obs { x: number; y: number; lap: number; t: number; stale: boolean; }

const HEADING_BONUS = 6;        // px-equivalent bonus for a heading-aligned branch at bootstrap

/** Nearest point on the polyline, restricted to s in [loS,hiS] (projections clamped into-window).
 *  When useH, a heading-aligned segment tangent discounts the cost (bootstrap tie-break). */
function nearestOnPath(ref: RefPt[], loS: number, hiS: number, px: number, py: number, hx = 0, hy = 0, useH = false) {
  let best = Infinity, bestS = loS;
  for (let i = 1; i < ref.length; i++) {
    const a = ref[i - 1], b = ref[i];
    if (Math.max(a.s, b.s) < loS || Math.min(a.s, b.s) > hiS) continue;
    const dx = b.cx - a.cx, dy = b.cy - a.cy, L2 = dx * dx + dy * dy;
    let t = L2 > 0 ? ((px - a.cx) * dx + (py - a.cy) * dy) / L2 : 0;
    t = Math.max(0, Math.min(1, t));
    let s = a.s + t * (b.s - a.s);
    const sC = Math.max(loS, Math.min(hiS, s));
    if (sC !== s) { t = b.s !== a.s ? Math.max(0, Math.min(1, (sC - a.s) / (b.s - a.s))) : 0; s = sC; }
    const x = a.cx + t * dx, y = a.cy + t * dy;
    let cost = dist(px, py, x, y);
    if (useH) { const tl = Math.hypot(dx, dy) || 1; cost -= HEADING_BONUS * Math.max(0, (dx / tl) * hx + (dy / tl) * hy); }
    if (cost < best) { best = cost; bestS = s; }
  }
  return { s: bestS, dist: best };
}

/** Project one observation onto the route, carrying per-player state. */
export function step(state: ProjState, ref: Reference, obs: Obs): { state: ProjState; s: number | null } {
  if (ref.ref.length === 0) return { state, s: state ? state.s : null };
  const b = ref.bounds;
  const loS = obs.lap >= 2 ? (b[obs.lap - 2] ?? 0) : 0;
  const hiS = (obs.lap - 1) < b.length ? b[obs.lap - 1] : 1;

  // bootstrap: no heading on a truly fresh state
  const r = nearestOnPath(ref.ref, loS, hiS, obs.x, obs.y);
  const s = Math.min(hiS, Math.max(loS, r.s));
  return { state: { s, t: obs.t, x: obs.x, y: obs.y }, s };
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

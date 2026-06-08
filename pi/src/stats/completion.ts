import type { DatabaseSync } from 'node:sqlite';
import type { Dimension, Period, StatResult, StatRow } from './types';
import { getMetric } from './metrics';

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

/** Nearest-vertex route fraction for P, restricted to vertices with s >= lowerS. */
export function completionFraction(ref: RefPt[], lowerS: number, px: number, py: number): number {
  let best = Infinity, bestS = lowerS;
  for (const p of ref) {
    if (p.s < lowerS) continue;
    const d = dist(p.cx, p.cy, px, py);
    if (d < best) { best = d; bestS = p.s; }
  }
  return bestS;
}

export interface CompletionQuery {
  metric: string;
  period: Period;
  filters: Partial<Record<Dimension, string>>;
  groupBy?: Dimension;
  seasonId: number;
}

function playerId(db: DatabaseSync, name: string): number | null {
  const r = db.prepare('SELECT id FROM players WHERE display_name=? COLLATE NOCASE').get(name) as { id: number } | undefined;
  return r?.id ?? null;
}
function courseId(db: DatabaseSync, v: string): number | null {
  const r = db.prepare('SELECT id FROM courses WHERE slug=? OR display_name=? COLLATE NOCASE').get(v, v) as { id: number } | undefined;
  return r?.id ?? null;
}
function nameOf(db: DatabaseSync, table: 'players' | 'courses', id: number): string {
  return (db.prepare(`SELECT display_name FROM ${table} WHERE id=?`).get(id) as { display_name: string }).display_name;
}

export interface RefEntry { ref: RefPt[]; bounds: number[]; }

/** The per-course completion reference: the densest finished run's trail (arc-length
 *  normalised) + its per-lap boundary fractions. null if no finished run with a trail. */
export function courseReference(db: DatabaseSync, seasonId: number, courseId: number, cc: number): RefEntry | null {
  const refRun = db.prepare(
    `SELECT r.id, COUNT(p.run_id) AS n FROM runs r JOIN run_points p ON p.run_id=r.id
     WHERE r.season_id=? AND r.course_id=? AND r.cc=? AND r.status='finished'
     GROUP BY r.id ORDER BY n DESC LIMIT 1`).get(seasonId, courseId, cc) as { id: number; n: number } | undefined;
  if (!refRun) return null;
  const pts = db.prepare('SELECT cx, cy, t_ms FROM run_points WHERE run_id=? ORDER BY t_ms').all(refRun.id) as { cx: number; cy: number; t_ms: number }[];
  const ref = buildReference(pts);
  const laps = db.prepare('SELECT lap_time_ms FROM run_laps WHERE run_id=? ORDER BY lap_index').all(refRun.id) as { lap_time_ms: number }[];
  let cum = 0; const cumMs = laps.map((l) => (cum += l.lap_time_ms));
  return { ref, bounds: lapBoundaries(ref, cumMs) };
}

export function resolveCompletion(db: DatabaseSync, q: CompletionQuery): StatResult {
  const m = getMetric(q.metric);
  if (!m || m.kind !== 'completion') throw new Error(`not a completion metric: ${q.metric}`);
  const cc = q.filters.cc != null ? Number(q.filters.cc) : 150;
  const pid = q.filters.player ? playerId(db, q.filters.player) : null;
  const cid = q.filters.course ? courseId(db, q.filters.course) : null;

  const where = ['r.season_id=?', "r.status='reset'", 'r.cc=?'];
  const params: unknown[] = [q.seasonId, cc];
  if (pid != null) { where.push('r.player_id=?'); params.push(pid); }
  if (cid != null) { where.push('r.course_id=?'); params.push(cid); }
  if (q.period.startUtc) { where.push('datetime(r.ended_at) >= ?'); params.push(q.period.startUtc); }
  if (q.period.endUtc) { where.push('datetime(r.ended_at) < ?'); params.push(q.period.endUtc); }
  const resets = db.prepare(`SELECT r.id, r.player_id, r.course_id FROM runs r WHERE ${where.join(' AND ')}`)
    .all(...params) as { id: number; player_id: number; course_id: number }[];

  const refCache = new Map<number, RefEntry | null>();
  const getRef = (courseIdv: number): RefEntry | null => {
    if (refCache.has(courseIdv)) return refCache.get(courseIdv)!;
    const entry = courseReference(db, q.seasonId, courseIdv, cc);
    refCache.set(courseIdv, entry);
    return entry;
  };

  const lastPt = db.prepare('SELECT cx, cy FROM run_points WHERE run_id=? ORDER BY t_ms DESC LIMIT 1');
  const lapCount = db.prepare('SELECT COUNT(*) AS n FROM run_laps WHERE run_id=?');

  const byKey = new Map<string, { sum: number; n: number }>();
  let overallSum = 0, overallN = 0, unevaluable = 0;
  const keyOf = (r: { player_id: number; course_id: number }) =>
    q.groupBy === 'player' ? nameOf(db, 'players', r.player_id)
      : q.groupBy === 'course' ? nameOf(db, 'courses', r.course_id)
        : q.metric;

  for (const reset of resets) {
    const entry = getRef(reset.course_id);
    const last = lastPt.get(reset.id) as { cx: number; cy: number } | undefined;
    if (!entry || entry.ref.length === 0 || !last) { unevaluable++; continue; }
    const L = (lapCount.get(reset.id) as { n: number }).n;
    const lowerS = L > 0 && L <= entry.bounds.length ? entry.bounds[L - 1] : 0;
    const frac = completionFraction(entry.ref, lowerS, last.cx, last.cy);
    overallSum += frac; overallN += 1;
    const k = keyOf(reset);
    const cur = byKey.get(k) ?? { sum: 0, n: 0 };
    cur.sum += frac; cur.n += 1; byKey.set(k, cur);
  }

  const total = overallN ? overallSum / overallN : null;
  const rows: StatRow[] = q.groupBy
    ? [...byKey.entries()].map(([key, v]) => ({ key, value: v.n ? v.sum / v.n : null })).sort((a, b) => (b.value ?? 0) - (a.value ?? 0))
    : [{ key: q.metric, value: total }];

  return {
    metric: q.metric,
    period: { key: q.period.key, tz: q.period.tz, start: q.period.startIso, end: q.period.endIso },
    filters: q.filters as Record<string, string>,
    group_by: q.groupBy,
    rows, total, unevaluable,
  };
}

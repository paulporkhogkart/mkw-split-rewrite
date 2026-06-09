import type { DatabaseSync } from 'node:sqlite';
import type { Dimension, Period, StatResult, StatRow } from './types';
import { getMetric } from './metrics';
import { prepareReference, step, type Reference, type ProjState } from './progress';

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

/** Per-course completion reference: the densest finished run's prepared trail + lap bounds. */
export function courseReference(db: DatabaseSync, seasonId: number, courseId: number, cc: number): Reference | null {
  const refRun = db.prepare(
    `SELECT r.id, COUNT(p.run_id) AS n FROM runs r JOIN run_points p ON p.run_id=r.id
     WHERE r.season_id=? AND r.course_id=? AND r.cc=? AND r.status='finished'
     GROUP BY r.id ORDER BY n DESC LIMIT 1`).get(seasonId, courseId, cc) as { id: number; n: number } | undefined;
  if (!refRun) return null;
  const pts = db.prepare('SELECT cx, cy, t_ms FROM run_points WHERE run_id=? ORDER BY t_ms').all(refRun.id) as { cx: number; cy: number; t_ms: number }[];
  const laps = db.prepare('SELECT lap_time_ms FROM run_laps WHERE run_id=? ORDER BY lap_index').all(refRun.id) as { lap_time_ms: number }[];
  let cum = 0; const cumMs = laps.map((l) => (cum += l.lap_time_ms));
  return prepareReference(pts, cumMs);
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

  const refCache = new Map<number, Reference | null>();
  const getRef = (courseIdv: number): Reference | null => {
    if (refCache.has(courseIdv)) return refCache.get(courseIdv)!;
    const entry = courseReference(db, q.seasonId, courseIdv, cc);
    refCache.set(courseIdv, entry);
    return entry;
  };

  const ptsStmt = db.prepare('SELECT cx, cy, t_ms FROM run_points WHERE run_id=? ORDER BY t_ms');
  const lapsStmt = db.prepare('SELECT lap_time_ms FROM run_laps WHERE run_id=? ORDER BY lap_index');

  const byKey = new Map<string, { sum: number; n: number }>();
  let overallSum = 0, overallN = 0, unevaluable = 0;
  const keyOf = (r: { player_id: number; course_id: number }) =>
    q.groupBy === 'player' ? nameOf(db, 'players', r.player_id)
      : q.groupBy === 'course' ? nameOf(db, 'courses', r.course_id)
        : q.metric;

  for (const reset of resets) {
    const entry = getRef(reset.course_id);
    const pts = ptsStmt.all(reset.id) as { cx: number; cy: number; t_ms: number }[];
    if (!entry || entry.ref.length === 0 || pts.length === 0) { unevaluable++; continue; }
    const laps = lapsStmt.all(reset.id) as { lap_time_ms: number }[];
    let c = 0; const cum = laps.map((l) => (c += l.lap_time_ms));
    const lapOf = (t: number) => { let L = 1; for (const bnd of cum) { if (t >= bnd) L++; else break; } return L; };
    let st: ProjState = null, frac = 0;
    for (const p of pts) {
      const r = step(st, entry, { x: p.cx, y: p.cy, lap: lapOf(p.t_ms), t: p.t_ms, stale: false });
      st = r.state; if (r.s != null) frac = r.s;
    }
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

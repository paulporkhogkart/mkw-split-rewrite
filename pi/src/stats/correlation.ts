import type { DatabaseSync } from 'node:sqlite';
import type { Period } from './types';
import { BODY_SOURCE_COLUMNS, PORKER_MAP, presentPorkerTables } from './body';

export interface Correlation { n: number; r: number | null; slope: number | null; intercept: number | null; }

/** Pearson r + least-squares line (y on x) over the pairs. Nulls when n<2 or x has no variance. */
export function pearson(pairs: [number, number][]): Correlation {
  const n = pairs.length;
  if (n < 2) return { n, r: null, slope: null, intercept: null };
  let sx = 0, sy = 0, sxx = 0, syy = 0, sxy = 0;
  for (const [x, y] of pairs) { sx += x; sy += y; sxx += x * x; syy += y * y; sxy += x * y; }
  const cov = n * sxy - sx * sy;
  const vx = n * sxx - sx * sx;
  const vy = n * syy - sy * sy;
  const denom = Math.sqrt(vx * vy);
  const r = denom === 0 ? null : cov / denom;
  const slope = vx === 0 ? null : cov / vx;
  const intercept = slope == null ? null : (sy - slope * sx) / n;
  return { n, r, slope, intercept };
}

export interface CorrelationQuery {
  body: string; player: string; course: string; period: Period; seasonId: number; cc?: number;
}
export interface CorrelationResult {
  body: string; filters: { player: string; course: string };
  period: { key: string; tz: string; start: string | null; end: string | null };
  n: number; r: number | null; slope: number | null; intercept: number | null;
}

/** Correlate a player's finish times on one course against their body metric as of each run. */
export function resolveCorrelation(mkw: DatabaseSync, porker: DatabaseSync, q: CorrelationQuery): CorrelationResult {
  const col = BODY_SOURCE_COLUMNS[q.body];
  if (!col) throw new Error(`unknown body metric: ${q.body}`);
  const cc = q.cc ?? 150;
  const player = mkw.prepare('SELECT id, display_name FROM players WHERE display_name=? COLLATE NOCASE').get(q.player) as { id: number; display_name: string } | undefined;
  if (!player) throw new Error(`unknown player: ${q.player}`);
  const course = mkw.prepare('SELECT id FROM courses WHERE slug=? OR display_name=? COLLATE NOCASE').get(q.course, q.course) as { id: number } | undefined;
  if (!course) throw new Error(`unknown course: ${q.course}`);

  const where = ['season_id=?', 'player_id=?', 'course_id=?', 'cc=?', "status='finished'", 'total_time_ms IS NOT NULL'];
  const params: unknown[] = [q.seasonId, player.id, course.id, cc];
  if (q.period.startUtc) { where.push('datetime(ended_at) >= ?'); params.push(q.period.startUtc); }
  if (q.period.endUtc) { where.push('datetime(ended_at) < ?'); params.push(q.period.endUtc); }
  const runs = mkw.prepare(
    `SELECT total_time_ms AS y, CAST(strftime('%s', datetime(ended_at)) AS INTEGER) AS ep FROM runs WHERE ${where.join(' AND ')}`
  ).all(...params) as { y: number; ep: number }[];

  const table = PORKER_MAP.find((m) => m.player.toLowerCase() === player.display_name.toLowerCase())?.table;
  const present = new Set(presentPorkerTables(porker).map((t) => t.table));
  const pairs: [number, number][] = [];
  if (table && present.has(table)) {
    const asof = porker.prepare(`SELECT "${col}" AS v FROM "${table}" WHERE "Timestamp" <= ? ORDER BY "Timestamp" DESC LIMIT 1`);
    for (const run of runs) {
      const m = asof.get(run.ep) as { v: number | null } | undefined;
      if (m && m.v != null) pairs.push([m.v, run.y]);
    }
  }

  return {
    body: q.body, filters: { player: q.player, course: q.course },
    period: { key: q.period.key, tz: q.period.tz, start: q.period.startIso, end: q.period.endIso },
    ...pearson(pairs),
  };
}

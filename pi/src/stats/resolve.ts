import type { DatabaseSync } from 'node:sqlite';
import type { Dimension, Period, StatResult, StatRow } from './types';
import { getMetric, type RaceMetric } from './metrics';

export interface RaceQuery {
  metric: string;
  period: Period;
  filters: Partial<Record<Dimension, string>>;
  groupBy?: Dimension;
  seasonId: number;
  cc?: number;
  /** Optional pre-built body-condition predicate (Task 6). */
  bodyConditionSql?: { join: string; where: string; params: unknown[] };
}

const POINTS_JOIN = 'LEFT JOIN (SELECT run_id, MAX(t_ms) AS maxt FROM run_points GROUP BY run_id) pt ON pt.run_id = r.id';
const LAPS_JOIN = 'JOIN run_laps rl ON rl.run_id = r.id';

/** SQL expression + label join for a group-by dimension. */
function groupExpr(dim: Dimension): { select: string; join: string } {
  switch (dim) {
    case 'course': return { select: 'c.display_name', join: 'JOIN courses c ON c.id = r.course_id' };
    case 'player': return { select: 'p.display_name', join: 'JOIN players p ON p.id = r.player_id' };
    case 'character': return { select: 'r.character', join: '' };
    case 'kart': return { select: 'r.kart', join: '' };
    case 'costume': return { select: 'r.costume', join: '' };
    case 'cc': return { select: 'CAST(r.cc AS TEXT)', join: '' };
    default: throw new Error(`race metrics cannot group by ${dim}`);
  }
}

/** Filter dimension -> (sql fragment, bound param). */
function filterClause(db: DatabaseSync, dim: Dimension, value: string): { sql: string; param: unknown } {
  switch (dim) {
    case 'course': {
      const row = db.prepare('SELECT id FROM courses WHERE slug=? OR display_name=? COLLATE NOCASE').get(value, value) as { id: number } | undefined;
      return { sql: 'r.course_id=?', param: row?.id ?? -1 };
    }
    case 'player': {
      const row = db.prepare('SELECT id FROM players WHERE display_name=? COLLATE NOCASE').get(value) as { id: number } | undefined;
      return { sql: 'r.player_id=?', param: row?.id ?? -1 };
    }
    case 'character': return { sql: 'r.character=?', param: value };
    case 'kart': return { sql: 'r.kart=?', param: value };
    case 'costume': return { sql: 'r.costume=?', param: value };
    case 'cc': return { sql: 'r.cc=?', param: Number(value) };
    default: throw new Error(`race metrics cannot filter by ${dim}`);
  }
}

export function resolveRace(db: DatabaseSync, q: RaceQuery): StatResult {
  const m = getMetric(q.metric);
  if (!m || m.kind !== 'race') throw new Error(`not a race metric: ${q.metric}`);
  const rm = m as RaceMetric;

  const joins: string[] = [];
  if (rm.joins.includes('laps')) joins.push(LAPS_JOIN);
  if (rm.joins.includes('points')) joins.push(POINTS_JOIN);

  const where: string[] = ['r.season_id=?'];
  const params: unknown[] = [q.seasonId];
  if (q.cc != null) { where.push('r.cc=?'); params.push(q.cc); }
  if (rm.statuses !== 'all') { where.push(`r.status IN (${rm.statuses.map(() => '?').join(',')})`); params.push(...rm.statuses); }
  if (rm.pbOnly) where.push('r.was_pb=1');
  if (q.period.startUtc) { where.push('datetime(r.ended_at) >= ?'); params.push(q.period.startUtc); }
  if (q.period.endUtc) { where.push('datetime(r.ended_at) < ?'); params.push(q.period.endUtc); }

  for (const [dim, val] of Object.entries(q.filters) as [Dimension, string][]) {
    if (dim === q.groupBy) continue; // grouping dimension isn't also an equality filter
    const fc = filterClause(db, dim, val);
    where.push(fc.sql); params.push(fc.param);
  }
  if (q.bodyConditionSql) { if (q.bodyConditionSql.join) joins.push(q.bodyConditionSql.join); where.push(q.bodyConditionSql.where); params.push(...q.bodyConditionSql.params); }

  let rows: StatRow[];
  let total: number | null;
  if (q.groupBy) {
    const g = groupExpr(q.groupBy);
    if (g.join) joins.push(g.join);
    const sql = `SELECT ${g.select} AS key, ${rm.value} AS value
                 FROM runs r ${joins.join(' ')} WHERE ${where.join(' AND ')}
                 GROUP BY key HAVING key IS NOT NULL ORDER BY value DESC`;
    rows = db.prepare(sql).all(...params) as StatRow[];
    total = rows.reduce((s, r) => s + (r.value ?? 0), 0);
  } else {
    const sql = `SELECT ${rm.value} AS value FROM runs r ${joins.join(' ')} WHERE ${where.join(' AND ')}`;
    const row = db.prepare(sql).get(...params) as { value: number | null } | undefined;
    total = row?.value ?? 0;
    rows = [{ key: q.metric, value: total }];
  }

  return {
    metric: q.metric,
    period: { key: q.period.key, tz: q.period.tz, start: q.period.startIso, end: q.period.endIso },
    filters: q.filters as Record<string, string>,
    group_by: q.groupBy,
    rows, total,
  };
}
